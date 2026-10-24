import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode
import httpx
from fastapi import HTTPException

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

load_dotenv()

app = FastAPI()

AUTHORIZE_URL = "https://dashboard.snaptrade.com/oauth/authorize"
SCOPES = "openid profile email read"
TOKEN_URL = "https://api.snaptrade.com/oauth/token/"
ISSUER = "https://api.snaptrade.com"

# Remembers each login attempt while the user is away at SnapTrade.
# Key = state, value = the secret word and nonce. Lives in memory only:
# restarting the server forgets it, which is fine for a small project.
pending = {}


def make_pkce():
    verifier = secrets.token_urlsafe(64)  # the secret word
    digest = hashlib.sha256(verifier.encode()).digest()  # scramble it
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()  # the fingerprint
    return verifier, challenge

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/login")
def login():
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    pending[state] = {"verifier": verifier, "nonce": nonce}

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

    attempt = pending.pop(state, None)  # one use only
    if not code or attempt is None:
        raise HTTPException(400, "Unknown or expired login attempt. Start again at /login.")
    if iss and iss != ISSUER:
        raise HTTPException(400, "Unexpected issuer")

    resp = httpx.post(
        TOKEN_URL,
        auth=(os.getenv("SNAPTRADE_CLIENT_ID"), os.getenv("SNAPTRADE_CLIENT_SECRET")),
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
    # Safe summary only. Never return or print the tokens themselves.
    return {
        "ok": True,
        "scope": tokens.get("scope"),
        "expires_in": tokens.get("expires_in"),
        "has_refresh_token": "refresh_token" in tokens,
        "has_id_token": "id_token" in tokens,
    }
    
    
# @app.get("/config-check")
# def config_check():
#     names = [
#         "SNAPTRADE_CLIENT_ID",
#         "SNAPTRADE_CLIENT_SECRET",
#         "REDIRECT_URI",
#         "SESSION_SECRET",
#     ]
#     return {name: bool(os.getenv(name)) for name in names}