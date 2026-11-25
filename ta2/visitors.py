"""Keeping visitors apart when the app is online, and keeping any one of them from using it up.

On your own computer nothing changes: there is one person, no cookie and no limits. With QM_PUBLIC=1
every browser gets its own random session in a cookie, and the server uses it to decide whose
conversations, saved words, failures and uploaded shops a request may touch.

Guard is the outer wall around the web app: size limit, requests from other websites, cookie and
security headers. Limits counts questions and uploads. Slots says how many questions run at once.
"""
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

PUBLIC = os.environ.get("QM_PUBLIC") == "1"


def number(name, default):
    return int(os.environ.get(name, default))


SESSION_HOURS = number("QM_SESSION_HOURS", 24)     # a visitor's data is deleted after this long without use
SWEEP_SECONDS = number("QM_SWEEP_SECONDS", 600)     # how often old data is looked for
RUNS_AT_ONCE = number("QM_RUNS_AT_ONCE", 2)        # questions the model works on at the same time
WAIT_SECONDS = 90                                  # how long a question waits for its turn
WAITING_AT_ONCE = 8                                # questions that may wait; more are refused at once
QUESTION_LENGTH = 500
STORES_EACH = 3                                    # uploaded shops one visitor may keep
TERMS_EACH = 40                                    # saved words per store
DISK_MB = number("QM_DISK_MB", 200)                # all uploaded shops together

# name -> (per visitor in an hour, per internet address in an hour, everyone in a day).
# The address figure is higher because a whole college can share one address.
RATES = {"ask": (number("QM_ASKS_HOUR", 30), 80, number("QM_ASKS_DAY", 200)),
         "upload": (10, 40, 300),
         "word": (60, 300, 3000)}

# A smaller machine online, so smaller files there.
MAX_FILES, MAX_FILE_MB, MAX_ROWS, MAX_CELLS, MAX_UNZIPPED_MB = (4, 5, 50_000, 1_500_000, 60) if PUBLIC else (8, 10, 200_000, 8_000_000, 120)
IMPORT_SECONDS, IMPORT_MB = number("QM_IMPORT_SECONDS", 60), number("QM_IMPORT_MB", 400)   # for reading one upload, see read_apart.py
MAX_JSON = 64 * 1024
MAX_UPLOAD = (MAX_FILES * MAX_FILE_MB + 1) * 1024 * 1024

COOKIE = "querymend"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
HOSTS = {h for h in os.environ.get("QM_HOSTS", "").lower().split(",") if h}   # the public address, when there is one
PROXIES = number("QM_PROXIES", 0)                  # trusted proxies in front of the server

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
}


def scoped(session, user_id):
    """The name under which a visitor's saved words and teams are kept. On your own computer it is just the store."""
    return f"{session}_{user_id}" if session else user_id


# ------------------------------------------------------------------ the wall around the app

class TooBig(Exception):
    pass


def host_name(value):
    try:
        return (urlsplit(f"//{value}").hostname or "").lower()
    except ValueError:
        return ""


