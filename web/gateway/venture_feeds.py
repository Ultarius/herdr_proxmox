"""Bounded, SSRF-safe news feeds for venture simulation.

Only https sources are accepted. Every hop is resolved and refused when any
address is loopback, private, link-local, reserved or otherwise non-global,
and the connection is then made to that checked address (no second lookup a
rebinding DNS server could answer differently). Environment proxies are not
used: a proxy would do its own lookup, outside these checks.
Parsing is stdlib-only and rejects entity declarations before parsing.
"""
from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
import html
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request
import xml.etree.ElementTree as ElementTree

FETCH_LIMIT = 1_000_000
FETCH_TIMEOUT = 10
# ``FETCH_TIMEOUT`` is per socket operation, so a source that trickles bytes
# would reset it forever. This bounds one whole request, redirects included.
FETCH_DEADLINE = 30
MAX_REDIRECTS = 3
MAX_ITEMS = 20
EXCERPT_LIMIT = 300
TITLE_LIMIT = 500
ITEM_AGE_DAYS = 90
# An item first seen after the previous tick is still news when it was
# published up to this long before it (feeds lag, fetches are throttled).
LATE_ITEM_GRACE = timedelta(days=1)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Deadline:
    """Shuts a request's socket down once the whole request used its time."""

    def __init__(self, seconds):
        self.at = time.monotonic() + seconds
        self.expired = False
        self._sockets = []
        self._lock = threading.Lock()
        self._timer = threading.Timer(max(0, seconds), self._expire)
        self._timer.daemon = True
        self._timer.start()

    def remaining(self):
        return max(0.001, self.at - time.monotonic())

    def spent(self):
        # By the clock: the timer that sets ``expired`` can run a little late.
        return self.expired or time.monotonic() >= self.at

    def attach(self, sock):
        with self._lock:
            self._sockets.append(sock)
            expired = self.expired
        if expired:
            self._shut(sock)

    @staticmethod
    def _shut(sock):
        try:
            # A source still sending bytes fails on its next read, also where
            # a shutdown does not wake a read already in progress.
            sock.settimeout(0.001)
            # The plain socket call: TLS's own shutdown is not safe while
            # another thread is blocked reading.
            socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except OSError:
            pass

    def _expire(self):
        with self._lock:
            self.expired = True
            sockets = list(self._sockets)
        for sock in sockets:
            self._shut(sock)

    def cancel(self):
        self._timer.cancel()


class _PinnedConnection(http.client.HTTPSConnection):
    """TLS to an address that passed the checks; the host name is still verified.

    Every checked address is tried in resolver order, as
    ``socket.create_connection`` does for a name. Resolvers often list IPv6
    first, and a host without an IPv6 route (common in containers) must still
    reach the IPv4 address.
    """

    def __init__(self, host, *, addresses, context, deadline=None, **options):
        super().__init__(host, context=context, **options)
        self._addresses, self._tls, self._deadline = tuple(addresses), context, deadline

    def connect(self):
        failure = None
        for address in self._addresses:
            timeout = self.timeout
            if self._deadline is not None:
                if self._deadline.spent():
                    raise failure or OSError('Feed request used its time before connecting.')
                timeout = min(timeout, self._deadline.remaining())
            try:
                sock = socket.create_connection((address, self.port), timeout)
            except OSError as error:
                failure = error
                continue
            try:
                if self._deadline is None:
                    self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)
                else:
                    # Watched before the handshake, which can trickle too.
                    self.sock = self._tls.wrap_socket(sock, server_hostname=self.host,
                                                      do_handshake_on_connect=False)
                    self._deadline.attach(self.sock)
                    self.sock.do_handshake()
            except BaseException:
                sock.close()
                raise
            return
        raise failure or OSError('Feed source has no checked address to connect to.')


class _PinnedHTTPS(HTTPSHandler):
    def __init__(self, addresses, deadline=None):
        self._tls = ssl.create_default_context()
        super().__init__(context=self._tls)
        self._addresses, self._deadline = tuple(addresses), deadline

    def https_open(self, req):
        return self.do_open(partial(_PinnedConnection, addresses=self._addresses, context=self._tls,
                                    deadline=self._deadline), req)


def now():
    return datetime.now(timezone.utc)


def resolve_addresses(host, port):
    """Every resolved address must be globally routable."""
    try:
        address = ipaddress.ip_address(host)
        addresses = [address]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(item[4][0])
                         for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
        except (OSError, ValueError) as error:
            raise ValueError('Feed source could not be resolved.') from error
    if not addresses or any(not item.is_global for item in addresses):
        raise ValueError('Feed source must resolve to a public address only.')
    return addresses


