# LedgerFlow — The Complete Guide (Basic → Advanced + Interview Prep)

This document explains LedgerFlow from the ground up. If you've never built a payments
system, start at Part 0 and read straight through. Each part builds on the last, uses
concrete examples, and points at the real files in this repo. Part 17 is a **decision
log** (what we use, the alternatives, and why we rejected them) and Part 18 is a playbook
for **explaining the project in an interview**.

**Contents**
- [Part 0 — What is this, in plain English?](#part-0)
- [Part 1 — Fundamentals you need first](#part-1)
- [Part 2 — Architecture at a glance (diagram)](#part-2)
- [Part 3 — The double-entry ledger (the heart)](#part-3)
- [Part 4 — Money & multi-currency](#part-4)
- [Part 5 — Idempotency: never charge twice](#part-5)
- [Part 6 — Concurrency: never overdraft](#part-6)
- [Part 7 — SAGA + compensation: safe multi-step](#part-7)
- [Part 8 — Event-driven core: the transactional outbox](#part-8)
- [Part 9 — Reliable consumers: at-least-once, DLQ, exactly-once](#part-9)
- [Part 10 — CQRS read-model projection](#part-10)
- [Part 11 — Reconciliation: proving zero drift](#part-11)
- [Part 12 — Recurring payments + the scheduler](#part-12)
- [Part 13 — Statements & CSV export](#part-13)
- [Part 14 — Security, roles & multi-tenancy](#part-14)
- [Part 15 — Observability](#part-15)
- [Part 16 — The stack & how it fits together](#part-16)
- [Part 17 — Decision log: what we use, alternatives, why not](#part-17)
- [Part 18 — How to explain it in an interview](#part-18)

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
  database. Data lives in **tables** (rows and columns) that can reference each other.
- **DB transaction:** a group of writes that either *all* succeed or *all* fail together —
  "atomic". Different from a *money* transaction; context tells which.
- **Money as integer cents:** we never store `$10.00` as the float `10.0`, because
  `0.1 + 0.2 != 0.3` in float math and money must be exact. We store **1000** (cents).
- **Event:** a fact that already happened ("transfer.posted"). Other code reacts to events
  *after* the fact, instead of being called *during* the payment.

> 💡 **The single most important idea:** a balance is something you can always *recompute*
> from history, so you never have to trust a stored number. Everything else follows.

---

<a name="part-2"></a>
## Part 2 — Architecture at a glance (diagram)

The whole system is really just **four parts**. Don't worry about the names yet:

```
   YOU (the web app)
        │  tap: "Send Bob $30"
        ▼
   ┌──────────────┐   saves it in ONE safe write    ┌──────────────────────────┐
   │ FastAPI (API)│ ───────────────────────────────▶│        PostgreSQL         │
   │  moves money │ ◀──────────  "done!"  ───────────│  the books: every money   │
   └──────────────┘     (instant reply to you)        │  entry + who has what     │
                                                       └─────────────┬────────────┘
                                                                     │ the same save also
                                                                     │ leaves a note:
                                                                     │ "a transfer happened"
   ── background (you don't wait for this) ────────────────────────────┤
                                                                     ▼
          ┌────────┐  reads the note  ┌────────┐  hands it over  ┌────────┐
          │ Relay  │ ────────────────▶│ Redis  │ ───────────────▶│ Worker │
          │postman │                   │mailbox │                 │reactor │
          └────────┘                   └────────┘                 └────────┘
                                                   updates a fast "cached" balance,
                                                   could send a receipt, etc.
```

**The four parts, in plain words:**
- **FastAPI** = the front desk. Takes your request, moves the money, replies right away.
- **PostgreSQL** = the books. The one source of truth — every entry, kept forever.
- **Relay → Redis → Worker** = the back office. *After* the money is safely saved, they do
  the slower follow-up (update a quick-view balance, send receipts) so you never wait.

> **Why split it in two?** Moving money must be instant and perfect. *Reacting* to it
> (receipts, stats) can happen a second later. Keeping them separate means a slow or broken
> receipt service can never break — or even slow down — a payment.

### A concrete example: Alice sends Bob $30
1. **Alice taps "Send $30 to Bob."** The app calls `POST /ledger/transfers`.
2. **FastAPI checks the basics:** Is this really Alice's account? Same currency? Does she
   actually have $30?
3. **One safe save to PostgreSQL** records *two* lines that cancel out — **−$30 from Alice**
   and **+$30 to Bob** (money only *moved*, none was created) — *and*, in the exact same
   save, a note: "transfer happened".
4. **FastAPI replies "done"** to Alice instantly. For her, the payment is finished.
5. **In the background**, the Relay spots the note, drops it in Redis (the mailbox), and the
   Worker picks it up and updates Bob's quick-view balance (and could email a receipt).
6. **Every 30 seconds**, a checker re-adds *all* the lines in the books to confirm they
   still total exactly zero — living proof that no money was lost or invented.

Everything else in this guide is just *how* each step is made bullet-proof — so retries
can't double-charge, two payments can't overdraft, a note is never lost, and so on.

**One codebase, two setups:** locally the Relay and Worker run as separate programs; on the
free hosting tier they run inside the API process. Same code either way.

---

<a name="part-3"></a>
## Part 3 — The double-entry ledger (the heart)

### The naive way (don't do this)
A beginner stores a `balance` column and does `balance = balance - 30`. Problems: if the
app crashes mid-update, or two requests run at once, or you need to know *why* a balance
is what it is — you're stuck. The number can drift from reality and you can't audit it.

### The accountant's way: double-entry
Every money movement is recorded as **at least two entries** that **sum to zero**: money
*leaves* one account (a negative "debit") and *enters* another (a positive "credit").
Nothing is created or destroyed; it only moves. An account's balance is **defined** as the
sum of its entries — never stored:

```
balance(account) = SUM(amount_cents) WHERE account_id = account
```

### In this repo
Tables in `backend/app/models.py`:

- `account` — a wallet (name, currency, owner). **No balance column.**
- `ledger_transaction` — one money movement; carries the idempotency key + status.
- `ledger_entry` — immutable, append-only lines. Signed integer cents. A
  `CHECK (amount_cents <> 0)` forbids zero-amount noise.

### Worked example
Alice deposits **$100**, then transfers **$30** to Bob. Deposits draw from a system
account `external:world` that represents "outside the system":

| transaction | account | amount_cents |
|---|---|---|
| txn-1 (deposit) | external:world | **-10000** |
| txn-1 (deposit) | Alice | **+10000** |
| txn-2 (transfer) | Alice | **-3000** |
| txn-2 (transfer) | Bob | **+3000** |

Alice = `10000 - 3000` = **$70**, Bob = **$30**, external:world = **-$100**. Add up *every*
entry: `-10000 + 10000 - 3000 + 3000 = 0`. That global invariant — the whole ledger always
nets to zero — is what Part 11 turns into a live "zero-drift" proof. The negative balance
of `external:world` is exactly the money that has entered the system.

### The code
`_post_double_entry()` in `backend/app/ledger.py` is the one function that writes money. It
validates the amount, locks the accounts (Part 6), checks the currency (Part 4) and funds,
inserts the two signed entries (asserting they sum to 0), and writes an outbox event
(Part 8) — all in **one DB transaction**. `deposit()` and `transfer()` are thin wrappers.

> **Why immutable?** Entries are never updated or deleted. To "reverse" a payment you post
> a new, opposite transaction. That gives a complete, tamper-evident audit trail — exactly
> what auditors and regulators expect from a real ledger.

---

<a name="part-4"></a>
## Part 4 — Money & multi-currency

**Integer cents.** Every amount is an `int` of minor units (`BigInteger` in the DB). No
floats anywhere — float rounding (`0.1 + 0.2`) silently corrupts money.

**One currency per transaction.** A transaction's two legs must be the **same currency**.
`_post_double_entry()` loads both (already-locked) accounts and rejects a mismatch with
`CurrencyMismatch`:

```python
if locked[debit_id].currency != locked[credit_id].currency:
    raise CurrencyMismatch(...)        # a -500 USD / +500 EUR pair "sums to 0" but is nonsense
```

This matters because the zero-sum invariant is *numeric*: without the guard, `-500 USD`
and `+500 EUR` would "balance" while actually moving value across incompatible units.

**Per-currency system accounts.** Deposits draw from a per-currency funding account —
`external:world` for USD, `external:world:EUR` for EUR, etc. — so both legs are always the
same currency and the books net to zero **within each currency**.

**Per-currency reconciliation.** Drift is checked **grouped by currency** (Part 11), not as
one global cents-sum (which could hide `+100 USD` cancelling `-100 EUR`).

**FX (the natural next step, not yet built):** a cross-currency transfer would be a
*four-leg* posting — debit USD + credit `external:world:USD`, then debit
`external:world:EUR` + credit EUR — at an agreed rate, each currency still summing to zero.

---

<a name="part-5"></a>
## Part 5 — Idempotency: never charge twice

### The problem
Networks are unreliable. A client sends "transfer $30", the server does it, but the
*response* is lost. The client retries. Without protection you've transferred $60.

### The solution: idempotency keys
The client attaches a unique **idempotency key** (UUID) to the request. The server records
it; a repeat key returns the *original* result instead of doing the work again.

### In this repo
`ledger_transaction.idempotency_key` has a **UNIQUE** database constraint — that constraint
*is* the guarantee, not application code. `_post_double_entry()`:

1. **Fast path:** look up the key; if found, return the existing transaction.
2. **Race path:** if two identical requests slip past the check at once, the second
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

The HTTP layer reads the key from an `Idempotency-Key` header; the frontend generates one
with `crypto.randomUUID()` per submit. **Tests:** `test_idempotent_transfer_does_not_double_charge`
(HTTP) and `test_idempotency_key_replays_same_transaction` (service) prove a repeat returns
the same transaction and the balance moves once.

---

<a name="part-6"></a>
## Part 6 — Concurrency: never overdraft

### The race condition
Alice has $100. Two transfers of $80 arrive simultaneously. Both read "balance = $100",
both think "plenty", both commit. Alice is at **-$60**. Catastrophe.

### Row locks
Before checking funds we **lock** the involved account rows with `SELECT … FOR UPDATE`. The
second transaction *waits* until the first commits, then reads the updated $20 and
correctly rejects the $80.

### Deadlock safety
If X locks A→B and Y locks B→A, they deadlock. We prevent it by **always locking in a
stable order** (sorted by account id), so a cycle can't form:

```python
for acc_id in sorted([debit_id, credit_id], key=str):
    session.exec(select(Account).where(Account.id == acc_id).with_for_update()).first()
```

**Test:** `test_concurrent_transfers_never_overdraft` fires 5 simultaneous $30 transfers at
a $100 balance and asserts **exactly 3 succeed**, final balance **$10** — proving the lock
+ funds check hold under real concurrency.

---

<a name="part-7"></a>
## Part 7 — SAGA + compensation: safe multi-step

### The problem
Some flows have multiple steps that can't be one atomic DB transaction — hold a buyer's
funds now, capture after the seller ships, refund if shipping fails. If step 2 fails after
step 1 succeeded, you must **undo** step 1.

### The pattern
A **SAGA** is a sequence of local transactions, each with a **compensating action** that
semantically undoes it. On failure you run the compensations backward — no global locks.

### In this repo
`saga_transfer()` models reserve → capture using a system `holds:system` account:

1. **Reserve:** sender → `holds:system` (key `…:reserve`).
2. **Capture:** `holds:system` → recipient (key `…:capture`).
3. **Compensate:** if capture fails, `holds:system` → sender (key `…:compensate`).

Each step is its own idempotent, committed transaction. Trigger the failure path live:
`POST /api/v1/ledger/saga-transfer?fail_at=capture` and watch the sender's balance get
restored. **Test:** `test_saga_compensation_restores_sender`.

> **SAGA vs two-phase commit (2PC):** 2PC locks all resources until everyone agrees —
> simple, but it blocks under failure and doesn't scale. SAGAs are *eventually consistent*:
> no global locks, just compensation. That's what real payment/microservice systems use.

---

<a name="part-8"></a>
## Part 8 — Event-driven core: the transactional outbox

### The dual-write problem
After a transfer, other things must happen: receipts, analytics, fraud checks. The naive
approach writes the transfer to the DB *and then* publishes to a queue. Two separate
systems → if the app crashes between them, you either **lost the event** (DB committed,
publish didn't) or **published a lie** (publish succeeded, DB rolled back).

### The transactional outbox
Write the event into `outbox_event` **in the same DB transaction** as the ledger entries.
Now it's all-or-nothing. A separate **relay** later reads pending rows and publishes them.

```
POST /transfers
  └─ ledger entries + outbox_event(status=pending)     ← one atomic DB commit
        └─ relay polls pending rows every 2s  (SELECT … FOR UPDATE SKIP LOCKED)
              └─ XADD to Redis Stream "ledger.events"
                    └─ mark outbox row published
```

`FOR UPDATE SKIP LOCKED` means two relay instances grab *different* rows instead of
double-publishing the same one — the canonical multi-instance outbox pattern.

### Files
- `models.py` → `OutboxEvent` (payload as JSONB).
- `outbox_relay.py` → the relay loop (+ scheduler + reconcile tick).
- `core/eventbus.py` → Redis Stream helpers (`ensure_group`, `publish`).
- The **Events** page shows each event flip `pending → published` live; **Operations**
  shows stream length / delivered / consumer lag from `XINFO GROUPS`.

> **Why not call the other services directly from the API?** That couples them: if the
> notification service is down, the payment fails. Events decouple them in *time* — the
> payment commits instantly; reactions happen when consumers are ready.

---

<a name="part-9"></a>
## Part 9 — Reliable consumers: at-least-once, DLQ, exactly-once

The relay publishes to a Redis **Stream** (`ledger.events`) — a durable, ordered,
replayable log (a tiny Kafka). An **event worker** reads via a **consumer group**
(`ledger-workers`):

- `XADD` appends; `XREADGROUP` reads new messages; `XACK` acknowledges after success.
- The worker **acks only after handling succeeds** → if it crashes mid-handle the event
  isn't acked and is redelivered. This is **at-least-once** delivery.

### Dead-letter queue (poison messages)
A handler that *always* throws (malformed event, a bug) would never ack and, since we read
only new messages with `">"`, would never retry either — it would silently stall. So the
worker, each loop, first **reclaims stuck pending messages**:

- `XPENDING` finds messages held idle too long and their delivery count.
- `XCLAIM` re-delivers them to this consumer (bumping the count).
- After `MAX_DELIVERIES` failures the message is **poison**: we `XADD` it to a
  **dead-letter stream** and `XACK` the original, so one bad event can never block the
  pipeline. `/ledger/dead-letter` exposes the quarantine; `ledger_dead_letter_total` alerts.

**Test:** `test_poison_message_is_dead_lettered_not_stuck` (against a real isolated Redis
stream) proves a poison message lands in the DLQ and the group's pending list drains.

### At-least-once ⇒ make consumers idempotent
Because an event can arrive twice, consumers must be **idempotent** — see the read-model
dedupe in Part 10. (Same discipline as Part 5, on the consumer side.)

---

<a name="part-10"></a>
## Part 10 — CQRS read-model projection

**CQRS** = Command/Query Responsibility Segregation: the **write model** (immutable ledger
entries) and the **read model** (fast balances) are *different shapes*, kept in sync by
events.

### Why
`balance = SUM(ledger_entry)` is always correct but **O(n)** in an account's history — it
gets slower forever. A materialized `account_balance` table the worker maintains turns
balance reads into O(1) lookups.

### Exactly-once under an at-least-once bus
Naively doing `balance += amount` on each event would **double-count** on redelivery. So
the projection is made idempotent via a `processed_event` dedupe table:

```python
session.add(ProcessedEvent(event_id=event_id))   # the unique PK is the gate
session.flush()                                   # IntegrityError ⇒ already applied ⇒ skip
# else: bump debit (−amount) and credit (+amount) balances, commit atomically
```

The dedupe row + both balance updates commit together, so an event moves the read model
**exactly once**. (`backend/app/projection.py`, wired from `event_worker.handle()`.)

### Observable lag
The authoritative balance stays `SUM(ledger_entry)`; `account_balance` is a cache. The
`/ledger/projection` endpoint reports per-account **`lag_cents` = authoritative − projected**
(must converge to 0). `ledger_projection_max_lag_cents` makes the eventual-consistency
window a metric you can watch. **Tests:** `test_projection_is_idempotent_on_redelivery`,
`test_projection_converges_to_authoritative`.

---

<a name="part-11"></a>
## Part 11 — Reconciliation: proving zero drift

Trust, but verify. `backend/app/reconciliation.py` independently re-proves the invariants:

- **Per-currency drift:** within *each* currency, `SUM(amount_cents)` must equal `0`.
  (A bare global sum could hide `+100 USD` cancelling `−100 EUR`.)
- **Global drift:** `SUM` over all entries (kept for the Prometheus gauge).
- **Per transaction:** every transaction's entries must sum to `0`.

`reconcile()` returns `{global_drift_cents, currency_drift_cents, unbalanced_transactions,
balanced}`. **Scheduled, not just on-demand:** the relay loop calls
`run_scheduled_check()` every 30s, persists the result to `reconciliation_check`, and logs
at ERROR on drift (the hook where real alerting fires). `/ledger/reconciliation/last`
serves the latest; `ledger_reconciliation_ok` is the gauge. The Operations dashboard shows
a green **"BALANCED — zero drift"** badge + "last reconciled Xs ago".

---

<a name="part-12"></a>
## Part 12 — Recurring payments + the scheduler

A **standing order** (`recurring_transfer`) says "move $X from A to B every N seconds" —
subscriptions, payroll. We didn't add a 4th moving part (Celery Beat): the **relay loop
doubles as the scheduler**. Each ~2s tick it calls `run_due_recurring()`, which:

- selects active orders whose `next_run_at` has passed,
- runs an idempotent `transfer()` with key `rec:{id}:{runs}` (a crash-retry never
  double-pays a cycle),
- **advances `next_run_at += interval`** (anchored to the schedule, not to "now"), and
  **catches up** missed intervals (bounded by `MAX_CATCHUP_RUNS`) if the worker was down,
- auto-deactivates an order that hits `InsufficientFunds`.

> **The drift bug we fixed:** the original set `next_run_at = now + interval`, which
> re-anchored every tick — the cadence slowly slid by the poll latency, and a backlog fired
> only *once*. Anchoring to `next_run_at` itself keeps the cadence exact and replays missed
> runs. **Test:** `test_recurring_catches_up_missed_intervals` (35s overdue / 10s interval
> → fires 4 times).

Because each run emits an outbox event, recurring payments appear live in the activity feed
and event log — the app visibly "moves money on its own".

---

<a name="part-13"></a>
## Part 13 — Statements & CSV export

`account_statement()` returns an account's entries **oldest → newest with a running
balance** (ordered by `created_at`, then `id` as a stable tiebreak, so the cumulative sum
is correct). Two endpoints:

- `GET /ledger/accounts/{id}/statement` — JSON (used by the Ledger page's statement panel).
- `GET /ledger/accounts/{id}/statement.csv` — a downloadable CSV (amounts in major units),
  `Content-Disposition: attachment`. The frontend fetches it with the bearer token and
  triggers a Blob download (a raw `href` can't carry the auth header).

Both are tenant-scoped via `_owned`. **Test:** `test_statement_json_and_csv` asserts the
running balance and CSV header/last row.

---

<a name="part-14"></a>
## Part 14 — Security, roles & multi-tenancy

- **Authentication:** JWT bearer tokens (`POST /login/access-token`); the frontend sends
  `Authorization: Bearer …`.
- **Tenant isolation / IDOR:** every account-scoped endpoint checks ownership via
  `_owned()` — a non-superuser can only touch **their own** accounts. Without it, anyone
  could pass another user's account id and move their money (an *Insecure Direct Object
  Reference*).
- **Role-based access:** operator views (`/events`, `/stream-info`, `/projection`,
  `/dead-letter`, `/reconciliation/last`, `/demo-seed`) are **superuser-only**
  (`_require_admin`). Per-user feeds (`/transactions`, `/recurring`) are **scoped** to the
  caller's accounts.
- **Abuse limits:** list endpoints cap `limit` (`Query(le=200)`); production refuses default
  (`changethis`) secrets.

These were added in response to automated security-review findings — closing cross-tenant
disclosure and IDOR.

---

<a name="part-15"></a>
## Part 15 — Observability

- **Prometheus metrics** at `/api/v1/ledger/metrics` (plain-text exposition):
  `ledger_transactions_total`, `ledger_accounts_total`, `ledger_volume_cents_total`,
  `ledger_drift_cents`, `ledger_outbox_pending`, and the ones added with the new features:
  `ledger_events_projected_total`, `ledger_projection_max_lag_cents`,
  `ledger_reconciliation_ok`, `ledger_dead_letter_total`.
- **Operations dashboard:** live tiles for the invariant, the event bus (stream length /
  delivered / consumer lag), the **CQRS read model** (events projected, projection lag,
  dead-letter, last reconcile), and per-currency drift.
- **Events dashboard:** the pipeline diagram + live outbox event log.
- **Swagger / ReDoc:** `/docs` and `/redoc` auto-document every endpoint (linked from the
  sidebar as "API Docs").

Observability is first-class here because in payments you must be able to *prove* the
system is healthy, not just hope it is.

---

<a name="part-16"></a>
## Part 16 — The stack & how it fits together

| Layer | Choice | Why (one line) |
|---|---|---|
| API | **FastAPI** (Python 3.14) | async, typed, auto OpenAPI/Swagger |
| ORM / models | **SQLModel** over SQLAlchemy | Pydantic + SQLAlchemy in one model |
| DB | **PostgreSQL** | ACID, `SELECT … FOR UPDATE [SKIP LOCKED]`, constraints, JSONB |
| Migrations | **Alembic** | versioned, autogenerated schema changes |
| Event bus | **Redis Streams** | durable, ordered, consumer groups, lightweight |
| Read model | **CQRS projection** in Postgres | O(1) balances, idempotent via dedupe table |
| Packaging | **uv** | fast, lockfile-based Python deps |
| Frontend | **React 19 + Vite + TanStack Router + Tailwind** | modern typed SPA |
| Local infra | **Docker Compose** | db + redis + backend + relay + worker + frontend |
| CI | **GitHub Actions** | tests (with Postgres + Redis) + frontend build on every push |
| Hosting | **Render** | managed Postgres + Redis + Docker + static site |

**Request lifecycle of a transfer:** React `fetch` → FastAPI route → `_owned()` ownership
check → `ledger.transfer()` → `_post_double_entry()` (lock → currency → funds → two entries
+ outbox event, one commit) → response. Asynchronously: relay → Redis Stream → worker →
read-model projection.

---

<a name="part-17"></a>
## Part 17 — Decision log: what we use, alternatives, why not

This is the "why this and not that" interviewers love. For each choice: what we picked, the
real alternatives, and the honest reason we didn't use them.

### Ledger model — **immutable double-entry** vs a balance column / full event-sourcing
- **Balance column (`UPDATE balance`)** — simplest, but unauditable and silently corrupts
  under crashes/races. Rejected: correctness is the whole point.
- **Full event-sourcing (rebuild *everything* from an event log)** — powerful but heavy
  (snapshots, replay, versioning). Double-entry gives us the audit trail and derivable
  balances without that machinery. We use a *light* version: immutable entries + a CQRS
  projection.

### Money — **integer cents (BigInteger)** vs float vs Decimal
- **Float** — never, for money (`0.1+0.2≠0.3`). **Decimal** — exact, but slower and
  serialization is fussier across the DB/JSON boundary. **Integer minor units** is the
  industry norm (Stripe does this); arithmetic is exact and trivial.

### Database — **PostgreSQL** vs MySQL / MongoDB / SQLite / CockroachDB
- **MySQL** — fine, but Postgres has stronger constraint/`CHECK` support, `JSONB`, and
  cleaner `SKIP LOCKED`. **MongoDB** — no multi-document ACID guarantees we'd want for
  money historically; relational integrity fits a ledger. **SQLite** — no real concurrency
  (`FOR UPDATE`), dev-only. **CockroachDB** — great for global scale but operational
  overkill here; Postgres scales far enough for this.

### ORM — **SQLModel** vs raw SQLAlchemy / Django ORM / raw SQL
- **Raw SQLAlchemy** — what SQLModel is built on; SQLModel removes the duplicate
  Pydantic-schema/ORM-model boilerplate (one class is both). **Django ORM** — couples you to
  Django; we want a lean FastAPI service. **Raw SQL** — maximal control, but loses typing,
  migrations integration, and safety. We drop to SQLAlchemy Core for the few analytical
  queries (sums, group-by) where it's clearer.

### API framework — **FastAPI** vs Flask / Django REST / Node(Express) / Spring
- **Flask** — minimal, but you bolt on validation/async/OpenAPI yourself. **Django REST** —
  batteries-included but heavyweight; ORM/admin we don't need. **Node/Express** — fine, but
  Python's typing + Pydantic validation + auto Swagger is a strong fit and matches the
  data/ML-adjacent ecosystem. **Spring Boot** — excellent for banks, heavier to iterate on
  solo. FastAPI gives async, types, and free interactive docs.

### Event bus — **Redis Streams** vs Kafka / RabbitMQ / SQS / NATS / Postgres LISTEN-NOTIFY
- **Kafka** — the "real" answer at scale (partitions, retention, throughput), but heavy to
  run and overkill for a portfolio/free tier. Redis Streams gives the same *concepts*
  (append-only log, consumer groups, acks, replay) at a fraction of the ops cost.
- **RabbitMQ** — great queue, but classic AMQP is push/ack with weaker replayability than a
  log; Streams' log model maps better to event-sourcing. **SQS/SNS** — managed and easy, but
  cloud-locked and no local story. **Postgres LISTEN/NOTIFY** — no durability/replay (a
  dropped notification is gone). **NATS JetStream** — close to Streams; Redis was already in
  the stack. The outbox pattern means the bus is swappable — we could put Kafka behind the
  same relay interface without touching the ledger.

### Event reliability — **transactional outbox** vs direct publish / CDC (Debezium) / 2PC
- **Direct publish** (write DB, then publish) — the dual-write bug (lost/lying events).
  Rejected. **CDC / logical decoding (Debezium)** — the *production-grade* evolution: tail
  the WAL instead of polling. Better latency, but needs Kafka Connect + infra. The outbox is
  the same guarantee with a simple polling relay; the doc explicitly calls CDC the next step.
  **2PC/XA across DB+broker** — distributed locks, poor failure behavior, rarely worth it.

### Consumer delivery — **at-least-once + idempotent consumers** vs exactly-once transport
- True exactly-once *delivery* is largely a myth across systems; the practical pattern is
  at-least-once delivery + **idempotent processing** (our `processed_event` dedupe), which
  yields exactly-once *effect*. That's what we do.

### Read model — **CQRS projection** vs always `SUM` / DB materialized view / cached balance
- **Always `SUM(entries)`** — correct but O(n); we keep it as the *authoritative* check.
  **Postgres materialized view** — must be `REFRESH`ed (stale or expensive); doesn't react
  to events incrementally. **Cache (Redis) balance** — fast but another dual-write to keep
  consistent. The event-driven projection updates incrementally and idempotently, and we
  *measure* its lag against the source of truth.

### Distributed transaction — **SAGA + compensation** vs 2PC
- See Part 7: 2PC blocks and doesn't scale; SAGA is eventually consistent with no global
  locks — the microservice/payments standard.

### Scheduler — **embedded relay tick** vs Celery Beat / cron / APScheduler / Temporal
- **Celery Beat / APScheduler** — real schedulers, but another process/broker to run and
  monitor. **OS cron** — outside the app, can't share the DB session/logic cleanly.
  **Temporal** — durable workflows, fantastic but heavy. For "run due transfers every tick",
  folding it into the relay loop is simplest and has one fewer moving part. (Trade-off: it's
  coupled to the relay's cadence — fine here, I'd split it out at scale.)

### Concurrency control — **pessimistic `FOR UPDATE`** vs optimistic (version column)
- **Optimistic locking** (retry on version conflict) shines under low contention. For
  money-movement on the *same* account, conflicts are exactly the hot path, so pessimistic
  row locks (taken in sorted order to avoid deadlock) are simpler and predictable.

### Packaging — **uv** vs pip / Poetry / Pipenv / conda
- `uv` is dramatically faster, has a lockfile, and manages the Python version itself. Poetry
  is the closest alternative but slower; pip alone has no lockfile. conda is for scientific
  stacks we don't need.

### Frontend — **React 19 + Vite + TanStack Router** vs Next.js / CRA / Vue / Svelte
- **Next.js** — SSR/routing/server components, but this is a pure dashboard SPA talking to a
  separate API; SSR adds complexity for no benefit. **CRA** — deprecated/slow vs Vite.
  **Vue/Svelte** — fine, but React has the deepest ecosystem and is most interview-relevant.

### Auth — **JWT bearer** vs server sessions / OAuth provider
- **Sessions/cookies** need server-side session storage and CSRF handling; JWTs are
  stateless and simple for an API + SPA. A real bank would add an OAuth/OIDC provider and
  short-lived tokens + refresh; out of scope for the demo.

### Hosting — **Render** vs AWS (ECS/Fargate) / Fly.io / Heroku / Kubernetes
- **AWS/k8s** — production-grade and what you'd use at scale, but heavy setup for a
  portfolio. **Render** gives managed Postgres + Redis + Docker + a static site with a
  blueprint file, free tier, and auto-deploy on push. The free tier's lack of a background
  worker is exactly why we support the embedded-workers topology.

---

<a name="part-18"></a>
## Part 18 — How to explain it in an interview

### The 30-second pitch
> "LedgerFlow is a payment-ledger backend — the money engine behind a digital wallet. It
> records every movement as immutable double-entry bookkeeping in Postgres, so balances are
> derived and provably always balance. It handles what actually breaks payment systems:
> idempotency keys so retries never double-charge, row locking so concurrent transfers can't
> overdraft, SAGAs with compensation for multi-step flows, and a transactional-outbox →
> Redis-Streams pipeline so events are never lost. Downstream, an idempotent CQRS projection
> maintains fast balances, a dead-letter queue isolates poison events, and a scheduled
> reconciliation job proves the ledger nets to zero. Deployed on Render with CI."

### A clean whiteboard story (if asked to design it)
1. "Never a balance column — store immutable signed entries, derive balance as their sum.
   Double-entry: auditable, can't silently drift."
2. "Money is integer cents; one currency per transaction."
3. "One write function: lock accounts in a fixed order → check currency + funds → post two
   entries summing to zero → write an outbox event — all in one commit."
4. "Idempotency key with a unique constraint makes retries safe."
5. "Outbox pattern dodges the dual-write problem; a relay publishes to Redis Streams; a
   consumer group gives at-least-once delivery."
6. "Consumers are idempotent (dedupe table) → a CQRS projection keeps O(1) balances exactly
   once; poison messages go to a dead-letter queue."
7. "A scheduled reconciliation job + Prometheus metrics let me *prove* it's healthy."

### Likely questions & crisp answers
- **"Why double-entry not a balance column?"** History is the source of truth, balance is
  derived, books must net to zero — bugs surface as drift, not silent corruption.
- **"Prevent double-charges?"** Idempotency key with a *unique DB constraint*; retries
  return the original; a race is caught via `IntegrityError`.
- **"Two transfers, same account — overdraft?"** No. `SELECT … FOR UPDATE` serializes them;
  locks taken in sorted order avoid deadlock. Proven by a concurrency test (3 of 5 succeed).
- **"Dual-write problem?"** Writing DB + broker separately can lose/fabricate events on a
  crash. The outbox writes the event in the same transaction; a relay publishes later.
- **"At-least-once means duplicates — how cope?"** Idempotent consumers + a `processed_event`
  dedupe table → exactly-once *effect*. Acks only after success.
- **"What's CQRS here?"** Write model = immutable entries (correct, O(n)); read model =
  materialized `account_balance` the worker maintains (fast, O(1)); I measure its lag vs the
  authoritative sum.
- **"Poison message?"** Reclaimed via `XPENDING`/`XCLAIM`; after N failures it's moved to a
  dead-letter stream and acked so it can't block the pipeline.
- **"What is a SAGA?"** A sequence of local transactions each with a compensating undo, for
  multi-step flows you can't make one atomic transaction. Eventually consistent, no 2PC.
- **"How would this scale?"** Shard accounts by id so locks stay local; replace the polling
  relay with WAL CDC (Debezium) + Kafka; run the worker as an autoscaled fleet; add balance
  **snapshots** so reads/reconciliation don't re-scan history; read replicas for dashboards.

### Honest trade-offs to volunteer (shows seniority)
- The relay **polls** every 2s — simple/reliable but adds latency; at scale → CDC off the WAL.
- Deriving balance as `SUM(entries)` is O(n); the CQRS projection helps reads, and I'd add
  periodic **snapshots** for the authoritative path too.
- On free-tier hosting the relay/worker run **in-process**; that's a deployment convenience,
  not the production topology (they belong in separate services).
- The scheduler is folded into the relay loop — one fewer moving part, but coupled to its
  cadence; I'd split it out (Temporal/Celery Beat) at scale.
- `holds:system` / `external:world` are modeled as accounts, keeping the zero-sum invariant
  uniform.

### What to emphasize about *you*
You didn't wire a CRUD app — you reasoned about **correctness under failure and
concurrency**, chose patterns real fintechs use (double-entry, idempotency, outbox, SAGA,
CQRS, DLQ, reconciliation), made the system **observable and provable**, and **deployed** it
with CI. That's backend/distributed-systems thinking, not feature plumbing.

---

*See also: [`README.md`](../README.md) for the quick tour and [`DEPLOY.md`](../DEPLOY.md) for hosting.*
