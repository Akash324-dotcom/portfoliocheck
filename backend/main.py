import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from risk import analyze, normalize_position

load_dotenv()

app = FastAPI()

API_BASE = "https://api.snaptrade.com"
AUTHORIZE_URL = "https://dashboard.snaptrade.com/oauth/authorize"
TOKEN_URL = API_BASE + "/oauth/token/"
ISSUER = API_BASE
SCOPES = "openid profile email read"
PENDING_MAX_AGE = 600
SESSION_MAX_AGE = 36000
REFRESH_MARGIN = 60

pending = {}
sessions = {}


def client_auth():
    return (os.getenv("SNAPTRADE_CLIENT_ID"), os.getenv("SNAPTRADE_CLIENT_SECRET"))


def cookie_is_secure():
    return os.getenv("REDIRECT_URI", "").startswith("https://")


def make_pkce():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def forget_old_attempts():
    cutoff = time.time() - PENDING_MAX_AGE
    for key in [k for k, v in pending.items() if v["created"] < cutoff]:
        pending.pop(key, None)


def id_token_nonce(id_token):
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("nonce")
    except Exception:
        return None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/login")
def login():
    forget_old_attempts()
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    pending[state] = {"verifier": verifier, "nonce": nonce, "created": time.time()}

    params = {
        "response_type": "code",
        "client_id": os.getenv("SNAPTRADE_CLIENT_ID"),
        "redirect_uri": os.getenv("REDIRECT_URI"),
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(AUTHORIZE_URL + "?" + urlencode(params))


@app.get("/callback")
def callback(
    code: str | None = None,
    state: str | None = None,
    iss: str | None = None,
    error: str | None = None,
):
    if error:
        raise HTTPException(400, f"SnapTrade returned an error: {error}")

    attempt = pending.pop(state, None)
    if not code or attempt is None:
        raise HTTPException(400, "Unknown or expired login attempt. Start again at /login.")
    if time.time() - attempt["created"] > PENDING_MAX_AGE:
        raise HTTPException(400, "Login took too long. Start again at /login.")
    if iss and iss != ISSUER:
        raise HTTPException(400, "Unexpected issuer")

    resp = httpx.post(
        TOKEN_URL,
        auth=client_auth(),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": attempt["verifier"],
            "redirect_uri": os.getenv("REDIRECT_URI"),
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(502, f"Token exchange failed (status {resp.status_code})")

    tokens = resp.json()
    claimed_nonce = id_token_nonce(tokens.get("id_token") or "")
    if claimed_nonce is not None and claimed_nonce != attempt["nonce"]:
        raise HTTPException(400, "ID token nonce does not match this login")

    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": time.time() + tokens.get("expires_in", 0),
    }

    response = RedirectResponse("/")
    response.set_cookie(
        "session",
        session_id,
        httponly=True,
        samesite="lax",
        secure=cookie_is_secure(),
        max_age=SESSION_MAX_AGE,
    )
    return response


@app.post("/logout")
def logout(request: Request):
    sessions.pop(request.cookies.get("session"), None)
    response = JSONResponse({"ok": True})
    response.delete_cookie("session")
    return response


def current_session(request: Request):
    session = sessions.get(request.cookies.get("session"))
    if session is None:
        raise HTTPException(401, "Not signed in. Go to /login.")
    return session


def refresh_session(session):
    if not session.get("refresh_token"):
        raise HTTPException(401, "Session expired. Go to /login.")
    resp = httpx.post(
        TOKEN_URL,
        auth=client_auth(),
        data={"grant_type": "refresh_token", "refresh_token": session["refresh_token"]},
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(401, "Session expired. Go to /login.")
    tokens = resp.json()
    session["access_token"] = tokens["access_token"]
    session["refresh_token"] = tokens.get("refresh_token", session["refresh_token"])
    session["expires_at"] = time.time() + tokens.get("expires_in", 0)


def send_get(session, path):
    return httpx.get(
        API_BASE + path,
        headers={"Authorization": f"Bearer {session['access_token']}"},
        timeout=15,
    )


def snaptrade_get(session, path):
    if time.time() > session["expires_at"] - REFRESH_MARGIN:
        refresh_session(session)
    resp = send_get(session, path)
    if resp.status_code == 401:
        refresh_session(session)
        resp = send_get(session, path)
    if resp.status_code == 401:
        raise HTTPException(401, "Session expired. Go to /login.")
    if resp.status_code != 200:
        raise HTTPException(502, f"SnapTrade returned status {resp.status_code}")
    return resp.json()


def as_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "accounts", "positions", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


@app.get("/api/me")
def me(request: Request):
    return {"signed_in": sessions.get(request.cookies.get("session")) is not None}


@app.get("/api/accounts")
def accounts(request: Request):
    return snaptrade_get(current_session(request), "/accounts")


@app.get("/api/accounts/{account_id}/positions")
def positions(account_id: str, request: Request):
    safe_id = quote(account_id, safe="")
    return snaptrade_get(current_session(request), f"/accounts/{safe_id}/positions/all")


@app.get("/api/portfolio")
def portfolio(request: Request, account_id: str | None = None):
    session = current_session(request)
    account_rows = as_list(snaptrade_get(session, "/accounts"))

    summaries = []
    normalized = []
    for account in account_rows:
        if not isinstance(account, dict):
            continue
        this_id = str(account.get("id", ""))
        name = account.get("name") or account.get("institution_name") or "Account"
        summary = {"id": this_id, "name": name, "position_count": 0, "error": None}
        summaries.append(summary)
        if not this_id:
            summary["error"] = "Account has no id"
            continue
        if account_id and account_id != this_id:
            continue
        try:
            data = snaptrade_get(session, f"/accounts/{quote(this_id, safe='')}/positions/all")
        except HTTPException as exc:
            if exc.status_code == 401:
                raise
            summary["error"] = str(exc.detail)
            continue
        rows = as_list(data)
        summary["position_count"] = len(rows)
        normalized.extend(normalize_position(this_id, row) for row in rows)

    return {
        "accounts": summaries,
        "selected_account": account_id,
        "risk": analyze(normalized),
    }


FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
