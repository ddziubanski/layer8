# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two-factor sign-in with an authenticator app (TOTP, RFC 6238: SHA-1, 30-second steps, 6 digits),
for password sign-ins to the console. Secrets are encrypted at rest with a key in the secrets
folder (totp.key, mode 0600), never shown again after setup. A code works once: its time step is
remembered. Recovery codes are one-time, and stored only as hashes."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.configstore import write_secret_file

STEP = 30
DIGITS = 6
WINDOW = 1  # one step either side, for clocks a little apart
RECOVERY_CODES = 8


def _fernet() -> Fernet:
    path = settings.secrets_dir / "totp.key"
    if not path.is_file():
        write_secret_file(path, Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def seal(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def unseal(sealed: str) -> str | None:
    try:
        return _fernet().decrypt(sealed.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def uri(secret: str, account: str, issuer: str) -> str:
    """The otpauth:// link an authenticator app reads from a QR code."""
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def check(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """The time step the code belongs to, or None if it's wrong or already used."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            return step if last_step is None or step > last_step else None
    return None


def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def recovery_codes() -> tuple[list[str], list[str]]:
    """New one-time codes to show once, and their hashes to keep."""
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODES)]
    return codes, [_hash(c) for c in codes]


def use_recovery_code(hashes: list[str], code: str) -> list[str] | None:
    """The hashes left after using this code, or None if it isn't one of them."""
    h = _hash(code)
    for stored in hashes:
        if hmac.compare_digest(stored, h):
            return [x for x in hashes if x != stored]
    return None
