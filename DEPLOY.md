# Deploying LedgerFlow to Render

LedgerFlow ships with a [`render.yaml`](render.yaml) **Blueprint** that provisions everything:

| Component | Render type | Plan |
|---|---|---|
| PostgreSQL (ledger of record) | Managed Postgres | free |
| Redis / Key Value (event stream) | Redis | free |
| FastAPI backend (+ embedded relay/worker) | Docker web service | free |
| React frontend | Static site | free |

On the free tier there's no standalone Background Worker, so the outbox relay + event worker run **in-process** inside the backend (`RUN_EMBEDDED_WORKERS=true`). In a paid/production setup you'd split them into their own `type: worker` services — the code already supports both (see `compose.override.yml`).

---

## 1. Push the repo to GitHub

```bash
git remote add origin https://github.com/<you>/ledgerflow.git
git push -u origin main
```

## 2. Create the Blueprint on Render

1. Render Dashboard → **New** → **Blueprint**.
2. Connect your GitHub account and pick the `ledgerflow` repo.
3. Render reads `render.yaml` and shows the four resources it will create.
4. You'll be prompted for the one secret left unset: **`FIRST_SUPERUSER_PASSWORD`** — enter a strong password and **remember it**, it's your admin login. (Everything else — `SECRET_KEY`, the DB credentials, `REDIS_URL` — is generated/wired automatically.)
5. Click **Apply**. First build takes a few minutes (Docker image + migrations + frontend bundle).

## 3. Fix up the URLs (one-time)

Render assigns each service a URL like `https://ledgerflow-backend.onrender.com`. If your service names ended up different (e.g. a suffix was added because a name was taken), update these so the frontend and CORS line up:

- **frontend** service → env var `VITE_API_URL` → the backend's real URL → trigger a redeploy (it's baked in at build time).
- **backend** service → env vars `FRONTEND_HOST` and `BACKEND_CORS_ORIGINS` → the frontend's real URL.

## 4. Log in

Open the frontend URL, log in as `admin@example.com` with the password you set, and click **Load demo data**. Visit **Operations** and **Events** to show the live ledger integrity + event flow.

> ⚠️ Free Render web services **spin down when idle** — the first request after a nap takes ~50s to wake. That's expected. Free Postgres instances expire after 90 days.

---

## How to see the database in production

You have four options, easiest first.

### A. Through the app
The **Operations** and **Events** pages plus the wallet/activity tables are already a live read-view of the data. Often enough for a demo.

### B. A GUI client (best for browsing) — TablePlus / DBeaver / pgAdmin / Postico
1. Render Dashboard → your **`ledgerflow-db`** → **Connect** → copy the **External Database URL**
   (looks like `postgresql://ledgerflow:…@dpg-xxxx.oregon-postgres.render.com/ledgerflow`).
2. Paste it into the client. SSL is required (clients usually auto-detect). You get full table browsing.

> The **Internal** URL only works between Render services. Use the **External** one from your laptop.

### C. `psql` from your laptop
```bash
psql "postgresql://ledgerflow:…@dpg-xxxx.oregon-postgres.render.com/ledgerflow?sslmode=require"

# then, for example:
\dt                                   -- list tables
SELECT name FROM account;             -- the wallets
SELECT event_type, status FROM outbox_event ORDER BY created_at DESC LIMIT 10;
SELECT COALESCE(SUM(amount_cents),0) AS drift FROM ledger_entry;   -- must be 0
```

### D. Render Shell (no local tools)
Backend service → **Shell** tab → run a one-off query through the app's own engine:
```bash
python -c "from sqlmodel import Session, select; from app.core.db import engine; \
from app.models import Account; \
print([(a.name) for a in Session(engine).exec(select(Account)).all()])"
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Frontend loads but every call fails (CORS / network) | `VITE_API_URL` (frontend) or `BACKEND_CORS_ORIGINS` (backend) doesn't match the real URLs — fix and redeploy. |
| "User not found" after login | Old token in the browser — run `localStorage.clear()` and log in again. |
| Backend boot fails on "changethis" secret | `ENVIRONMENT=production` refuses default secrets — make sure `SECRET_KEY` is generated and `FIRST_SUPERUSER_PASSWORD` is set. |
| First request hangs ~50s | Free service woke from idle spin-down — normal. |
| Events stay `pending` | The embedded relay didn't start — confirm `RUN_EMBEDDED_WORKERS=true` on the backend. |
