# LedgerFlow

**The money engine inside a digital wallet** — an immutable, double-entry payment ledger with an event-driven core.

Like the part of Venmo or a neobank that actually moves money: people hold balances, get paid, send money to each other, and pay subscriptions. LedgerFlow is the backend that does that **correctly** — every cent accounted for, no double-charges, nothing lost, and a mathematical guarantee that the books always balance.

[![CI](https://github.com/smruthidinesh/ledgerflow/actions/workflows/ci.yml/badge.svg)](https://github.com/smruthidinesh/ledgerflow/actions/workflows/ci.yml)

> 🔗 **Live demo:** _add your Render URL here after deploying_ · log in with the admin credentials you set, or click **Load demo data**.

---

## Why it's interesting

Most CRUD apps store a `balance` column and update it. That silently corrupts the moment two requests race, a retry fires twice, or a multi-step payment fails halfway. LedgerFlow is built the way real payment systems are, around a few guarantees:

| Guarantee | How |
|---|---|
| 🧮 **Always balanced** | Money is **integer cents** recorded as immutable, append-only **double-entry** rows. Every transaction's debits and credits sum to exactly zero. A balance is *derived* (`SUM(entries)`), never stored. |
| 🛡️ **No double-charge** | Every money movement carries an **idempotency key** with a unique DB constraint. Retried requests return the original transaction instead of moving money twice. |
| 🔁 **Safe multi-step transfers** | Multi-step flows run as a **SAGA**: reserve → capture, with **compensation** that releases funds back if a later step fails. No partial states. |
| 📡 **No lost events** | Each movement writes a domain event to a **transactional outbox** in the *same* DB transaction, then a relay publishes it to **Redis Streams** for at-least-once delivery. A Redis outage delays events; it never loses them. |
| ✅ **Provably no drift** | A **reconciliation** job re-proves that the entire ledger nets to exactly `$0.00` — surfaced live on the Operations dashboard. |

There's also a **recurring-payments** scheduler (standing orders that execute on their own) and **role-based access** (customers see only their own wallets; operators see system-wide dashboards).

## Architecture

```
                         ┌─────────────────────────────────────────────┐
   React SPA  ──HTTPS──▶ │  FastAPI                                     │
   (wallets,             │   • /ledger  double-entry posting            │
    ops & events         │   • idempotency keys, row locks (SELECT…FOR  │
    dashboards)          │     UPDATE), SAGA + compensation             │
                         │                                              │
                         │   one DB transaction writes BOTH:            │
                         │   ledger entries ──┐      ┌── outbox event    │
                         └────────────────────┼──────┼───────────────────┘
                                              ▼      ▼
                                   ┌──────────────────────────┐
                                   │ PostgreSQL                │
                                   │  account · ledger_entry · │
                                   │  ledger_transaction ·     │
                                   │  outbox_event · recurring │
                                   └──────────┬────────────────┘
                                              │ relay polls pending events
                                              ▼
                                   ┌──────────────────────────┐      ┌──────────────┐
                                   │ Redis Stream              │────▶ │ Event worker │
                                   │  "ledger.events"          │ XACK │ consumer grp │
                                   └──────────────────────────┘      └──────────────┘
```

The **outbox relay** also drives the recurring-payment **scheduler**. See [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md) for the full, beginner-to-advanced walkthrough.

## Tech stack

**Backend:** Python 3.14 · FastAPI · SQLModel / SQLAlchemy · PostgreSQL · Redis Streams · Alembic · `uv`
**Frontend:** React 19 · Vite · TanStack Router · Tailwind · shadcn/Radix
**Infra:** Docker Compose · GitHub Actions CI · Prometheus metrics · deploy on Render

## Run it locally

```bash
# bring up Postgres, Redis, backend, relay, worker, frontend
docker compose up -d

# seed the superuser (first run)
docker compose exec backend python app/initial_data.py
```

- Frontend: http://localhost:5173 — log in as `admin@example.com` / `changethis`, then click **Load demo data**.
- API docs: http://localhost:8000/docs
- Metrics: http://localhost:8000/api/v1/ledger/metrics

## Tests

```bash
docker compose exec backend python -m pytest tests/ -q
```

Includes a concurrency test that fires simultaneous transfers against one account and asserts it can **never** overdraft.

## The three things to look at

1. **Ledger** — create wallets, deposit, transfer, set up a recurring payment, watch the live activity feed.
2. **Operations** (operator) — total volume, the **drift gauge held at $0.00**, and Redis stream/consumer health.
3. **Events** (operator) — a flow diagram of the event-driven pipeline plus a **live event log** where you watch each event go `pending → published`.

## Deploy

One-click-ish via the [Render blueprint](render.yaml) — see [`DEPLOY.md`](DEPLOY.md).

## Docs

- [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md) — full explanation from fundamentals to advanced, with examples, **and how to explain it in an interview**.
- [`DEPLOY.md`](DEPLOY.md) — deploying to Render and inspecting the production database.

---

<sub>Built on the [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) foundation; the ledger domain, event-driven core, recurring payments, dashboards, and access model are bespoke.</sub>
