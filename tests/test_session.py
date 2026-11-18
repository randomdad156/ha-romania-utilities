"""JWT expiry parsing used to time E·ON session renewals."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

_PATH = Path(__file__).parents[1] / "custom_components" / "romania_utilities" / "session.py"
_spec = importlib.util.spec_from_file_location("ru_session", _PATH)
s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s)


def _jwt(claims: dict) -> str:
    def enc(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'HS256'})}.{enc(claims)}.sig"


def test_reads_exp_claim() -> None:
    assert s.jwt_expiry(_jwt({"exp": 1900000000, "sub": "x"})) == 1900000000.0


def test_padding_is_restored() -> None:
    for extra in ("", "a", "ab", "abc"):
        assert s.jwt_expiry(_jwt({"exp": 1900000000, "p": extra})) == 1900000000.0


def test_not_a_jwt_or_no_exp() -> None:
    assert s.jwt_expiry(None) is None
    assert s.jwt_expiry("opaque-token") is None
    assert s.jwt_expiry("a.b.c") is None
    assert s.jwt_expiry(_jwt({"sub": "x"})) is None
    assert s.jwt_expiry(_jwt({"exp": "soon"})) is None


def test_cookie_export_and_import_roundtrip() -> None:
    from http.cookies import SimpleCookie

    jar_in = SimpleCookie()
    jar_in["sid"] = "abc"
    jar_in["sid"]["domain"] = ".api.example.test"
    jar_in["sid"]["path"] = "/users"
    jar_in["sid"]["secure"] = True
    saved = s.export_cookies(jar_in.values())
    assert saved == [
        {"name": "sid", "value": "abc", "domain": "api.example.test", "path": "/users", "secure": True}
    ]

    class FakeJar:
        def __init__(self) -> None:
            self.calls: list = []

        def update_cookies(self, cookie, response_url=None) -> None:
            self.calls.append((dict(cookie), str(response_url)))

    import pytest

    pytest.importorskip("yarl")
    jar = FakeJar()
    assert s.import_cookies(jar, saved) == 1
    assert jar.calls[0][1] == "https://api.example.test/users"
    assert s.import_cookies(jar, None) == 0
    assert s.import_cookies(jar, [{"value": "x"}]) == 0
