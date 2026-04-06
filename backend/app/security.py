import base64
import hashlib
import hmac
import os
import secrets
from typing import Tuple


PBKDF2_SCHEME = "pbkdf2_sha256"
PBKDF2_ITERATIONS = max(120_000, min(int(os.getenv("ADMIN_PASSWORD_PBKDF2_ITERATIONS", "260000")), 1_000_000))
TOKEN_SCHEME = "sha256"


def _legacy_sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_password(raw: str) -> str:
    password = (raw or "").encode("utf-8")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password, salt.encode("utf-8"), PBKDF2_ITERATIONS)
    encoded = base64.b64encode(digest).decode("ascii")
    return f"{PBKDF2_SCHEME}${PBKDF2_ITERATIONS}${salt}${encoded}"


def verify_password(raw: str, stored_hash: str) -> Tuple[bool, bool]:
    """Returns (is_valid, needs_rehash)."""
    value = (stored_hash or "").strip()
    if not value:
        return False, False

    if value.startswith(f"{PBKDF2_SCHEME}$"):
        parts = value.split("$", 3)
        if len(parts) != 4:
            return False, False
        _, raw_iterations, salt, encoded = parts
        try:
            iterations = int(raw_iterations)
            expected = base64.b64decode(encoded.encode("ascii"))
        except Exception:
            return False, False
        computed = hashlib.pbkdf2_hmac("sha256", (raw or "").encode("utf-8"), salt.encode("utf-8"), iterations)
        ok = hmac.compare_digest(computed, expected)
        return ok, ok and iterations < PBKDF2_ITERATIONS

    # Legacy compatibility: unsalted SHA-256 from older releases.
    legacy_ok = hmac.compare_digest(_legacy_sha256(raw), value)
    return legacy_ok, legacy_ok


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    payload = (token or "").encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return f"{TOKEN_SCHEME}${base64.urlsafe_b64encode(digest).decode('ascii')}"