def validate_url(value, resolve=resolve_addresses, pinned=None):
    """Normalise one feed URL. ``pinned`` (a list) receives the checked addresses."""
    if not isinstance(value, str) or not value or len(value) > 2000 or '\0' in value:
        raise ValueError('Feed source must be a bounded URL.')
    parts = urlsplit(value)
    if parts.scheme.lower() != 'https' or not parts.hostname:
        raise ValueError('Feed source must use https.')
    if parts.username or parts.password:
        raise ValueError('Feed source must not contain credentials.')
    host = parts.hostname.lower().rstrip('.')
    if not host or any(char in host for char in ('/', '\\', ' ')):
        raise ValueError('Feed source has an invalid host.')
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError('Feed source has an invalid port.') from error
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Feed source has an invalid port.')
    if resolve is not None:
        addresses = resolve(host, port or 443)
        if pinned is not None:
            pinned[:] = [str(address) for address in addresses or []]
    netloc = '[' + host + ']' if ':' in host else host
    if port is not None and port != 443:
        netloc += ':' + str(port)
    return urlunsplit(('https', netloc, parts.path or '/', parts.query, ''))


class Response:
    def __init__(self, status, headers, body, url):
        self.status = status
        self.headers = {key.lower(): value for key, value in headers.items()}
        self.body = body
        self.url = url


def _within(budget, resolve, message):
    """``resolve`` bounded by the request's deadline. A system lookup cannot be
    cancelled, so it finishes on a daemon thread while the request moves on."""
    if resolve is None:
        return None

    def bounded(host, port):
        outcome = {}

        def work():
            try:
                outcome['addresses'] = resolve(host, port)
            except Exception as error:
                outcome['error'] = error
        lookup = threading.Thread(target=work, name='feed-dns', daemon=True)
        lookup.start()
        lookup.join(budget.remaining())
        if lookup.is_alive():
            raise ValueError(message)
        if 'error' in outcome:
            raise outcome['error']
        return outcome['addresses']
    return bounded


def request(url, headers, *, resolve=resolve_addresses, timeout=FETCH_TIMEOUT, limit=FETCH_LIMIT,
            deadline=FETCH_DEADLINE):
    """Fetch one URL with manual redirects so every hop is revalidated and pinned.

    ``deadline`` bounds the whole request in seconds: name lookups, every
    connection attempt, redirects and the response."""
    pinned = []
    budget = _Deadline(deadline)
    late = 'Feed request exceeded its time limit.'
    resolve = _within(budget, resolve, late)
    try:
        current = validate_url(url, resolve, pinned)
        for _ in range(MAX_REDIRECTS + 1):
            if budget.spent():
                raise ValueError(late)
            handlers = [_NoRedirect(), ProxyHandler({})] + ([_PinnedHTTPS(pinned, budget)] if pinned else [])
            opener = build_opener(*handlers)
            try:
                with opener.open(Request(current, headers=headers),
                                 timeout=min(timeout, budget.remaining())) as response:
                    body = response.read(limit + 1)
                    if budget.expired:  # A shut socket can read as a short, clean body.
                        raise ValueError(late)
                    if len(body) > limit:
                        raise ValueError('Feed response exceeds its byte budget.')
                    return Response(response.status, dict(response.headers), body, current)
            except HTTPError as error:
                if error.code == 304:
                    return Response(304, dict(error.headers), b'', current)
                if error.code in (301, 302, 303, 307, 308):
                    location = error.headers.get('Location')
                    if not location:
                        raise ValueError('Feed redirect did not provide a destination.') from error
                    current = validate_url(urljoin(current, location), resolve, pinned)
                    continue
                raise ValueError('Feed request failed with HTTP ' + str(error.code) + '.') from error
            except (URLError, OSError, TimeoutError, http.client.HTTPException) as error:
                if budget.spent():
                    raise ValueError(late) from error
                raise ValueError('Feed request failed before a response was received.') from error
        raise ValueError('Feed exceeded its redirect limit.')
    finally:
        budget.cancel()


def _local(tag):
    return str(tag).rsplit('}', 1)[-1].lower()


def _child(element, *names):
    for child in element:
        if _local(child.tag) in names:
            return child
    return None


def _clean(value, limit):
    value = html.unescape(re.sub(r'<[^>]+>', ' ', str(value or '')))
    value = re.sub(r'\s+', ' ', value).strip()
    return ''.join(char for char in value if ord(char) >= 32)[:limit]


def _text(element, *names):
    child = _child(element, *names)
    if child is None:
        return ''
    return _clean(' '.join(child.itertext()), 4000)


def _published(element, fallback):
    for name in ('published', 'pubdate', 'updated', 'date'):
        child = _child(element, name)
        if child is None:
            continue
        value = _clean(child.text or '', 80)
        if not value:
            continue
        try:
            if value.endswith('Z') or re.search(r'\d{4}-\d{2}-\d{2}T', value):
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            else:
                from email.utils import parsedate_to_datetime
                parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        # Normalise to UTC: stored timestamps are compared and pruned as
        # ISO strings, which is only ordered within one offset (a +09:00
        # Japanese feed would otherwise sort nine hours late).
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return fallback.astimezone(timezone.utc)


