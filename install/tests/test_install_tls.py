# SPDX-License-Identifier: AGPL-3.0-or-later
"""install.sh's early check of your own HTTPS certificate: one that can't be used falls back to a
self-signed certificate with a warning instead of stopping the install at the HTTPS step.

The real install.sh runs with `id` and `apt-get` replaced, and stops at its first package step.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

INSTALL = Path(__file__).resolve().parents[1] / "install.sh"


def make_cert(directory: Path, name: str) -> tuple[Path, Path]:
    cert, key = directory / f"{name}.pem", directory / f"{name}-key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=helpdesk.corp.example",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    return cert, key


@unittest.skipUnless(os.name == "posix" and shutil.which("openssl"), "needs a POSIX shell and openssl")
class InstallTlsCheckTests(unittest.TestCase):
    def run_install(self, tls, hostname="helpdesk.corp.example"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "bin"
            binaries.mkdir()
            for name, body in (("id", "echo 0"), ("timedatectl", "exit 0"), ("apt-get", "echo STOPPED-AT-PACKAGES >&2; exit 42")):
                (binaries / name).write_text(f"#!/bin/sh\n{body}\n")
                (binaries / name).chmod(0o755)
            tls_value = tls(root) if callable(tls) else tls
            answers = root / "answers.json"
            answers.write_text(json.dumps({"install": {"hostname": hostname, "tls": tls_value,
                                                       "admin": {"email": "admin@example.com", "password": "Synthetic-password-123"}}}))
            return subprocess.run(["sh", str(INSTALL), "--answers", str(answers)], capture_output=True, text=True,
                                  env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}"})

    def assert_falls_back(self, result, why):
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn("STOPPED-AT-PACKAGES", result.stderr)
        self.assertIn("Installing with a self-signed certificate instead", result.stderr)
        self.assertIn(why, result.stderr)

    def test_missing_certificate_falls_back_to_self_signed(self):
        result = self.run_install({"cert": "/home/sdadmin/cert.pem", "key": "/home/sdadmin/key.pem"})
        self.assert_falls_back(result, "there's no certificate file at /home/sdadmin/cert.pem")

    def test_key_from_another_certificate_falls_back(self):
        def tls(root):
            cert, _ = make_cert(root, "a")
            _, other_key = make_cert(root, "b")
            return {"cert": str(cert), "key": str(other_key)}
        self.assert_falls_back(self.run_install(tls), "doesn't belong to the certificate")

    def test_not_pem_falls_back(self):
        def tls(root):
            (root / "cert.pem").write_text("not a certificate")
            (root / "key.pem").write_text("not a key")
            return {"cert": str(root / "cert.pem"), "key": str(root / "key.pem")}
        self.assert_falls_back(self.run_install(tls), "isn't a PEM certificate")

    def test_usable_certificate_is_kept(self):
        def tls(root):
            cert, key = make_cert(root, "good")
            return {"cert": str(cert), "key": str(key)}
        result = self.run_install(tls)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertNotIn("self-signed certificate instead", result.stderr)

    def test_self_signed_needs_no_files(self):
        result = self.run_install("self-signed")
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertNotIn("HTTPS certificate", result.stderr)

    def test_host_name_that_isnt_a_dns_name_stops_before_anything_changes(self):
        for bad in ("help desk", "a/b.example", "bad_name.example", "x&y"):
            result = self.run_install("self-signed", hostname=bad)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("install.hostname isn't a DNS name", result.stderr)
            self.assertNotIn("STOPPED-AT-PACKAGES", result.stderr)


if __name__ == "__main__":
    unittest.main()
