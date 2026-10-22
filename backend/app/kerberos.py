"""Portal single sign-on as the logged-on Windows user: Kerberos (HTTP Negotiate / SPNEGO).

The browser sends "Authorization: Negotiate <token>" when the portal URL is trusted
for Windows sign-in (Local Intranet zone or AuthServerAllowlist). We accept it with
the keytab for the portal's SPN and learn who is logged on, e.g. jpark@CORP.LOCAL.
Only Kerberos is accepted: NTLM can't be checked here and is refused.

Admins set it up in Settings: upload the keytab (kept in HELPDESK_SECRETS_DIR, never
in the database or served back), then turn it on. The realm comes from the keytab.
"""

import base64
import binascii
import os
import struct
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AppSetting, User

# Kerberos 5 GSS-API mechanism.
KRB5_OID = "1.2.840.113554.1.2.2"
SETTING_KEY = "windows_sign_in"
MAX_KEYTAB_BYTES = 64 * 1024
ENCTYPES = {
    17: "aes128-cts-hmac-sha1-96",
    18: "aes256-cts-hmac-sha1-96",
    19: "aes128-cts-hmac-sha256-128",
    20: "aes256-cts-hmac-sha384-192",
    23: "rc4-hmac",
    3: "des-cbc-md5",
    1: "des-cbc-crc",
}
STRONG = {17, 18, 19, 20}


class KerberosError(Exception):
    pass


class KeytabError(ValueError):
    """An uploaded keytab can't be used; the message is safe to show."""


@dataclass
class WindowsSignIn:
    """Stored in app_settings. keytab_* describe the uploaded file (never the keys)."""

    enabled: bool = False
    # Try it on the sign-in page without a click. Needs the portal URL trusted for
    # Windows sign-in by GPO, or browsers may ask for a password.
    silent: bool = True
    realm: str = ""
    keytab_principals: list[str] = field(default_factory=list)
    keytab_enctypes: list[str] = field(default_factory=list)
    keytab_uploaded_at: str | None = None
    keytab_uploaded_by: str | None = None


def keytab_path() -> Path:
    return settings.secrets_dir / "http.keytab"


def load(session: Session) -> WindowsSignIn:
    row = session.get(AppSetting, SETTING_KEY)
    known = WindowsSignIn.__dataclass_fields__
    return WindowsSignIn(**{k: v for k, v in (row.value if row else {}).items() if k in known})


def save(session: Session, config: WindowsSignIn, by: User | None) -> None:
    row = session.get(AppSetting, SETTING_KEY)
    if row is None:
        row = AppSetting(key=SETTING_KEY)
        session.add(row)
    row.value = asdict(config)
    row.updated_at = datetime.now().astimezone()
    row.updated_by_id = by.id if by else None


def active(session: Session) -> WindowsSignIn | None:
    """The settings when Windows sign-in is on and has a keytab, else None."""
    config = load(session)
    if config.enabled and config.realm and keytab_path().is_file():
        return config
    return None


# Keytab files (MIT format 0x0502, as ktpass and ktutil write them)


@dataclass
class KeytabEntry:
    principal: str
    enctype: int
    kvno: int


def parse_keytab(data: bytes) -> list[KeytabEntry]:
    if len(data) < 2 or data[:2] != b"\x05\x02":
        raise KeytabError("That isn't a keytab file")
    entries, pos = [], 2
    try:
        while pos + 4 <= len(data):
            (size,) = struct.unpack_from(">i", data, pos)
            pos += 4
            if size <= 0:  # a hole left by a deleted entry
                pos += -size
                continue
            rec, pos = data[pos : pos + size], pos + size
            if len(rec) < size:
                raise KeytabError("The keytab file is cut short")
            r = 0

            def counted() -> bytes:
                nonlocal r
                (n,) = struct.unpack_from(">H", rec, r)
                r += 2 + n
                if r > len(rec):
                    raise struct.error
                return rec[r - n : r]

            (count,) = struct.unpack_from(">H", rec, r)
            r += 2
            realm = counted().decode()
            parts = [counted().decode() for _ in range(count)]
            _name_type, _timestamp, kvno = struct.unpack_from(">IIB", rec, r)
            r += 9
            (enctype,) = struct.unpack_from(">H", rec, r)
            r += 2
            counted()  # the key itself: not kept
            if len(rec) - r >= 4:
                (kvno32,) = struct.unpack_from(">I", rec, r)
                kvno = kvno32 or kvno
            entries.append(KeytabEntry(f"{'/'.join(parts)}@{realm}", enctype, kvno))
    except (struct.error, UnicodeDecodeError):
        raise KeytabError("The keytab file is damaged") from None
    if not entries:
        raise KeytabError("The keytab file has no keys")
    return entries


