import time

import pytest
from fastapi.testclient import TestClient

import main

ACCOUNTS = [
    {"id": "acc-1", "name": "TFSA"},
    {"id": "acc-2", "name": "Margin"},
]

POSITIONS = {
    "acc-1": {"results": [
        {"instrument": {"kind": "equity", "symbol": "AAPL"}, "quantity": 10, "price": 100, "currency": "USD"},
        {"instrument": {"kind": "etf", "symbol": "VTI"}, "quantity": 5, "price": 200, "currency": "USD"},
    ]},
    "acc-2": {"results": [
        {"instrument": {"kind": "equity", "symbol": "AAPL"}, "quantity": 1, "price": 100, "currency": "USD"},
    ]},
}


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self.body = body or {}

    def json(self):
        return self.body


@pytest.fixture
def client():
    main.sessions.clear()
    main.pending.clear()
    main.sessions["sid"] = {
        "access_token": "old",
        "refresh_token": "r1",
        "expires_at": time.time() + 3600,
    }
    c = TestClient(main.app)
    c.cookies.set("session", "sid")
    return c


@pytest.fixture
def fake_snaptrade(monkeypatch):
    def fake(session, path):
        if path == "/accounts":
            return ACCOUNTS
        for key, data in POSITIONS.items():
            if path == f"/accounts/{key}/positions/all":
                return data
        raise AssertionError(path)

    monkeypatch.setattr(main, "snaptrade_get", fake)


def test_health():
    assert TestClient(main.app).get("/health").json() == {"status": "ok"}


def test_requires_login():
    assert TestClient(main.app).get("/api/portfolio").status_code == 401


def test_me(client):
    assert client.get("/api/me").json() == {"signed_in": True}
    assert TestClient(main.app).get("/api/me").json() == {"signed_in": False}


def test_portfolio_all_accounts(client, fake_snaptrade):
    body = client.get("/api/portfolio").json()
    assert [a["name"] for a in body["accounts"]] == ["TFSA", "Margin"]
    assert body["risk"]["total_value"] == 2100.0
    assert body["risk"]["holdings"][0]["symbol"] == "AAPL"
    assert body["risk"]["holdings"][0]["value"] == 1100.0
    assert body["risk"]["holding_count"] == 2


def test_portfolio_single_account(client, fake_snaptrade):
    body = client.get("/api/portfolio", params={"account_id": "acc-2"}).json()
    assert body["risk"]["total_value"] == 100.0
    assert body["risk"]["holding_count"] == 1


def test_account_error_does_not_break_page(client, monkeypatch):
    def fake(session, path):
        if path == "/accounts":
            return ACCOUNTS
        if "acc-1" in path:
            raise main.HTTPException(502, "SnapTrade returned status 500")
        return POSITIONS["acc-2"]

    monkeypatch.setattr(main, "snaptrade_get", fake)
    body = client.get("/api/portfolio").json()
    assert body["accounts"][0]["error"] == "SnapTrade returned status 500"
    assert body["risk"]["holding_count"] == 1


def test_token_refresh_after_401(monkeypatch):
    session = {"access_token": "old", "refresh_token": "r1", "expires_at": time.time() + 3600}
    calls = []

    def fake_get(url, headers, timeout):
        calls.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer old":
            return FakeResponse(401)
        return FakeResponse(200, {"ok": True})

    def fake_post(url, auth, data, timeout):
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "r1"
        return FakeResponse(200, {"access_token": "new", "refresh_token": "r2", "expires_in": 36000})

    monkeypatch.setattr(main.httpx, "get", fake_get)
    monkeypatch.setattr(main.httpx, "post", fake_post)
    assert main.snaptrade_get(session, "/accounts") == {"ok": True}
    assert calls == ["Bearer old", "Bearer new"]
    assert session["refresh_token"] == "r2"


def test_refresh_before_expiry(monkeypatch):
    session = {"access_token": "old", "refresh_token": "r1", "expires_at": time.time() + 10}
    monkeypatch.setattr(
        main.httpx, "post",
        lambda url, auth, data, timeout: FakeResponse(200, {"access_token": "new", "expires_in": 36000}),
    )
    monkeypatch.setattr(main.httpx, "get", lambda url, headers, timeout: FakeResponse(200, {"token": headers["Authorization"]}))
    assert main.snaptrade_get(session, "/accounts") == {"token": "Bearer new"}
    assert session["refresh_token"] == "r1"


def test_failed_refresh_means_signed_out(monkeypatch):
    session = {"access_token": "old", "refresh_token": "r1", "expires_at": 0}
    monkeypatch.setattr(main.httpx, "post", lambda url, auth, data, timeout: FakeResponse(400))
    with pytest.raises(main.HTTPException) as caught:
        main.snaptrade_get(session, "/accounts")
    assert caught.value.status_code == 401


def test_logout_clears_session(client):
    assert client.post("/logout").json() == {"ok": True}
    assert "sid" not in main.sessions


def test_callback_rejects_unknown_state():
    response = TestClient(main.app).get("/callback", params={"code": "x", "state": "nope"})
    assert response.status_code == 400


def test_callback_rejects_wrong_issuer():
    main.pending["s"] = {"verifier": "v", "nonce": "n", "created": time.time()}
    response = TestClient(main.app).get("/callback", params={"code": "x", "state": "s", "iss": "https://evil.example"})
    assert response.status_code == 400


def test_login_redirects_with_pkce(monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "cid")
    monkeypatch.setenv("REDIRECT_URI", "http://localhost:8000/callback")
    response = TestClient(main.app).get("/login", follow_redirects=False)
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert location.startswith(main.AUTHORIZE_URL)
    assert "code_challenge_method=S256" in location
    assert "client_id=cid" in location
    assert len(main.pending) == 1


def test_id_token_nonce_reader():
    import base64, json
    payload = base64.urlsafe_b64encode(json.dumps({"nonce": "abc"}).encode()).rstrip(b"=").decode()
    assert main.id_token_nonce(f"h.{payload}.s") == "abc"
    assert main.id_token_nonce("garbage") is None
