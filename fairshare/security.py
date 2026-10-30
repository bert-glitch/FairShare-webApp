"""CSRF protection, security headers, safe redirects and login throttling."""
import hmac
import secrets
import time
from urllib.parse import urlparse

from flask import abort, request, session

CSRF_KEY = "csrf_token"


def csrf_token():
    """Return this session's CSRF token (creating it if needed). Used in every form."""
    if CSRF_KEY not in session:
        session[CSRF_KEY] = secrets.token_urlsafe(32)
    return session[CSRF_KEY]


def check_csrf():
    """Reject any POST that does not carry the session's token."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
        expected = session.get(CSRF_KEY, "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, description="Your form expired or is invalid. Please go back, reload the page and try again.")


def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' https://cdn.jsdelivr.net; "
        "script-src 'self' https://cdn.jsdelivr.net; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'",
    )
    if request.endpoint not in ("static", None):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def safe_next_url(target):
    """Only allow redirects to a path on this site (stops open-redirect tricks)."""
    if not target:
        return None
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


class LoginThrottle:
    """Very small in-memory limiter: too many failed logins -> wait a few minutes."""

    def __init__(self):
        self._fails = {}

    def _recent(self, key, window):
        now = time.time()
        self._fails[key] = [t for t in self._fails.get(key, []) if now - t < window]
        return self._fails[key]

    def blocked(self, key, max_attempts, window):
        return len(self._recent(key, window)) >= max_attempts

    def record_failure(self, key, window):
        self._recent(key, window).append(time.time())

    def clear(self, key):
        self._fails.pop(key, None)

    def reset_all(self):
        self._fails.clear()


throttle = LoginThrottle()
