"""Sign in with Google. The browser gets a Google ID token; the server checks it, checks the person is
allowed, and hands back a signed cookie. Users are identified by Google's `sub`, never by email."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from . import config


def _secret():
    if os.environ.get("SESSION_SECRET"):
        return os.environ["SESSION_SECRET"].encode()
    f = config.DATA / "secret.key"
    if not f.exists():
        config.DATA.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip().encode()


def _sign(payload):
    return base64.urlsafe_b64encode(hmac.new(_secret(), payload, hashlib.sha256).digest()).decode()


def make_session(sub, email):
    body = base64.urlsafe_b64encode(json.dumps(
        {"sub": sub, "email": email, "exp": time.time() + config.SESSION_HOURS * 3600}).encode())
    return body.decode() + "." + _sign(body)


def read_session(token):
    try:
        body, sig = (token or "").split(".")
        if not hmac.compare_digest(sig, _sign(body.encode())):
            return None
        data = json.loads(base64.urlsafe_b64decode(body))
        return data if data["exp"] > time.time() else None
    except Exception:
        return None


def allowed(email):
    email = email.lower()
    return "*" in config.ALLOWED_EMAILS or email in config.ALLOWED_EMAILS


def verify_google(credential):
    """Returns (sub, email) or raises PermissionError / ValueError."""
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token
    info = id_token.verify_oauth2_token(credential, grequests.Request(), config.GOOGLE_CLIENT_ID)
    email = (info.get("email") or "").lower()
    if not info.get("email_verified") or not email:
        raise PermissionError("Google account has no verified email")
    if not allowed(email):
        raise PermissionError(f"{email} is not on the allowed list")
    return info["sub"], email
