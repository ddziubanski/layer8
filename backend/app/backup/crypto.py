# SPDX-License-Identifier: AGPL-3.0-or-later
"""Passphrase encryption for backup archives, streamed in chunks.

File layout: a header (magic, scrypt salt and cost, nonce prefix), then chunks, each a 4-byte
big-endian length and an AES-256-GCM ciphertext of up to CHUNK bytes. Each chunk's nonce is
the prefix plus its number, and its associated data is the header plus a last-chunk flag,
so chunks can't be reordered, dropped, cut off at the end, or moved to another file.
"""

import io
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"HDBACKUP\x00\x01"
CHUNK = 1024 * 1024
# scrypt cost: 2**17 * 8 * 128 bytes = 128 MB of memory, about half a second.
LOG2_N, R, P = 17, 8, 1
HEADER = struct.Struct(f"{len(MAGIC)}s16sBBB8s")


class BackupCryptoError(ValueError):
    """Wrong passphrase, or the file is damaged or not a backup. Safe to show."""


def _key(passphrase: str, salt: bytes, log2_n: int, r: int, p: int) -> bytes:
    if not passphrase:
        raise BackupCryptoError("The backup passphrase isn't set")
    return Scrypt(salt=salt, length=32, n=2**log2_n, r=r, p=p).derive(passphrase.encode())


def _nonce(prefix: bytes, n: int) -> bytes:
    return prefix + struct.pack(">I", n)


class EncryptingWriter(io.RawIOBase):
    """A write-only file: what's written goes to `out` encrypted. close() writes the last chunk."""

    def __init__(self, out, passphrase: str, *, log2_n: int = LOG2_N):
        self.out = out
        salt, prefix = os.urandom(16), os.urandom(8)
        self.header = HEADER.pack(MAGIC, salt, log2_n, R, P, prefix)
        self.aead = AESGCM(_key(passphrase, salt, log2_n, R, P))
        self.prefix = prefix
        self.n = 0
        self.buf = bytearray()
        out.write(self.header)

    def writable(self) -> bool:
        return True

    def _emit(self, data: bytes, last: bool) -> None:
        if self.n >= 2**32 - 1:
            raise BackupCryptoError("Backup too large")
        sealed = self.aead.encrypt(_nonce(self.prefix, self.n), data, self.header + (b"\x01" if last else b"\x00"))
        self.out.write(struct.pack(">I", len(sealed)) + sealed)
        self.n += 1

    def write(self, b) -> int:
        self.buf += b
        while len(self.buf) > CHUNK:
            self._emit(bytes(self.buf[:CHUNK]), last=False)
            del self.buf[:CHUNK]
        return len(b)

    def close(self) -> None:
        if not self.closed:
            self._emit(bytes(self.buf), last=True)
            self.buf.clear()
        super().close()


class DecryptingReader(io.RawIOBase):
    """A read-only file of `src` decrypted. Raises BackupCryptoError on a wrong passphrase or any damage."""

    def __init__(self, src, passphrase: str):
        self.src = src
        header = src.read(HEADER.size)
        if len(header) < HEADER.size or not header.startswith(MAGIC):
            raise BackupCryptoError("Not a backup file from this app (or an unsupported version)")
        _, salt, log2_n, r, p, prefix = HEADER.unpack(header)
        if not (10 <= log2_n <= 22 and 1 <= r <= 32 and 1 <= p <= 16):
            raise BackupCryptoError("The backup's header is damaged")
        self.header = header
        self.aead = AESGCM(_key(passphrase, salt, log2_n, r, p))
        self.prefix = prefix
        self.n = 0
        self.buf = b""
        self.pos = 0
        self.done = False

    def readable(self) -> bool:
        return True

    def _next(self) -> None:
        raw = self.src.read(4)
        if len(raw) < 4:
            raise BackupCryptoError("The backup is cut short (incomplete copy?)")
        (size,) = struct.unpack(">I", raw)
        if size > CHUNK + 16:
            raise BackupCryptoError("The backup is damaged")
        sealed = self.src.read(size)
        if len(sealed) < size:
            raise BackupCryptoError("The backup is cut short (incomplete copy?)")
        for last in (False, True):
            try:
                self.buf, self.pos = self.aead.decrypt(_nonce(self.prefix, self.n), sealed, self.header + (b"\x01" if last else b"\x00")), 0
                break
            except InvalidTag:
                continue
        else:
            raise BackupCryptoError("Wrong passphrase, or the backup is damaged" if self.n == 0 else "The backup is damaged")
        self.n += 1
        if last:
            self.done = True
            if self.src.read(1):
                raise BackupCryptoError("The backup has unexpected data at the end")

    def readinto(self, b) -> int:
        while self.pos >= len(self.buf) and not self.done:
            self._next()
        n = min(len(b), len(self.buf) - self.pos)
        b[:n] = memoryview(self.buf)[self.pos : self.pos + n]
        self.pos += n
        return n
