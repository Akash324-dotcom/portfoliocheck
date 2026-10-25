# Portfolio Risk Check

LIVE URL - https://portfolio-risk-check.onrender.com/

A small web app built on the SnapTrade OAuth API. You sign in with SnapTrade, the app reads your accounts and positions (read-only), and it flags concentration risk: one holding dominating, too few holdings, one asset type, mixed currencies.

## How it works

1. `/login` creates a PKCE secret and its fingerprint, a `state` and a `nonce`, and redirects to SnapTrade's authorize page requesting the scopes `openid profile email read`.
2. You sign in with a personal SnapTrade account and approve access.
3. SnapTrade redirects to `/callback?code=...&state=...&iss=...`. The app checks `state` and the issuer, exchanges the code (plus the PKCE secret) for tokens on the server, checks the ID token `nonce` when present, and stores the tokens in a server-side session. The browser only receives an opaque session cookie (`HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS).
4. `/api/portfolio` calls `GET /accounts` and `GET /accounts/{id}/positions/all` with the Bearer token, normalises the positions, and runs the risk rules in `backend/risk.py`.
5. The tokens last 10 hours. Before they expire (or after a 401) the app uses the refresh token to get new ones. Refresh tokens rotate, so the new one replaces the old one.

## Risk rules

| Rule | Trigger | Points |
| --- | --- | --- |
| Single holding, high | largest holding is 40% or more | 35 |
| Single holding, medium | largest holding is 25% to 40% | 20 |
| Top three | more than 3 holdings and the top three exceed 60% | 15 |
| Few holdings | fewer than 3 holdings | 25 |
| Few holdings | 3 or 4 holdings | 15 |
| One asset type | one type above 90% | 10 |
| Missing values | positions with no usable price or value | 5 |
| Mixed currency | holdings in more than one currency | 5 |

The score is the sum of points, capped at 100. Below 25 is low, below 50 is medium, otherwise high. The same symbol held in several accounts is merged. Weights are calculated within the largest currency only, because no exchange rates are applied; other currencies are reported, not mixed in.

## Run locally

```
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env
uvicorn main:app --reload
```

Fill in `backend/.env`:

```
SNAPTRADE_CLIENT_ID=
SNAPTRADE_CLIENT_SECRET=
REDIRECT_URI=http://localhost:8000/callback
SESSION_SECRET=
```

The redirect URI must match the one registered in the SnapTrade OAuth app exactly. Open `http://localhost:8000`.

Tests:

```
cd backend
python -m pytest
```

## Deploy on Render

1. Push the repo to GitHub and create a Blueprint on Render from `render.yaml`, or create a Web Service with build command `pip install -r backend/requirements.txt` and start command `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`.
2. Set `SNAPTRADE_CLIENT_ID`, `SNAPTRADE_CLIENT_SECRET` and `REDIRECT_URI` (`https://<your-service>.onrender.com/callback`) as environment variables.
3. Add the same HTTPS callback URL to the SnapTrade OAuth app. Production redirect URIs must be HTTPS; localhost is only for testing.

## Project layout

```
backend/main.py             routes, OAuth flow, sessions, token refresh
backend/risk.py             position normalising and risk rules
backend/test_risk.py        tests for the rules
backend/test_api.py         tests for the routes and token refresh
frontend/                   plain HTML, CSS and JavaScript served by FastAPI
render.yaml                 Render deploy settings
```

## What I'd improve

- Sessions and pending logins live in server memory, so a restart signs everyone out and it only works with one process. A real session store (Redis or a database) is the first thing to change.
- Tokens are stored unencrypted in memory. They should be encrypted at rest if they are ever persisted.
- The ID token is only checked for the `nonce`. Its signature should be verified against SnapTrade's published keys.
- No exchange rates, so mixed-currency portfolios are analysed one currency at a time.
- The rules are simple guidelines. Sector, region and correlation exposure would need data the positions endpoint does not return.
- Positions are fetched on every page load. Short-lived caching would reduce calls.
- Rate limits and API errors are reported as a single message per account; retries with backoff are not implemented.

## Developer experience notes (friction while building)

- OAuth sign-in needs a personal SnapTrade account, which is separate from the Commercial account that owns the app. It is easy to miss that you must create one.
- The OAuth app screen has no place to choose scopes. They are requested per authorization with the `scope` parameter, so the missing setting is easy to mistake for a missing feature.
- Opening the authorize URL without all parameters shows "Missing required OAuth query parameters" without saying which one is missing.
- Several older holdings endpoints are deprecated in favour of `/accounts/{accountId}/positions/all`, so it takes some reading to find the right one for OAuth.
- The test OAuth app and the documentation make it hard to tell which limits apply in testing versus production.
