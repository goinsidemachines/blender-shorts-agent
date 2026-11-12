"""HTTPS for the plugin's downloads and API calls.

Blender's bundled Python is built with its own OpenSSL, whose default CA locations often don't exist on the user's
machine (Linux especially): every HTTPS request then fails with CERTIFICATE_VERIFY_FAILED. `ssl_context` uses the
default context when it can verify (or when SSL_CERT_FILE / SSL_CERT_DIR say where the CAs are), else the first
system CA bundle it finds.
"""

from __future__ import annotations

import os
import ssl
import urllib.request
from functools import lru_cache
from pathlib import Path

# System CA bundles: Debian/Ubuntu, Fedora/RHEL, Arch/Alpine/macOS, openSUSE, Homebrew.
CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
    "/etc/ssl/ca-bundle.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
)


def _default_can_verify(ctx: ssl.SSLContext) -> bool:
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return True  # the user said where the CAs are: never second-guess it
    if ctx.cert_store_stats().get("x509_ca", 0):
        return True  # loaded at creation (a CA file, or the Windows / macOS store)
    paths = ssl.get_default_verify_paths()
    for p in (paths.cafile, paths.capath):  # a CA directory loads lazily, so it isn't counted above
        if p and Path(p).exists() and (Path(p).is_file() or any(Path(p).iterdir())):
            return True
    return False


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """A verifying SSL context that has CA certificates to verify with (see the module docstring)."""
    ctx = ssl.create_default_context()
    if _default_can_verify(ctx):
        return ctx
    for bundle in CA_BUNDLES:
        if Path(bundle).is_file():
            ctx.load_verify_locations(cafile=bundle)
            break
    return ctx


def urlopen(req, timeout: float | None = None):
    """urllib.request.urlopen with `ssl_context()`."""
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())
