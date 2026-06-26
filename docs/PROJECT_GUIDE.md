# LedgerFlow — The Complete Guide (Basic → Advanced + Interview Prep)

This document explains LedgerFlow from the ground up. If you've never built a payments
system, start at Part 0 and read straight through. Each part builds on the last, uses
concrete examples, and points at the real files in this repo. The final part is a
playbook for **explaining the project in an interview**.

**Contents**
- [Part 0 — What is this, in plain English?](#part-0)
- [Part 1 — Fundamentals you need first](#part-1)
- [Part 2 — The double-entry ledger (the heart)](#part-2)
- [Part 3 — Idempotency: never charge twice](#part-3)
- [Part 4 — Concurrency: never overdraft](#part-4)
- [Part 5 — SAGA + compensation: safe multi-step](#part-5)
- [Part 6 — Event-driven core: outbox → Redis Streams](#part-6)
- [Part 7 — Reconciliation: proving zero drift](#part-7)
- [Part 8 — Recurring payments + the scheduler](#part-8)
- [Part 9 — Security, roles & multi-tenancy](#part-9)
- [Part 10 — Observability](#part-10)
- [Part 11 — The stack & how it fits together](#part-11)
- [Part 12 — How to explain it in an interview](#part-12)

---

<a name="part-0"></a>
## Part 0 — What is this, in plain English?

Imagine the app behind a digital wallet — Venmo, Cash App, a neobank. People put money
in, hold a balance, send money to friends, and pay for things like a coffee subscription.

The hard part is **not** the buttons. It's making sure the money is *always right*:

- If your phone double-taps "Send $50", you must be charged **once**, not twice.
- If two payments hit your account at the same moment, you must never go negative.
- If a transfer fails halfway, money must not vanish or get stuck.
- Other parts of the system (notifications, analytics, fraud checks) need to **react** to
  payments — without slowing the payment down, and without ever missing one.

**LedgerFlow is that money engine.** It's a backend service that records money movements
the way banks do (double-entry bookkeeping) and reacts to them using an event-driven
architecture. The web UI is just a window into it.

---

<a name="part-1"></a>
## Part 1 — Fundamentals you need first

A few building blocks, briefly, so the rest makes sense.

- **Backend / API:** a program that runs on a server and answers requests like
  "transfer $30 from A to B". LedgerFlow's API is built with **FastAPI** (Python). You
  talk to it over HTTP (e.g. `POST /api/v1/ledger/transfers`).
- **Database:** where data lives durably. We use **PostgreSQL**, a relational (SQL)
  database. Data lives in **tables** (rows and columns), like spreadsheets that can
  reference each other.
- **Transaction (database):** a group of writes that either *all* succeed or *all* fail
  together — "atomic". This is different from a *money* transaction; context tells which.
- **Money as integer cents:** we never store `$10.00` as the floating-point number `10.0`,
  because `0.1 + 0.2 != 0.3` in float math and money must be exact. We store **1000**
  (cents). Every amount in the system is an integer.

> 💡 **The single most important idea:** a balance is something you can always *recompute*
> from history, so you never have to trust a stored number. Read on.

---

<a name="part-2"></a>
## Part 2 — The double-entry ledger (the heart)

### The naive way (don't do this)
A beginner stores a `balance` column and does `balance = balance - 30`. Problems: if the
app crashes mid-update, or two requests run at once, or you need to know *why* a balance
is what it is — you're stuck. The number can drift from reality and you can't audit it.

### The accountant's way: double-entry
Every money movement is recorded as **at least two entries** that **sum to zero**: money
*leaves* one account (a negative entry, a "debit") and *enters* another (a positive entry,
a "credit"). Nothing is created or destroyed; it only moves.

An account's balance is then **defined** as the sum of its entries — never stored:

```
balance(account) = SUM(amount_cents) WHERE account_id = account
```

### In this repo
Three tables model it (`backend/app/models.py`):

- `account` — a wallet. Has a name, currency, and an owner. **No balance column.**
- `ledger_transaction` — one money movement (e.g. "transfer"). Carries the idempotency key.
- `ledger_entry` — the immutable, append-only lines. Signed integer cents. A database
  `CHECK (amount_cents <> 0)` forbids zero-amount noise.

### Worked example
Alice deposits **$100**, then transfers **$30** to Bob.

**Deposit $100 to Alice.** Where does the money come from? A special system account
`external:world` represents "outside the system". Deposits move money *from* it:

| transaction | account | amount_cents |
|---|---|---|
| txn-1 (deposit) | external:world | **-10000** |
| txn-1 (deposit) | Alice | **+10000** |

Sum of txn-1's entries = 0. ✅ Alice's balance = `SUM` = `+10000` = $100.

**Transfer $30 Alice → Bob:**

| transaction | account | amount_cents |
|---|---|---|
| txn-2 (transfer) | Alice | **-3000** |
| txn-2 (transfer) | Bob | **+3000** |

Now: Alice = `10000 - 3000` = **$70**, Bob = **$30**, external:world = **-$100**.

Add up **every entry in the whole system**: `-10000 + 10000 - 3000 + 3000 = 0`. This is
the global invariant — the entire ledger always nets to zero. The negative balance of
`external:world` is exactly the total money that has entered the system. (Part 7 turns
this into a live "zero-drift" proof.)

### The code
`_post_double_entry()` in `backend/app/ledger.py` is the one function that writes money.
It: validates the amount is positive, locks the accounts (Part 4), checks funds, inserts
the two signed entries (asserting they sum to 0), and writes an outbox event (Part 6) —
all in **one DB transaction**. `deposit()` and `transfer()` are thin wrappers around it.

> **Why immutable?** Entries are never updated or deleted. To "reverse" a payment you post
> a new, opposite transaction. This gives you a complete, tamper-evident audit trail —
> exactly what auditors and regulators expect from a real ledger.

---

<a name="part-3"></a>
## Part 3 — Idempotency: never charge twice

### The problem
Networks are unreliable. A client sends "transfer $30", the server does it, but the
*response* gets lost. The client retries. Without protection, you've now transferred $60.

### The solution: idempotency keys
The client attaches a unique **idempotency key** (a UUID) to the request. The server
records it. If a request arrives with a key it has already processed, the server returns
the *original* result instead of doing the work again.

### In this repo
`ledger_transaction.idempotency_key` has a **UNIQUE** database constraint — that
constraint *is* the real guarantee, not application code. `_post_double_entry()`:

1. **Fast path:** looks up the key; if found, returns the existing transaction.
2. **Race path:** if two identical requests slip past the check simultaneously, the second
   `commit()` hits the unique constraint, raises `IntegrityError`, and we catch it and
   return the already-committed transaction.

```python
try:
    session.commit()
except IntegrityError:           # someone else committed the same key first
    session.rollback()
    return session.exec(select(Transaction)
        .where(Transaction.idempotency_key == idempotency_key)).first()
```

The HTTP layer takes the key from an `Idempotency-Key` header
(`POST /api/v1/ledger/transfers`). The frontend generates one with `crypto.randomUUID()`
per submit.

**Test:** `test_idempotent_transfer_does_not_double_charge` sends the same transfer twice
and asserts only one transaction exists and the balance moved once.

---

<a name="part-4"></a>
## Part 4 — Concurrency: never overdraft

### The problem (a race condition)
Alice has $100. Two transfers of $80 arrive at the exact same time. Both read "balance =
$100", both think "plenty of funds", both commit. Alice is now at **-$60**. Catastrophe.

### The solution: row locks
Before checking funds, we **lock** the involved account rows with `SELECT … FOR UPDATE`.
The second transaction must *wait* until the first finishes and commits. When it finally
reads the balance, it sees the updated $20 and correctly rejects the $80 transfer.

### Deadlock safety
If transfer X locks A then B, and transfer Y locks B then A, they can deadlock forever.
We prevent this by **always locking in a stable order** (sorted by account id):

```python
for acc_id in sorted([debit_id, credit_id], key=str):
    session.exec(select(Account).where(Account.id == acc_id).with_for_update()).first()
```

Everyone grabs locks in the same order, so a cycle can't form.

**Test:** `test_concurrent_transfers_never_overdraft` uses a `ThreadPoolExecutor` to fire
5 simultaneous transfers of $30 against a $100 balance and asserts **exactly 3 succeed**
and the final balance is **$10** — proving the lock + funds check hold under real
concurrency.

---

<a name="part-5"></a>
## Part 5 — SAGA + compensation: safe multi-step

### The problem
Some money flows have multiple steps that can't all be one atomic DB transaction — e.g.
hold a buyer's funds now, capture them after the seller ships, or refund if shipping
fails. If step 2 fails after step 1 succeeded, you must **undo** step 1.

### The pattern: SAGA
A SAGA is a sequence of local transactions where each step has a **compensating action**
that semantically undoes it. Instead of one big lock, you move forward step by step and,
on failure, run the compensations backward.

### In this repo
`saga_transfer()` models a reserve → capture flow using a system `holds:system` account:

1. **Reserve:** transfer sender → `holds:system` (key `…:reserve`).
2. **Capture:** transfer `holds:system` → recipient (key `…:capture`).
3. **Compensation:** if capture fails, transfer `holds:system` → sender (key
   `…:compensate`), releasing the hold.

Each step is its own idempotent, committed transaction (note the `:reserve` / `:capture`
/ `:compensate` idempotency-key suffixes). So a partial failure rolls back cleanly and the
ledger never drifts.

```python
transfer(... to=holds,     key=f"{key}:reserve")
try:
    if fail_at == "capture": raise RuntimeError("injected failure")
    transfer(... from=holds, to=recipient, key=f"{key}:capture")
    return {"status": "posted"}
except Exception:
    transfer(... from=holds, to=sender, key=f"{key}:compensate")  # undo
    return {"status": "compensated"}
```

You can trigger the failure path live: `POST /api/v1/ledger/saga-transfer?fail_at=capture`
and watch the sender's balance get restored.

> **SAGA vs. two-phase commit (2PC):** 2PC locks all resources until everyone agrees —
> simple but it doesn't scale and blocks under failure. SAGAs are *eventually consistent*:
> they never hold global locks, they just compensate. That's the model real payment and
> microservice systems use.

---

<a name="part-6"></a>
## Part 6 — Event-driven core: outbox → Redis Streams

### The problem: the dual-write
After a transfer, other things must happen: send a receipt, update analytics, run fraud
checks. The naive approach writes the transfer to the DB *and then* publishes a message to
a queue. But these are two separate systems — if the app crashes between them, you either
**lost the event** (DB committed, publish didn't) or **published a lie** (publish
succeeded, DB rolled back). This is the **dual-write problem**.

### The solution: the transactional outbox
Write the event into an `outbox_event` table **in the same DB transaction** as the ledger
entries. Now it's all-or-nothing: you cannot commit a transfer without its event, or
vice-versa. A separate **relay** process later reads pending outbox rows and publishes
them, marking each `published`.

```
POST /transfers
  └─ ledger entries + outbox_event(status=pending)     ← one atomic DB commit
        └─ relay polls pending rows every 2s
              └─ publish to Redis Stream "ledger.events"
                    └─ mark outbox row published
```

### Redis Streams + consumer groups
The relay publishes to a Redis **Stream** (`ledger.events`) — a durable, ordered,
replayable log (think a tiny Kafka). An **event worker** reads from it via a **consumer
group** (`ledger-workers`):

- `XADD` appends an event; `XREADGROUP` reads new ones; `XACK` acknowledges after success.
- The worker **acks only after handling succeeds**. If it crashes mid-handle, the event
  isn't acked and is redelivered — **at-least-once** delivery.

### At-least-once ⇒ make consumers idempotent
At-least-once means an event can be delivered more than once, so consumers must be
**idempotent** (handling the same event twice has no extra effect). This is the same
discipline as Part 3, applied to the consumer side.

### In this repo
- `backend/app/models.py` → `OutboxEvent` (payload stored as JSONB).
- `backend/app/outbox_relay.py` → the relay loop (and, see Part 8, the scheduler).
- `backend/app/core/eventbus.py` → Redis Stream helpers (`ensure_group`, `publish`).
- `backend/app/event_worker.py` → the consumer-group worker.
- The **Events** page in the UI visualizes this exact pipeline and shows each event flip
  from `pending` to `published` live; the **Operations** page shows Redis stream length,
  events delivered, and consumer lag (from `XINFO GROUPS`).

> **Why not just call the other services directly from the API?** Because that couples
> them: if the notification service is down, the payment fails. Events decouple them in
> *time* — the payment commits instantly; reactions happen when consumers are ready.

---

<a name="part-7"></a>
## Part 7 — Reconciliation: proving zero drift

Trust, but verify. A background **reconciliation** (`backend/app/reconciliation.py`)
independently re-proves the invariants:

- **Global:** `SUM(amount_cents)` over *all* entries must equal `0`. If it isn't, money was
  created or destroyed somewhere — a "drift".
- **Per transaction:** each transaction's entries must sum to `0`.

`reconcile()` returns `{global_drift_cents, unbalanced_transactions, balanced}`. The
Operations dashboard renders this as a big green **"BALANCED — zero drift"** badge, and
the Prometheus endpoint exposes `ledger_drift_cents` as a gauge you could alert on. In a
real company this job runs on a schedule and pages someone if drift ever ≠ 0.

---

<a name="part-8"></a>
## Part 8 — Recurring payments + the scheduler

A **standing order** (`recurring_transfer` table) says "move $X from A to B every N
seconds" — think subscriptions or payroll. Fields: `from`/`to`, `amount_cents`,
`interval_seconds`, `active`, `runs`, `next_run_at`.

The clever bit: we didn't add a 4th moving part (like Celery Beat). The **outbox relay
loop doubles as the scheduler**. Each ~2s tick it also calls `run_due_recurring()`, which:

- selects active orders whose `next_run_at` has passed,
- runs a normal idempotent `transfer()` with key `rec:{id}:{runs}` (so a crash-and-retry
  never double-pays a cycle),
- increments `runs` and sets the next `next_run_at`,
- auto-deactivates an order if it hits `InsufficientFunds` (runs dry).

Because each run emits an outbox event, recurring payments show up live in both the
activity feed and the event log — the app visibly "moves money on its own".

---

<a name="part-9"></a>
## Part 9 — Security, roles & multi-tenancy

- **Authentication:** JWT bearer tokens (`POST /login/access-token`). The frontend stores
  the token and sends `Authorization: Bearer …`.
- **Tenant isolation / IDOR:** every account-scoped endpoint checks ownership via
  `_owned()` — a non-superuser can only read or move money in **their own** accounts
  (superusers bypass for the demo). Without this, anyone could pass another user's account
  id and touch their money (an *Insecure Direct Object Reference*).
- **Role-based access:** system-wide operator views (`/events`, `/stream-info`,
  `/demo-seed`) are **superuser-only** (`_require_admin`). Per-user feeds (`/transactions`,
  `/recurring`) are **scoped** to the caller's own accounts, so customers never see another
  tenant's data.
- **Abuse limits:** list endpoints cap `limit` (`Query(le=200)`) so a caller can't request
  an unbounded scan; production refuses default (`changethis`) secrets.

These were added in response to automated security review findings — closing
cross-tenant disclosure and IDOR.

---

<a name="part-10"></a>
## Part 10 — Observability

- **Prometheus metrics** at `/api/v1/ledger/metrics` (plain-text exposition): transactions,
  accounts, volume, `ledger_drift_cents`, outbox pending. Scrapeable by Prometheus/Grafana.
- **Operations dashboard:** the above as live tiles + the drift gauge + Redis stream health
  (length, delivered, consumer lag) from `XINFO GROUPS`.
- **Events dashboard:** the pipeline diagram + the live outbox event log.

Observability is a first-class feature here precisely because in payments you must be able
to *prove* the system is healthy, not just hope it is.

---

<a name="part-11"></a>
## Part 11 — The stack & how it fits together

| Layer | Choice | Why |
|---|---|---|
| API | **FastAPI** (Python 3.14) | async, typed, auto OpenAPI docs |
| ORM / models | **SQLModel** over SQLAlchemy | Pydantic + SQLAlchemy in one model |
| DB | **PostgreSQL** | ACID, `SELECT … FOR UPDATE`, strong constraints |
| Migrations | **Alembic** | versioned schema changes |
| Event bus | **Redis Streams** | durable, ordered, consumer groups, lightweight |
| Packaging | **uv** | fast, lockfile-based Python deps |
| Frontend | **React 19 + Vite + TanStack Router + Tailwind** | modern SPA |
| Local infra | **Docker Compose** | db + redis + backend + relay + worker + frontend |
| CI | **GitHub Actions** | runs the test suite + frontend build on every push |
| Hosting | **Render** | managed Postgres + Redis + Docker + static site |

**Request lifecycle of a transfer:** React `fetch` → FastAPI route → `_owned()` ownership
check → `ledger.transfer()` → `_post_double_entry()` (lock, funds check, two entries +
outbox event, one commit) → response. Asynchronously: relay → Redis Stream → worker.

**Local topology:** the relay and worker run as their own Compose services. **Free-tier
Render:** they run in-process via `RUN_EMBEDDED_WORKERS` (see `app/embedded_workers.py`)
because the free plan has no standalone Background Worker. Same code, two topologies.

---

<a name="part-12"></a>
## Part 12 — How to explain it in an interview

### The 30-second pitch
> "LedgerFlow is a payment-ledger backend — the money engine behind a digital wallet. It
> records every movement as immutable double-entry bookkeeping in Postgres, so balances
> are derived and provably always balance. It handles the things that actually break
> payment systems: idempotency keys so retries never double-charge, row locking so
> concurrent transfers can't overdraft, SAGAs with compensation for multi-step flows, and
> a transactional-outbox → Redis-Streams pipeline so domain events are never lost. A
> reconciliation job proves the whole ledger nets to zero. It's deployed on Render with
> CI."

### A clean whiteboard story (if asked to design it)
1. "I'd never store a balance column — I'd store immutable signed entries and derive
   balance as their sum. That's double-entry; it's auditable and can't silently drift."
2. "Money is integer cents to avoid float errors."
3. "Writes go through one function that locks the accounts in a fixed order, checks funds,
   and posts two entries summing to zero — atomic."
4. "Idempotency key with a unique constraint makes retries safe."
5. "For events I use the outbox pattern to dodge the dual-write problem, then a relay to
   Redis Streams with a consumer group for at-least-once delivery."
6. "A reconciliation job and Prometheus metrics let me *prove* it's healthy."

### Likely questions & crisp answers
- **"Why double-entry instead of a balance column?"** Auditability and correctness: history
  is the source of truth, balance is derived, and the books must net to zero — so any bug
  shows up as drift instead of silent corruption.
- **"How do you prevent double-charges?"** Idempotency key with a *unique DB constraint* —
  the database enforces it, not app code. Retries return the original transaction; a race
  is caught via `IntegrityError`.
- **"Two transfers hit the same account at once — overdraft?"** No. `SELECT … FOR UPDATE`
  serializes them; the second sees the updated balance. Locks are taken in sorted account
  order to avoid deadlocks. I have a concurrency test that proves exactly 3 of 5 racing
  transfers succeed.
- **"What's the dual-write problem and how do you solve it?"** Writing to the DB and a
  message broker separately can lose or fabricate events on a crash. The transactional
  outbox writes the event in the same DB transaction, and a relay publishes it later —
  all-or-nothing.
- **"At-least-once means duplicates — how do you cope?"** Idempotent consumers; acks only
  after successful handling so a crash redelivers rather than drops.
- **"What is a SAGA and when do you use it?"** A sequence of local transactions each with a
  compensating undo, for multi-step flows you can't make one atomic transaction.
  Eventually consistent, no global locks — unlike 2PC.
- **"How would this scale?"** Partition accounts (shard by account id) so locks stay local;
  replace polling relay with logical-decoding CDC (e.g. Debezium) and Kafka; run the worker
  as an autoscaled fleet; add read replicas for dashboards; cache hot balances with the
  ledger still the source of truth.

### Honest trade-offs to volunteer (shows seniority)
- The relay **polls** every 2s — simple and reliable, but adds latency; at scale I'd move
  to CDC off the WAL.
- Deriving balance as `SUM(entries)` is correct but gets slow for hot accounts; I'd add
  periodic balance **snapshots** and sum only entries since the last snapshot.
- On free-tier hosting the relay/worker run **in-process**; that's a deployment
  convenience, not the production topology — they belong in separate services.
- `holds:system` / `external:world` are modeled as accounts, which keeps the zero-sum
  invariant clean and uniform.

### What to emphasize about *you*
You didn't just wire a CRUD app — you reasoned about **correctness under failure and
concurrency**, chose patterns real fintechs use (double-entry, idempotency, outbox, SAGA,
reconciliation), made the architecture **observable**, and **deployed** it with CI. That's
backend/distributed-systems thinking, not just feature plumbing.

---

*See also: [`README.md`](../README.md) for the quick tour and [`DEPLOY.md`](../DEPLOY.md) for hosting.*
