import ssl
import urllib.error

from bsa import net
from bsa.assets import sounds


def test_ssl_context_falls_back_to_a_system_bundle(monkeypatch, tmp_path):
    loaded = []
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(net, "_default_can_verify", lambda ctx: False)
    bundle = tmp_path / "ca.pem"
    bundle.write_text("x")
    monkeypatch.setattr(net, "CA_BUNDLES", (str(tmp_path / "missing.pem"), str(bundle)))
    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", lambda self, cafile=None, **kw: loaded.append(cafile))
    net.ssl_context.cache_clear()
    try:
        net.ssl_context()
    finally:
        net.ssl_context.cache_clear()
    assert loaded == [str(bundle)]


def test_ssl_context_keeps_the_default_when_it_can_verify(monkeypatch):
    loaded = []
    monkeypatch.setattr(net, "_default_can_verify", lambda ctx: True)
    monkeypatch.setattr(ssl.SSLContext, "load_verify_locations", lambda self, **kw: loaded.append(kw))
    net.ssl_context.cache_clear()
    try:
        assert net.ssl_context().verify_mode == ssl.CERT_REQUIRED
    finally:
        net.ssl_context.cache_clear()
    assert loaded == []


def test_user_cert_env_is_trusted(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/somewhere/ca.pem")
    assert net._default_can_verify(ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


def test_certificate_failure_is_not_reported_as_dns():
    exc = urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))
    out = sounds.skipped("Freesound", exc)
    assert "certificate" in out["error"] and "DNS" not in out["error"]
    assert "SSL_CERT_FILE" in out["retry"]
    dns = sounds.skipped("Freesound", urllib.error.URLError(OSError("Name or service not known")))
    assert "network or DNS" in dns["error"]
