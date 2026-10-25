# SPDX-License-Identifier: AGPL-3.0-or-later
"""Trusting on-premises consoles (UniFi, Prism Central) that keep their factory certificate.

An admin pastes either the CA that issued the console's certificate, or the console's own
self-signed certificate. A CA gets the normal checks. A self-signed certificate that isn't a CA
(UniFi's, for one) would be refused by Python's strict checks and usually doesn't name the address
in use, so it is pinned instead: the handshake must present exactly that certificate, checked
before anything (an API key, a password) is sent. The address itself is then not checked.
"""
import ssl
from pathlib import Path

import httpx
from cryptography import x509


def _pinned(pem: bytes) -> bool:
    certs = x509.load_pem_x509_certificates(pem)
    if len(certs) != 1 or certs[0].issuer != certs[0].subject:
        return False
    try:
        return not certs[0].extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        return True


def verify(ca_file: str) -> ssl.SSLContext | bool:
    """What to pass as httpx's verify= for a console whose CA (or own certificate) is in ca_file."""
    if not ca_file:
        return True
    context = ssl.create_default_context(cafile=ca_file)
    if _pinned(Path(ca_file).read_bytes()):
        # The only trust anchor is the console's own key, so the name in it doesn't matter.
        context.check_hostname = False
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def reason(exc: httpx.HTTPError) -> str:
    """Why a connection failed, in words an admin can act on (never includes credentials)."""
    cause = exc.__cause__ or exc.__context__
    text = f"{exc} {cause or ''}"
    if isinstance(exc, httpx.TimeoutException):
        return "no answer in time: check the address, and that a firewall lets the appliance through"
    if "CERTIFICATE_VERIFY_FAILED" in text or isinstance(cause, ssl.SSLCertVerificationError):
        if "mismatch" in text or "not valid for" in text:
            return "its certificate doesn't name this address: use the name in the certificate, or paste the console's own certificate"
        return "its certificate isn't trusted: paste the CA certificate, or the console's own self-signed certificate, in the CA certificate setting"
    if isinstance(cause, ssl.SSLError) or "SSL" in text:
        return "the secure connection failed (TLS)"
    if "Name or service not known" in text or "nodename nor servname" in text or "getaddrinfo" in text or "Temporary failure in name resolution" in text:
        return "the name doesn't resolve: check the address"
    if "refused" in text.lower():
        return "the connection was refused: check the address and port"
    return f"can't connect ({type(exc).__name__})"
