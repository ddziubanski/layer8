# SPDX-License-Identifier: AGPL-3.0-or-later
"""Console certificates, against a real TLS server on 127.0.0.1."""
import datetime
import ipaddress
import socket
import ssl
import threading

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import tls


def _cert(name, *, issuer=None, ca=None, sans=()):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    builder = (x509.CertificateBuilder().subject_name(subject)
               .issuer_name(issuer[0].subject if issuer else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30)))
    if ca is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    if ca:
        builder = builder.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    if issuer:
        builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer[1].public_key()), critical=False)
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
    return builder.sign(issuer[1] if issuer else key, hashes.SHA256()), key


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture
def server(tmp_path):
    """Serve one HTTPS answer per connection with the certificate (and key) given."""
    servers = []

    def start(cert, key):
        (tmp_path / "server.pem").write_bytes(_pem(cert) + key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tmp_path / "server.pem")
        listener = socket.create_server(("127.0.0.1", 0))
        listener.settimeout(5)
        seen = []

        def serve():
            while True:
                try:
                    raw, _ = listener.accept()
                except OSError:
                    return
                try:
                    with context.wrap_socket(raw, server_side=True) as conn:
                        seen.append(conn.recv(4096))
                        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                except (ssl.SSLError, OSError):
                    pass

        threading.Thread(target=serve, daemon=True).start()
        servers.append(listener)
        return listener.getsockname()[1], seen

    yield start
    for listener in servers:
        listener.close()


def _get(url, ca_file):
    with httpx.Client(verify=tls.verify(ca_file), trust_env=False, timeout=5) as http:
        return http.get(url, headers={"X-API-KEY": "secret-key"})


def test_factory_certificate_is_pinned_whatever_the_address(server, tmp_path):
    # Like a UniFi console: self-signed, no CA flag, names only unifi.local.
    cert, key = _cert("unifi.local", sans=("unifi.local",))
    port, _ = server(cert, key)
    (tmp_path / "pin.pem").write_bytes(_pem(cert))
    assert _get(f"https://127.0.0.1:{port}/", str(tmp_path / "pin.pem")).status_code == 200


def test_another_certificate_is_refused_before_the_key_is_sent(server, tmp_path):
    cert, key = _cert("unifi.local")
    impostor, impostor_key = _cert("unifi.local")
    port, seen = server(impostor, impostor_key)
    (tmp_path / "pin.pem").write_bytes(_pem(cert))
    with pytest.raises(httpx.ConnectError) as failed:
        _get(f"https://127.0.0.1:{port}/", str(tmp_path / "pin.pem"))
    assert "isn't trusted" in tls.reason(failed.value)
    assert not any(b"secret-key" in r for r in seen)


def test_a_real_ca_still_checks_the_name(server, tmp_path):
    ca = _cert("Example CA", ca=True)
    leaf, leaf_key = _cert("localhost", issuer=ca, ca=False, sans=("localhost",))
    port, _ = server(leaf, leaf_key)
    (tmp_path / "ca.pem").write_bytes(_pem(ca[0]))
    assert _get(f"https://localhost:{port}/", str(tmp_path / "ca.pem")).status_code == 200
    with pytest.raises(httpx.ConnectError) as failed:
        _get(f"https://127.0.0.1:{port}/", str(tmp_path / "ca.pem"))
    assert "doesn't name this address" in tls.reason(failed.value)


def test_no_ca_file_uses_the_system_store():
    assert tls.verify("") is True


def test_reasons_for_unreachable_consoles():
    closed = socket.create_server(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    with pytest.raises(httpx.ConnectError) as refused:
        httpx.get(f"https://127.0.0.1:{port}/", timeout=2)
    assert "refused" in tls.reason(refused.value)
    assert "in time" in tls.reason(httpx.ConnectTimeout("timed out"))
    assert ipaddress.ip_address("127.0.0.1")  # loopback only: no outside network in these tests
