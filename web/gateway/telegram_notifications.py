"""Opt-in owner alerts. Credentials and delivery receipts stay outside exports."""
import json
from http.client import HTTPException
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from repository_connections import write_private

MAX_RECEIPTS = 10000

SCAN_ERROR = 'Could not check Herdr notifications. The gateway will retry automatically.'


class TelegramError(ValueError):
    def __init__(self, message, retry_after=60):
        super().__init__(message)
        self.retry_after = retry_after


def send_message(token, chat_id, text):
    """Use plain text and fixed HTTPS origin; never surface upstream URLs/errors."""
    request = Request('https://api.telegram.org/bot' + token + '/sendMessage',
                      data=json.dumps(dict(chat_id=chat_id, text=text[:4000],
                                           link_preview_options=dict(is_disabled=True))).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read(65536))
    except HTTPError as error:
        try:
            payload = json.loads(error.read(65536))
        except (ValueError, OSError, HTTPException):
            payload = {}
        finally:
            error.close()
        code = error.code
    except (URLError, OSError, ValueError, HTTPException):
        raise TelegramError('Could not reach Telegram. Check outbound HTTPS connectivity and retry.') from None
    else:
        if isinstance(payload, dict) and payload.get('ok') is True:
            return
        code = payload.get('error_code') if isinstance(payload, dict) else None
    if code in (401, 404):
        raise TelegramError('Telegram rejected the bot token. Check the token from @BotFather.') from None
    if code in (400, 403):
        raise TelegramError('Telegram could not message this chat. Send /start to your bot and check your chat ID.') from None
    if code == 429:
        parameters = payload.get('parameters', {}) if isinstance(payload, dict) else {}
        delay = parameters.get('retry_after', 60) if isinstance(parameters, dict) else 60
        delay = min(max(delay, 1), 86400) if type(delay) is int else 60
        raise TelegramError('Telegram rate limit reached. Delivery will retry automatically.', delay) from None
    raise TelegramError('Telegram did not accept the message. Delivery will retry automatically.') from None


