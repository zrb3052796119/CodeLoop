from __future__ import annotations

import sys
from pathlib import Path

import pytest

import minicode.tls as tls


class _FakeContext:
    def __init__(self, ca_count: int = 0) -> None:
        self.ca_count = ca_count
        self.loaded: list[str] = []

    def cert_store_stats(self) -> dict[str, int]:
        return {"x509": self.ca_count, "crl": 0, "x509_ca": self.ca_count}

    def load_verify_locations(self, *, cafile: str) -> None:
        self.loaded.append(cafile)
        self.ca_count += 1


@pytest.fixture(autouse=True)
def _clear_tls_cache() -> None:
    tls.create_verified_ssl_context.cache_clear()
    yield
    tls.create_verified_ssl_context.cache_clear()


def test_empty_python_trust_store_loads_existing_system_ca_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "system-ca.pem"
    bundle.write_text("synthetic certificate bundle", encoding="utf-8")
    context = _FakeContext(ca_count=0)
    monkeypatch.setattr(tls.ssl, "create_default_context", lambda: context)
    monkeypatch.setattr(tls, "_SYSTEM_CA_CANDIDATES", (bundle,))
    monkeypatch.setitem(sys.modules, "certifi", None)

    result = tls.create_verified_ssl_context()

    assert result is context
    assert context.loaded == [str(bundle)]


def test_populated_python_trust_store_does_not_replace_system_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "system-ca.pem"
    bundle.write_text("synthetic certificate bundle", encoding="utf-8")
    context = _FakeContext(ca_count=12)
    monkeypatch.setattr(tls.ssl, "create_default_context", lambda: context)
    monkeypatch.setattr(tls, "_SYSTEM_CA_CANDIDATES", (bundle,))
    monkeypatch.setitem(sys.modules, "certifi", None)

    result = tls.create_verified_ssl_context()

    assert result is context
    assert context.loaded == []