class Guard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # the page and the answers about it carry the session. Pictures and scripts do not, because
        # a network in between may keep a copy of those and hand it to someone else.
        personal = not scope["path"].startswith("/assets/") and "." not in scope["path"].rsplit("/", 1)[-1]
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        problem = self.refuse(scope, headers)
        if problem:
            return await JSONResponse({"detail": problem[1]}, status_code=problem[0], headers=HEADERS)(scope, receive, send)

        session = self.session(headers) if PUBLIC else ""
        scope.setdefault("state", {}).update(session=session, address=address(scope, headers))
        started, received = False, 0
        limit = self.limit(scope)

        async def counted():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise TooBig
            return message

        async def finished(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                sent = list(message.get("headers", []))
                extra = [(k.lower().encode(), v.encode()) for k, v in HEADERS.items()]
                if personal and PUBLIC:
                    secure = "; Secure" if headers.get("x-forwarded-proto", scope.get("scheme")) == "https" else ""
                    cookie = f"{COOKIE}={session}; Path=/; Max-Age={SESSION_HOURS * 3600}; HttpOnly; SameSite=Lax{secure}"
                    sent = [h for h in sent if h[0] != b"cache-control"]
                    extra += [(b"set-cookie", cookie.encode()), (b"cache-control", b"private, no-cache")]
                message = {**message, "headers": sent + extra}
            await send(message)

        try:
            await self.app(scope, counted, finished)
        except TooBig:
            if not started:
                await JSONResponse({"detail": "That is too large."}, status_code=413, headers=HEADERS)(scope, receive, send)

    def limit(self, scope):
        return MAX_UPLOAD if scope["path"] == "/api/shops/preview" else MAX_JSON

    def refuse(self, scope, headers):
        """(status, reason) when a request must not reach the app."""
        host = host_name(headers.get("host", ""))
        allowed = HOSTS if PUBLIC else LOCAL_HOSTS | HOSTS
        if allowed and host not in allowed:
            return 400, "Unknown address."
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            # only our own page may ask for changes, not another website open in the same browser
            origin = headers.get("origin")
            if origin:
                other = host_name(origin.split("://", 1)[-1])
                if other != host and not (not PUBLIC and other in LOCAL_HOSTS):
                    return 403, "This request came from another website."
            elif headers.get("sec-fetch-site") == "cross-site":
                return 403, "This request came from another website."
        length = headers.get("content-length", "0")
        if not length.isdigit() or int(length) > self.limit(scope):
            return 413, "That is too large."
        return None

    def session(self, headers):
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except CookieError:
            pass
        value = cookie[COOKIE].value if COOKIE in cookie else ""
        if re.fullmatch(r"[0-9a-f]{32}", value):
            return value
        return secrets.token_hex(16)


def address(scope, headers):
    """The visitor's internet address. Behind proxies it is the entry the last trusted one added, which cannot be made up."""
    chain = [p.strip() for p in headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if PROXIES and len(chain) >= PROXIES:
        return chain[-PROXIES]
    return (scope.get("client") or ("unknown",))[0]


# ------------------------------------------------------------------ limits

class Limits:
    """Counts what each visitor, each address and everyone together did lately. Kept in memory."""

    def __init__(self, rates=RATES, now=time.time):
        self.rates, self.now = rates, now
        self.seen = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, kind, session, address):
        """None when allowed, otherwise what to tell the visitor."""
        each, per_address, everyone = self.rates[kind]
        with self.lock:
            now = self.now()
            if len(self.seen) > 20_000:
                self.forget_old(now)
            checks = [((kind, "visitor", session), each, 3600, "You have reached the limit for this hour. Please try again later."),
                      ((kind, "address", address), per_address, 3600, "Too many requests from your network this hour. Please try again later."),
                      ((kind, "all"), everyone, 86400, "The online demo has reached its limit for today. Please try again tomorrow.")]
            for key, most, seconds, message in checks:
                times = self.seen[key]
                while times and times[0] < now - seconds:
                    times.popleft()
                if len(times) >= most:
                    return message
            for key, *_ in checks:
                self.seen[key].append(now)
        return None

    def forget_old(self, now):
        for key in [k for k, v in self.seen.items() if not v or v[-1] < now - 86400]:
            del self.seen[key]


class Slots:
    """One question at a time per visitor, and a few at a time for everyone."""

    def __init__(self, at_once=RUNS_AT_ONCE):
        self.places = threading.BoundedSemaphore(at_once)
        self.queue = threading.BoundedSemaphore(WAITING_AT_ONCE)
        self.busy, self.lock = set(), threading.Lock()

    def enter(self, session):
        with self.lock:
            if session in self.busy:
                return False
            self.busy.add(session)
            return True

    def leave(self, session):
        with self.lock:
            self.busy.discard(session)

    def wait(self):
        """Waits for a place. False when too many are already waiting, or it took too long."""
        if not self.queue.acquire(blocking=False):
            return False
        try:
            return self.places.acquire(timeout=WAIT_SECONDS)
        finally:
            self.queue.release()