class TelegramNotifications:
    def __init__(self, path, collect, *, send=send_message, wake=None):
        self.path = Path(path)
        self.collect, self.send = collect, send
        self.lock = threading.RLock()
        self.wake = wake or threading.Event()
        self.stopped = threading.Event()
        self.retry_at = 0
        self.thread = threading.Thread(target=self.run, daemon=True, name='telegram-notifications')

    def load(self):
        if not self.path.exists():
            return dict(enabled=False, token='', chat_id='', delivered=[], last_error='', last_sent_at=0)
        if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > 2_000_000:
            raise ValueError('Invalid Telegram settings file.')
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or type(data.get('enabled')) is not bool:
                raise ValueError()
            if not isinstance(data.get('token'), str) or not isinstance(data.get('chat_id'), str):
                raise ValueError()
            if (not isinstance(data.get('delivered'), list) or
                    not all(isinstance(key, str) for key in data['delivered'])):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ValueError('Invalid Telegram settings file.') from None
        return data

    def save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_private(self.path, json.dumps(data))

    def snapshot(self):
        with self.lock:
            data = self.load()
            return dict(enabled=data['enabled'], token_configured=bool(data['token']),
                        chat_id=data['chat_id'], last_error=data.get('last_error', ''),
                        last_sent_at=data.get('last_sent_at', 0))

    def configure(self, body):
        if not isinstance(body, dict) or set(body) - {'token', 'chat_id', 'enabled'}:
            raise ValueError('Expected Telegram token, chat ID and enabled settings.')
        if type(body.get('enabled')) is not bool:
            raise ValueError('Choose whether Telegram notifications are enabled.')
        token, chat_id = body.get('token', ''), body.get('chat_id', '')
        if not isinstance(token, str) or not isinstance(chat_id, str):
            raise ValueError('Enter a bot token and numeric private chat ID.')
        token, chat_id = token.strip(), chat_id.strip()
        if token and not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,200}', token):
            raise ValueError('Enter the bot token provided by @BotFather.')
        if chat_id and not re.fullmatch(r'[1-9][0-9]{0,18}', chat_id):
            raise ValueError('Enter your positive numeric private Telegram chat ID.')
        with self.lock:
            data = self.load()
            token = token or data['token']
            if body['enabled'] and (not token or not chat_id):
                raise ValueError('A bot token and private chat ID are required to enable Telegram.')
            changed = (token, chat_id) != (data['token'], data['chat_id'])
            data.update(enabled=body['enabled'], token=token, chat_id=chat_id, last_error='')
            if changed:
                data.update(delivered=[], last_sent_at=0)
            self.save(data)
            self.retry_at = 0
            self.wake.set()
            return self.snapshot()

    def disconnect(self):
        with self.lock:
            self.save(dict(enabled=False, token='', chat_id='', delivered=[], last_error='', last_sent_at=0))
            self.retry_at = 0
            self.wake.set()
            return self.snapshot()

    def deliver(self, data, text):
        try:
            self.send(data['token'], data['chat_id'], text)
        except TelegramError as error:
            data['last_error'] = str(error)
            self.retry_at = time.time() + error.retry_after
            self.save(data)
            raise
        data.update(last_error='', last_sent_at=time.time())

    def test(self):
        with self.lock:
            data = self.load()
            if not data['token'] or not data['chat_id']:
                raise ValueError('Save a bot token and chat ID before sending a test.')
            if time.time() < self.retry_at:
                raise TelegramError('Telegram delivery is waiting before retrying. Please try again shortly.')
            self.deliver(data, 'Herdr: Telegram notifications are connected. Open Herdr to review blockers and approvals.')
            self.save(data)
            return self.snapshot()

    def reconcile(self):
        with self.lock:
            data = self.load()
            if (self.stopped.is_set() or not data['enabled'] or time.time() < self.retry_at or
                    time.time() - data.get('last_sent_at', 0) < 1):
                return
        # Authoritative reads can be slow; configuration stays usable meanwhile.
        alerts = self.collect()
        with self.lock:
            data = self.load()
            if (self.stopped.is_set() or not data['enabled'] or time.time() < self.retry_at or
                    time.time() - data.get('last_sent_at', 0) < 1):
                return
            current = {alert['key'] for alert in alerts}
            preserve = getattr(alerts, 'preserve_prefixes', ())
            retained = [key for key in data['delivered']
                        if key in current or any(key.startswith(prefix) for prefix in preserve)]
            delivered = set(retained)
            data['delivered'] = retained[-MAX_RECEIPTS:]
            if data.get('last_error') == SCAN_ERROR:
                data['last_error'] = ''
            self.save(data)
            # A small digest bounds bursts without making a backlog wait for
            # hundreds of individual sends. Record only acknowledged entries.
            parts, sent = [], set()
            for alert in alerts:
                if alert['key'] in delivered:
                    continue
                part = (alert['title'] + '\n' + alert['message'])[:700]
                if parts and (sum(map(len, parts)) + len(part) > 3700 or len(parts) >= 10):
                    break
                parts.append(part)
                sent.add(alert['key'])
            if parts:
                self.deliver(data, 'Herdr: Attention needed\n\n' + '\n\n'.join(parts) +
                             '\n\nOpen Herdr to review and respond.')
                data['delivered'] = (data['delivered'] + sorted(sent))[-MAX_RECEIPTS:]
                self.save(data)

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            self.wake.clear()
            try:
                self.reconcile()
            except TelegramError:
                pass  # Sanitized delivery error is already visible in settings.
            except Exception:
                # A runtime/store outage must never terminate the notification
                # worker or expose terminal output/credentials through an error.
                try:
                    with self.lock:
                        data = self.load()
                        data['last_error'] = SCAN_ERROR
                        self.save(data)
                except (OSError, ValueError):
                    pass
            self.wake.wait(15)

    def close(self):
        self.stopped.set()
        self.wake.set()
        if self.thread.is_alive():
            self.thread.join(timeout=10)
