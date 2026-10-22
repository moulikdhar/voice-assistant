
from __future__ import annotations

import os
import ssl
import sys
import threading
import warnings

# ─────────────── 1. one SSLContext per library + trust settings ───────────────
_orig_create_default_context = ssl.create_default_context
_ctx_cache: dict[tuple, ssl.SSLContext] = {}
_ctx_lock = threading.Lock()


def _caller_library() -> str:
    """Top-level package of the code that asked for a context ('httpx', 'websockets', 'mistralai'...)."""
    f = sys._getframe(2)
    while f is not None:
        name = f.f_globals.get("__name__", "")
        if name and not name.startswith(("ssl", __name__)):
            return name.split(".")[0]
        f = f.f_back
    return "?"


def _shared_create_default_context(purpose=ssl.Purpose.SERVER_AUTH, *, cafile=None, capath=None, cadata=None):
    # Only the normal client case is shared. Anything unusual gets a fresh context, as before.
    if purpose != ssl.Purpose.SERVER_AUTH or cadata is not None:
        return _orig_create_default_context(purpose, cafile=cafile, capath=capath, cadata=cadata)
    key = (_caller_library(), str(cafile), str(capath),
           os.environ.get("SSL_CERT_FILE"), os.environ.get("SSL_CERT_DIR"))
    ctx = _ctx_cache.get(key)
    if ctx is None:
        with _ctx_lock:
            ctx = _ctx_cache.get(key)
            if ctx is None:
                ctx = _orig_create_default_context(purpose, cafile=cafile, capath=capath)
                _ctx_cache[key] = ctx
    # A library that turns verification OFF on "its" context would weaken it for its later connections:
    # in that case hand out a private context instead of the shared one.
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
        with _ctx_lock:
            _ctx_cache.pop(key, None)
        return _orig_create_default_context(purpose, cafile=cafile, capath=capath)
    return ctx


# ─────────────── 2. Windows: read the certificate store once ───────────────
_store_cache: dict[tuple[str, str], bytes] = {}


def _cached_store_certs(self: ssl.SSLContext, storename: str, purpose) -> None:
    key = (storename, purpose.oid)
    data = _store_cache.get(key)
    if data is None:
        chunks = []
        try:
            for cert, encoding, trust in ssl.enum_certificates(storename):  # type: ignore[attr-defined]
                if encoding == "x509_asn" and (trust is True or purpose.oid in trust):
                    chunks.append(cert)
        except PermissionError:
            warnings.warn("unable to enumerate Windows certificate store")
        data = _store_cache[key] = b"".join(chunks)
    if data:
        try:
            self.load_verify_locations(cadata=data)
        except ssl.SSLError:  # one bad certificate: fall back to loading them one by one
            for cert in _split_der(data):
                try:
                    self.load_verify_locations(cadata=cert)
                except ssl.SSLError:
                    pass


def _split_der(data: bytes):
    i = 0
    while i < len(data):
        if data[i] != 0x30:
            return
        n = data[i + 1]
        if n & 0x80:
            k = n & 0x7F
            length = int.from_bytes(data[i + 2:i + 2 + k], "big")
            hdr = 2 + k
        else:
            length, hdr = n, 2
        yield data[i:i + hdr + length]
        i += hdr + length


_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    if sys.platform == "win32" and hasattr(ssl, "enum_certificates"):
        ssl.SSLContext._load_windows_store_certs = _cached_store_certs  # type: ignore[method-assign]
    ssl.create_default_context = _shared_create_default_context  # type: ignore[assignment]


install()


if __name__ == "__main__":  # python -m backend.winssl  -> shows the saving on this machine
    import time

    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())  # what LiveKit does on Windows
    for label, fn in (("original (every connection)", lambda: _orig_create_default_context()),
                      ("shared (this fix)", lambda: ssl.create_default_context())):
        t = time.perf_counter()
        for _ in range(5):
            fn()
        print(f"{label:30s} {1000 * (time.perf_counter() - t) / 5:7.1f} ms per new connection")