def _link(element):
    for child in element:
        if _local(child.tag) != 'link':
            continue
        href = child.get('href')
        if href and child.get('rel', 'alternate') not in ('enclosure', 'self'):
            return href.strip()
        if child.text and child.text.strip():
            return child.text.strip()
    guid = _child(element, 'guid', 'id')
    return _clean(guid.text, 2000) if guid is not None and guid.text and guid.text.strip().startswith(('http://', 'https://')) else ''


def canonical_link(value):
    parts = urlsplit(value)
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        return ''
    host = parts.hostname.lower().rstrip('.')
    netloc = '[' + host + ']' if ':' in host else host
    if parts.port and parts.port not in (80, 443):
        netloc += ':' + str(parts.port)
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or '/', parts.query, ''))


def _refuse_declarations(body, label='Feed'):
    """Refuse a DTD or entity declaration in any encoding the parser accepts.

    The byte check in ``parse`` sees ASCII-compatible text only: a UTF-16
    document spells ``<!DOCTYPE`` with zero bytes between the letters. Expat
    detects the encoding first, so its declaration handlers see every one."""
    from xml.parsers import expat

    def refuse(*_):
        raise ValueError(label + ' rejected a document with entity declarations.')

    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    try:
        parser.Parse(body, True)
    except expat.ExpatError as error:
        raise ValueError(label + ' is not valid XML.') from error


def parse(body, source, fetched_at):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise ValueError('Feed rejected a document with entity declarations.')
    _refuse_declarations(body)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise ValueError('Feed is not valid XML.') from error
    items = []
    for element in root.iter():
        if _local(element.tag) not in ('item', 'entry'):
            continue
        link = canonical_link(_link(element))
        if not link:
            continue
        title = _text(element, 'title')[:TITLE_LIMIT] or 'Untitled'
        excerpt = _text(element, 'description', 'summary', 'content', 'encoded')[:EXCERPT_LIMIT]
        tags = []
        for child in element:
            if _local(child.tag) not in ('category', 'subject'):
                continue
            value = _clean(child.get('term') or child.text or '', 40)
            if value and value.lower() not in {tag.lower() for tag in tags}:
                tags.append(value)
        published = _published(element, fetched_at)
        item_id = hashlib.sha256(link.encode()).hexdigest()
        items.append(dict(id=item_id, source=source, title=title, link=link,
                          published=published.astimezone(timezone.utc).isoformat(), tags=tags[:10],
                          excerpt=excerpt, fetched_at=fetched_at.isoformat()))
    return items


def fetch(source, *, resolve=resolve_addresses, etag='', modified='', request_fn=request, deadline=None):
    headers = {'User-Agent': 'Herdr venture simulation', 'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml'}
    if etag:
        headers['If-None-Match'] = etag
    if modified:
        headers['If-Modified-Since'] = modified
    fetched_at = now()
    limits = {} if deadline is None else dict(deadline=deadline)
    response = request_fn(source, headers, resolve=resolve, **limits)
    if response.status == 304:
        return dict(status=304, source=source, fetched_at=fetched_at.isoformat(), items=[],
                    etag=response.headers.get('etag', etag), modified=response.headers.get('last-modified', modified))
    items = parse(response.body, source, fetched_at)
    return dict(status=response.status, source=source, fetched_at=fetched_at.isoformat(), items=items,
                etag=response.headers.get('etag', ''), modified=response.headers.get('last-modified', ''))


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def has_keyword(text, word):
    """A whole-word match, plural included: "ai" is not in "said", "invoice" is in "invoices"."""
    return re.search(r'(?<!\w)' + re.escape(word) + r'(?:s|es)?(?!\w)', text) is not None


def filter_items(items, sources, since):
    """Recent items that pass the tag or keyword watchlists, newest first.

    ``since`` (the previous tick) only bounds age: the caller decides what is
    new by what it has not offered before, so an item published just before
    the previous tick but stored after it still counts, within
    ``LATE_ITEM_GRACE``. Tags and keywords are a union: a tagged section or a
    trigger word is enough, so a feed without category tags is still covered
    by the keyword watchlist. No watchlists keeps everything.
    """
    tags = {str(tag).strip().lower() for tag in sources.get('tags', []) if str(tag).strip()}
    keywords = [str(word).strip().lower() for word in sources.get('keywords', []) if str(word).strip()]
    boundary = _instant(since) if since else None
    kept = []
    for item in items:
        published = _instant(item.get('published') or item.get('fetched_at'))
        if boundary and published and published <= boundary - LATE_ITEM_GRACE:
            continue
        if tags or keywords:
            text = (item.get('title', '') + ' ' + item.get('excerpt', '')).lower()
            if not ({str(tag).lower() for tag in item.get('tags', [])} & tags
                    or any(has_keyword(text, word) for word in keywords)):
                continue
        kept.append(item)
    kept.sort(key=lambda item: _instant(item.get('published')) or datetime.min.replace(tzinfo=timezone.utc),
              reverse=True)
    return kept[:MAX_ITEMS]