def install_keytab(session: Session, data: bytes, by: User | None) -> WindowsSignIn:
    """Check an uploaded keytab, store it where only this service can read it, and take the
    realm from it. KeytabError with a message for the admin if it won't work."""
    if len(data) > MAX_KEYTAB_BYTES:
        raise KeytabError("That file is too big to be a keytab")
    entries = parse_keytab(data)
    http = [e for e in entries if e.principal.startswith("HTTP/")]
    if not http:
        found = ", ".join(sorted({e.principal for e in entries}))
        raise KeytabError(f"The keytab has no HTTP/ service principal for the portal (it has {found})")
    realms = {e.principal.rsplit("@", 1)[1] for e in http}
    if len(realms) > 1:
        raise KeytabError(f"The keytab mixes realms ({', '.join(sorted(realms))}); export one for this domain only")
    if not any(e.enctype in STRONG for e in http):
        raise KeytabError(
            "The keytab only has RC4 or DES keys, which current Kerberos refuses. Set the service account to AES "
            "(msDS-SupportedEncryptionTypes) and export it again with -crypto AES256-SHA1"
        )
    path = keytab_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    problem = keytab_problem(tmp)
    if problem:
        tmp.unlink()
        raise KeytabError(f"Kerberos can't use this keytab: {problem}")
    os.replace(tmp, path)

    config = load(session)
    config.realm = realms.pop()
    config.keytab_principals = sorted({e.principal for e in http})
    config.keytab_enctypes = sorted({ENCTYPES.get(e.enctype, f"type {e.enctype}") for e in http})
    config.keytab_uploaded_at = datetime.now().astimezone().isoformat()
    config.keytab_uploaded_by = by.display_name if by else "installer"
    save(session, config, by)
    return config


def remove_keytab(session: Session, by: User) -> WindowsSignIn:
    """Delete the keytab; Windows sign-in turns off."""
    keytab_path().unlink(missing_ok=True)
    config = WindowsSignIn(silent=load(session).silent)
    save(session, config, by)
    return config


def keytab_problem(path: Path | None = None) -> str | None:
    """Why the keytab can't be used (missing, unreadable, empty), or None if it can."""
    import gssapi

    try:
        gssapi.Credentials(usage="accept", store={"keytab": str(path or keytab_path())})
    except gssapi.exceptions.GSSError as exc:
        # The minor message says what's wrong ("Keytab FILE:... is nonexistent or empty").
        return str(exc).rsplit("Minor", 1)[-1].split(":", 1)[-1].strip() or str(exc)
    return None


# Signing in


def negotiate_token(authorization: str | None) -> bytes | None:
    """The token from an "Authorization: Negotiate ..." header, or None if there isn't one."""
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "negotiate" or not value.strip():
        return None
    try:
        return base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise KerberosError("Malformed Negotiate token") from None


def accept(token: bytes) -> tuple[str, bytes | None]:
    """The client principal (name@REALM) proved by this token, and a reply token for the
    browser (mutual authentication), if any. Raises KerberosError otherwise."""
    import gssapi

    try:
        creds = gssapi.Credentials(usage="accept", store={"keytab": str(keytab_path())})
        ctx = gssapi.SecurityContext(creds=creds, usage="accept")
        reply = ctx.step(token)
        # Kerberos completes in one round trip; anything wanting more (NTLM) is refused.
        if not ctx.complete:
            raise KerberosError("Sign-in needs more than one round trip (not Kerberos)")
        if ctx.mech is None or ctx.mech.dotted_form != KRB5_OID:
            raise KerberosError("Only Kerberos is accepted")
        return str(ctx.initiator_name), reply
    except gssapi.exceptions.GSSError as exc:
        raise KerberosError(str(exc)) from None
