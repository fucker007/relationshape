"""API key and password hashing helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets


KEY_PREFIX = "rsk_live"


def generate_api_key(prefix: str = KEY_PREFIX) -> str:
    token = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    return f"{prefix}_{token}"


def key_preview(api_key: str) -> str:
    if len(api_key) <= 14:
        return api_key
    return f"{api_key[:10]}...{api_key[-4:]}"


def hash_api_key(api_key: str, pepper: str = "") -> str:
    payload = api_key.encode("utf-8")
    if pepper:
        digest = hmac.new(pepper.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    else:
        digest = hashlib.sha256(payload).hexdigest()
    return digest


def password_hash(password: str, *, iterations: int = 260_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = base64.b64decode(raw_salt.encode("ascii"))
        expected = base64.b64decode(raw_digest.encode("ascii"))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def default_pepper() -> str:
    return os.environ.get("RELATIONSHAPE_KEY_PEPPER", "")
