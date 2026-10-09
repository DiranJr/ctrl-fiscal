"""Gate: login/refresh/logout, CSRF, Origin, rotação com detecção de reuso."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import ORIGIN

A = "/api/core/v1/auth"


def _client(stack) -> TestClient:
    return TestClient(stack.core_app)


def test_login_sets_httponly_strict_cookie_and_returns_bearer(stack, world):
    c = _client(stack)
    r = c.post(f"{A}/login", json={"email": "a@ex.com", "password": "senha-forte-123"}, headers={"Origin": ORIGIN})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 900 and body["user"]["email"] == "a@ex.com"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api/core/v1/auth" in cookie
    assert "access_token" not in cookie


def test_login_requires_allowed_origin_and_rejects_bad_password(stack, world):
    c = _client(stack)
    assert c.post(f"{A}/login", json={"email": "a@ex.com", "password": "senha-forte-123"}).status_code == 403
    assert c.post(f"{A}/login", json={"email": "a@ex.com", "password": "senha-forte-123"},
                  headers={"Origin": "https://evil.example"}).status_code == 403
    r = c.post(f"{A}/login", json={"email": "a@ex.com", "password": "errada"}, headers={"Origin": ORIGIN})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    p = r.json()
    assert {"type", "title", "status", "detail", "instance", "code", "trace_id"} <= set(p)
    assert "senha" not in r.text.lower() or p["code"] == "INVALID_CREDENTIALS"


def test_login_rate_limited_after_failures(stack, world):
    c = _client(stack)
    for _ in range(5):
        assert c.post(f"{A}/login", json={"email": "a@ex.com", "password": "x"}, headers={"Origin": ORIGIN}).status_code == 401
    r = c.post(f"{A}/login", json={"email": "a@ex.com", "password": "senha-forte-123"}, headers={"Origin": ORIGIN})
    assert r.status_code == 429 and "Retry-After" in r.headers
    stack.clock.advance(minutes=16)
    assert c.post(f"{A}/login", json={"email": "a@ex.com", "password": "senha-forte-123"},
                  headers={"Origin": ORIGIN}).status_code == 200


def test_refresh_requires_csrf_and_rotates(stack, world):
    c = _client(stack)
    stack.login("a@ex.com", client=c)
    assert c.post(f"{A}/refresh", headers={"Origin": ORIGIN}).status_code == 403  # sem CSRF
    csrf = c.get(f"{A}/csrf").json()["csrf_token"]
    old_cookie = c.cookies.get("ctrl_refresh")
    r = c.post(f"{A}/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["access_token"]
    assert c.cookies.get("ctrl_refresh") != old_cookie
    me = c.get(f"{A}/me", headers={"Authorization": "Bearer " + r.json()["access_token"]})
    assert me.status_code == 200 and me.json()["companies"][0]["environment"] == "mock"
    # CSRF continua vinculado à mesma sessão após rotação.
    assert c.post(f"{A}/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status_code == 200


def test_refresh_reuse_revokes_family(stack, world):
    c = _client(stack)
    stack.login("a@ex.com", client=c)
    csrf = c.get(f"{A}/csrf").json()["csrf_token"]
    stolen = c.cookies.get("ctrl_refresh")
    assert c.post(f"{A}/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status_code == 200
    legit = c.cookies.get("ctrl_refresh")
    stack.clock.advance(seconds=30)  # fora da janela de corrida entre abas
    attacker = _client(stack)
    attacker.cookies.set("ctrl_refresh", stolen, path="/api/core/v1/auth")
    r = attacker.post(f"{A}/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert r.status_code == 401 and r.json()["code"] == "REFRESH_REUSE_DETECTED"
    c.cookies.set("ctrl_refresh", legit, path="/api/core/v1/auth")
    assert c.post(f"{A}/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status_code == 401


def test_logout_revokes_session_everywhere(stack, world):
    c = _client(stack)
    h = stack.login("a@ex.com", client=c)
    assert stack.fiscal.get(f"/api/fiscal/v1/companies/{world['a']}/documents", headers=h).status_code == 200
    csrf = c.get(f"{A}/csrf").json()["csrf_token"]
    assert c.post(f"{A}/logout", headers={"Origin": ORIGIN}).status_code == 403
    assert c.post(f"{A}/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status_code == 204
    # Access token ainda não expirou, mas a sessão foi revogada: core e fiscal recusam.
    assert c.get(f"{A}/me", headers=h).status_code == 401
    assert stack.fiscal.get(f"/api/fiscal/v1/companies/{world['a']}/documents", headers=h).status_code == 401
    assert c.post(f"{A}/refresh", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}).status_code in (401, 403)
    assert c.get(f"{A}/csrf").status_code == 401


def test_access_token_expires(stack, world):
    h = stack.login("a@ex.com")
    assert stack.core.get(f"{A}/me", headers=h).status_code == 200
    stack.clock.advance(minutes=16)
    r = stack.core.get(f"{A}/me", headers=h)
    assert r.status_code == 401 and r.json()["code"] == "INVALID_TOKEN"


def test_production_refuses_insecure_cookie(jwt_keys):
    from core_api.settings import CoreSettings
    from ctrl_common.config import ConfigError

    s = CoreSettings(database_url="sqlite://", jwt_private_key_pem=jwt_keys[0], jwt_public_key_pem=jwt_keys[1],
                     csrf_secret="x" * 40, app_env="production", allowed_origins=["https://ctrl.example"],
                     cookie_secure=False, allow_insecure_cookie=True)
    with pytest.raises(ConfigError):
        s.validate()
    s.cookie_secure = True
    s.validate()
    s.allowed_origins = ["http://ctrl.example"]
    with pytest.raises(ConfigError):
        s.validate()


def test_tampered_or_foreign_token_rejected(stack, world, jwt_keys):
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    other = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption())
    forged = pyjwt.encode({"sub": world["ua"], "sid": "x", "jti": "y", "typ": "access", "iss": "ctrl-core",
                           "aud": "ctrl-api", "iat": 1, "exp": 4102444800}, other, algorithm="EdDSA")
    for client, url in ((stack.core, f"{A}/me"), (stack.fiscal, f"/api/fiscal/v1/companies/{world['a']}/documents")):
        assert client.get(url, headers={"Authorization": "Bearer " + forged}).status_code == 401
    hs = pyjwt.encode({"sub": world["ua"]}, "segredo", algorithm="HS256")
    assert stack.core.get(f"{A}/me", headers={"Authorization": "Bearer " + hs}).status_code == 401


def test_local_password_rotation_preserves_account_and_revokes_sessions(stack, world):
    from core_api.set_password import rotate

    c = _client(stack)
    old = stack.login("a@ex.com", client=c)
    assert stack.fiscal.get(f"/api/fiscal/v1/companies/{world['a']}/documents", headers=old).status_code == 200
    new_secret = "nova-senha-local-" + "x" * 16  # valor de teste, não é credencial real
    out = rotate(stack.urls["core"], "A@EX.COM", new_secret)
    assert out["sessions_revoked"] >= 1
    # Sessão anterior revogada em core e fiscal; refresh antigo recusado.
    assert c.get(f"{A}/me", headers=old).status_code == 401
    assert stack.fiscal.get(f"/api/fiscal/v1/companies/{world['a']}/documents", headers=old).status_code == 401
    assert c.get(f"{A}/csrf").status_code == 401
    # Senha antiga não autentica; nova autentica e mantém empresas/permissões.
    assert c.post(f"{A}/login", json={"email": "a@ex.com", "password": "senha-forte-123"}, headers={"Origin": ORIGIN}).status_code == 401
    r = c.post(f"{A}/login", json={"email": "a@ex.com", "password": new_secret}, headers={"Origin": ORIGIN})
    assert r.status_code == 200
    me = c.get(f"{A}/me", headers={"Authorization": "Bearer " + r.json()["access_token"]}).json()
    assert [co["id"] for co in me["companies"]] == [world["a"]] and "prepare" in me["companies"][0]["permissions"]
    with pytest.raises(SystemExit):
        rotate(stack.urls["core"], "a@ex.com", "curta")

