"""Read-only Herdr subscription; authoritative snapshots reconcile invalidations."""
import json
import os
from pathlib import Path
import socket
import threading
import time


EVENTS = ('pane.created', 'pane.updated', 'pane.agent_detected', 'pane.closed',
          'pane.exited', 'pane.moved', 'workspace.created', 'workspace.closed',
          'worktree.created', 'worktree.opened', 'worktree.removed')

# How long a failed recovery may gate terminal mutations. Recovery normally
# completes in seconds; past this bound the gateway proceeds on its own
# identity checks rather than refusing to act indefinitely.
RECOVERY_BOUND = 300.0


def socket_path():
    if os.environ.get('HERDR_SOCKET_PATH'):
        return os.environ['HERDR_SOCKET_PATH']
    root = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'herdr'
    session = os.environ.get('HERDR_SESSION', '')
    return str(root / 'sessions' / session / 'herdr.sock' if session and session != 'default' else root / 'herdr.sock')


class RuntimeEvents:
    def __init__(self, hub, command, path=None):
        self.hub, self.command = hub, command
        self.path = path or socket_path()
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.subscribed_panes = frozenset()
        self.subscription_started_at = 0
        self.recovering_since = 0
        self.stopped = threading.Event()
        self.dirty = threading.Event()
        self.generation = 0
        self.connection_generation = 0
        self.acknowledged = False
        self.ever_connected = False
        self.state = 'starting'
        self.reason = 'Opening runtime subscription.'
        self.checked_at = None
        self.stream = None
        self.threads = []

    def snapshot(self):
        with self.lock:
            return dict(state=self.state, reason=self.reason, checked_at=self.checked_at,
                        generation=self.connection_generation,
                        degraded=self.degraded_locked(),
                        recovering_for=round(self.recovering_for_locked(), 1))

    def recovering_for_locked(self):
        if self.state == 'live' or not self.recovering_since:
            return 0.0
        return max(0.0, time.monotonic() - self.recovering_since)

    def degraded_locked(self):
        """True when a failed recovery has stopped gating mutations."""
        return self.recovering_for_locked() > RECOVERY_BOUND

    def guard(self):
        """Block terminal mutations while agent inventory may be stale.

        Recovery is normally seconds. A permanently unreachable socket would
        otherwise wedge every launch and prompt forever, which is worse than
        proceeding: each command still verifies identity, receipts and exact
        commits before it acts, so a bounded wait degrades into a warning
        rather than an outage.
        """
        with self.lock:
            if not self.ever_connected or self.state == 'live':
                return
            if self.degraded_locked():
                return
            raise ValueError('Runtime state is being reconciled; wait for a fresh session snapshot.')

    def invalidate(self, reason, disconnected=False):
        with self.lock:
            self.generation += 1
            if self.state == 'live' or not self.recovering_since:
                self.recovering_since = time.monotonic()
            self.state = 'reconciling' if self.ever_connected else 'polling'
            self.reason = reason
            if disconnected:
                self.acknowledged = False
                self.connection_generation += 1
        self.dirty.set()
        self.hub.publish('runtime')

    def consume(self, message):
        if not isinstance(message, dict):
            raise ValueError('Invalid runtime event envelope.')
        if message.get('error'):
            raise ValueError('Runtime subscription requires resynchronization.')
        result = message.get('result') or {}
        if isinstance(result, dict) and result.get('type') == 'subscription_started':
            if message.get('id') != 'gateway-runtime':
                raise ValueError('Unexpected runtime subscription acknowledgement.')
            with self.lock:
                self.acknowledged = True
                self.ever_connected = True
                self.generation += 1
                self.state = 'reconciling'
                self.reason = 'Subscription started; verifying authoritative state.'
            self.dirty.set()
        else:
            # Never apply potentially delayed event payloads to a snapshot.
            with self.lock:
                if not self.acknowledged:
                    raise ValueError('Runtime event arrived before subscription acknowledgement.')
                self.generation += 1
            self.dirty.set()

    def subscribe(self, stream, panes):
        # Status subscriptions are pane-scoped in the upstream socket protocol.
        subscriptions = [dict(type=event) for event in EVENTS]
        subscriptions.extend(dict(type='pane.agent_status_changed', pane_id=pane)
                             for pane in sorted(panes))
        request = dict(id='gateway-runtime', method='events.subscribe',
                       params=dict(subscriptions=subscriptions))
        with self.send_lock:
            self.subscription_started_at = time.monotonic()
            stream.sendall((json.dumps(request) + '\n').encode())

    def reconcile(self):
        with self.lock:
            generation, connection = self.generation, self.connection_generation
            if not self.acknowledged:
                return False
        snapshot = self.command('api', 'snapshot', timeout=5)
        if isinstance(snapshot, dict) and isinstance(snapshot.get('snapshot'), dict):
            snapshot = snapshot['snapshot']
        # Snapshot schemas are additive, but all three inventories must be present.
        if not isinstance(snapshot, dict) or not all(isinstance(snapshot.get(k), list) for k in ('agents', 'panes', 'workspaces')):
            raise ValueError('Unsupported runtime snapshot; periodic reconciliation remains available.')
        with self.lock:
            if not self.acknowledged or connection != self.connection_generation or generation != self.generation:
                self.dirty.set()
                return False
            panes = frozenset(p['id'] for p in snapshot['panes']
                              if isinstance(p, dict) and isinstance(p.get('id'), str))
            stream = self.stream
            if stream and panes != self.subscribed_panes:
                self.subscribed_panes = panes
                self.acknowledged = False
                self.generation += 1
                self.state = 'reconciling'
                self.reason = 'Updating pane subscriptions; verifying authoritative state.'
        if stream and not self.acknowledged:
            # Reopen with the new subscription set; streaming connections need
            # not support replacing subscriptions with a second request.
            stream.shutdown(socket.SHUT_RDWR)
            return False
        with self.lock:
            if generation != self.generation or connection != self.connection_generation or not self.acknowledged:
                self.dirty.set()
                return False
            self.state, self.reason = 'live', 'Runtime subscription and snapshot are current.'
            self.checked_at = time.time()
            self.recovering_since = 0
        # Existing identity checks remain authoritative before every effect.
        self.hub.publish('runtime')
        return True

    def start(self):
        if os.name == 'nt' or os.environ.get('HERDR_RUNTIME_EVENTS') == '0':
            with self.lock:
                self.state, self.reason = 'polling', 'Runtime stream disabled or unsupported here; using periodic checks.'
            return
        for name, target in [('runtime-subscription', self.read), ('runtime-reconcile', self.refresh)]:
            thread = threading.Thread(target=target, name=name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def read(self):
        delay = 1
        while not self.stopped.is_set():
            stream = None
            try:
                stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                stream.settimeout(1)
                stream.connect(self.path)
                self.stream = stream
                with self.lock:
                    panes = self.subscribed_panes
                self.subscribe(stream, panes)
                buffer = b''
                while not self.stopped.is_set():
                    try:
                        data = stream.recv(65536)
                    except socket.timeout:
                        with self.lock:
                            acknowledged = self.acknowledged
                        if not acknowledged and time.monotonic() - self.subscription_started_at > 5:
                            raise ValueError('Runtime subscription was not acknowledged.')
                        continue
                    if not data:
                        raise OSError('Runtime subscription disconnected.')
                    buffer += data
                    if len(buffer) > 1024 * 1024:
                        raise ValueError('Runtime event exceeded the bounded frame size.')
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        self.consume(json.loads(line))
                    delay = 1
            except (OSError, ValueError, UnicodeError, TypeError):
                self.invalidate('Runtime stream unavailable; reconnecting and reconciling.', disconnected=True)
            finally:
                if stream:
                    stream.close()
                self.stream = None
            self.stopped.wait(delay)
            delay = min(delay * 2, 30)

    def refresh(self):
        while not self.stopped.is_set():
            if not self.dirty.wait(2):
                continue
            self.dirty.clear()
            if self.stopped.wait(.2):
                break
            try:
                self.reconcile()
            except (ValueError, OSError):
                self.invalidate('Runtime snapshot unavailable; waiting for a verified snapshot.')
                self.stopped.wait(2)

    def close(self):
        self.stopped.set()
        self.dirty.set()
        if self.stream:
            try:
                self.stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for thread in self.threads:
            thread.join(timeout=6)
