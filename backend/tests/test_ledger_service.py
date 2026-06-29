import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, func, select

from app import ledger, projection, reconciliation
from app.core.db import engine
from app.models import AccountBalance, LedgerEntry, OutboxEvent, RecurringTransfer


def test_transfer_entries_sum_to_zero():
    with Session(engine) as s:
        a = ledger.create_account(s, name=f"z-{uuid.uuid4()}")
        b = ledger.create_account(s, name=f"z-{uuid.uuid4()}")
        ledger.deposit(
            s, to_id=a.id, amount_cents=1_000, idempotency_key=str(uuid.uuid4())
        )
        txn = ledger.transfer(
            s,
            from_id=a.id,
            to_id=b.id,
            amount_cents=400,
            idempotency_key=str(uuid.uuid4()),
        )
        entries = s.exec(
            select(LedgerEntry).where(LedgerEntry.transaction_id == txn.id)
        ).all()
        assert len(entries) == 2
        assert sum(e.amount_cents for e in entries) == 0  # debits == credits


def test_global_zero_drift():
    # the whole ledger must always net to zero — the reconciliation invariant
    with Session(engine) as s:
        total = s.exec(
            select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
        ).one()
        assert total == 0


def test_concurrent_transfers_never_overdraft():
    # Fund Alice with exactly 100, then fire 5 concurrent transfers of 30.
    # Row-locking must serialize them: only 3 succeed (3*30=90 <= 100); Alice never goes negative.
    with Session(engine) as s:
        aid = ledger.create_account(s, name=f"c-{uuid.uuid4()}").id
        bid = ledger.create_account(s, name=f"c-{uuid.uuid4()}").id
        ledger.deposit(
            s, to_id=aid, amount_cents=100, idempotency_key=str(uuid.uuid4())
        )

    def attempt(_) -> bool:
        with Session(engine) as s:
            try:
                ledger.transfer(
                    s,
                    from_id=aid,
                    to_id=bid,
                    amount_cents=30,
                    idempotency_key=str(uuid.uuid4()),
                )
                return True
            except ledger.InsufficientFunds:
                return False

    with ThreadPoolExecutor(max_workers=5) as ex:
        successes = sum(ex.map(attempt, range(5)))

    with Session(engine) as s:
        assert successes == 3
        assert ledger.account_balance(s, aid) == 10  # 100 - 90, never negative
        assert ledger.account_balance(s, bid) == 90


def test_idempotency_key_replays_same_transaction():
    # retrying a transfer with the same Idempotency-Key must return the SAME txn and
    # post the money only once — the API replays, it doesn't double-charge.
    with Session(engine) as s:
        a = ledger.create_account(s, name=f"ia-{uuid.uuid4()}")
        b = ledger.create_account(s, name=f"ib-{uuid.uuid4()}")
        ledger.deposit(
            s, to_id=a.id, amount_cents=1_000, idempotency_key=str(uuid.uuid4())
        )
        key = str(uuid.uuid4())
        t1 = ledger.transfer(
            s, from_id=a.id, to_id=b.id, amount_cents=300, idempotency_key=key
        )
        t2 = ledger.transfer(
            s, from_id=a.id, to_id=b.id, amount_cents=300, idempotency_key=key
        )
        assert t1.id == t2.id  # same transaction returned
        assert ledger.account_balance(s, b.id) == 300  # charged once, not twice


def test_account_statement_running_balance():
    # the statement's final running balance must equal the authoritative balance, and
    # each row's running balance must be the cumulative sum up to that entry.
    with Session(engine) as s:
        a = ledger.create_account(s, name=f"st-{uuid.uuid4()}")
        b = ledger.create_account(s, name=f"st-{uuid.uuid4()}")
        ledger.deposit(
            s, to_id=a.id, amount_cents=1_000, idempotency_key=str(uuid.uuid4())
        )
        ledger.transfer(
            s,
            from_id=a.id,
            to_id=b.id,
            amount_cents=400,
            idempotency_key=str(uuid.uuid4()),
        )
        stmt = ledger.account_statement(s, a.id)
        assert [e["amount_cents"] for e in stmt] == [1_000, -400]  # credit then debit
        assert [e["running_balance_cents"] for e in stmt] == [1_000, 600]
        assert stmt[-1]["running_balance_cents"] == ledger.account_balance(s, a.id)


def test_currency_mismatch_rejected():
    # a USD->EUR transfer must be refused even when the sender has the funds:
    # "-500 USD / +500 EUR" sums to zero but is economically meaningless.
    with Session(engine) as s:
        usd = ledger.create_account(s, name=f"usd-{uuid.uuid4()}", currency="USD")
        eur = ledger.create_account(s, name=f"eur-{uuid.uuid4()}", currency="EUR")
        ledger.deposit(
            s, to_id=usd.id, amount_cents=1_000, idempotency_key=str(uuid.uuid4())
        )
        with pytest.raises(ledger.CurrencyMismatch):
            ledger.transfer(
                s,
                from_id=usd.id,
                to_id=eur.id,
                amount_cents=100,
                idempotency_key=str(uuid.uuid4()),
            )
        assert ledger.account_balance(s, usd.id) == 1_000  # untouched


def test_non_usd_deposit_uses_matching_external():
    # depositing to an EUR wallet funds it from a per-currency external account, so
    # both legs are EUR and the currency guard is satisfied; the books stay balanced.
    with Session(engine) as s:
        eur = ledger.create_account(s, name=f"eur-{uuid.uuid4()}", currency="EUR")
        ledger.deposit(
            s, to_id=eur.id, amount_cents=5_000, idempotency_key=str(uuid.uuid4())
        )
        assert ledger.account_balance(s, eur.id) == 5_000
        rep = reconciliation.reconcile(s)
        assert rep["balanced"] is True
        assert all(d == 0 for d in rep["currency_drift_cents"].values())


def test_saga_compensation_restores_sender():
    # a capture failure must roll back via compensation: sender ends whole, recipient gets nothing.
    with Session(engine) as s:
        a = ledger.create_account(s, name=f"sa-{uuid.uuid4()}")
        b = ledger.create_account(s, name=f"sb-{uuid.uuid4()}")
        ledger.deposit(
            s, to_id=a.id, amount_cents=1_000, idempotency_key=str(uuid.uuid4())
        )
        res = ledger.saga_transfer(
            s,
            from_id=a.id,
            to_id=b.id,
            amount_cents=300,
            idempotency_key=str(uuid.uuid4()),
            fail_at="capture",
        )
        assert res["status"] == "compensated"
        assert ledger.account_balance(s, a.id) == 1_000  # reserve then released
        assert ledger.account_balance(s, b.id) == 0  # recipient never funded


def test_recurring_catches_up_missed_intervals():
    # a standing order overdue by several intervals fires once per missed interval
    # (bounded), instead of the old behaviour that fired exactly once and re-anchored.
    with Session(engine) as s:
        a = ledger.create_account(s, name=f"ra-{uuid.uuid4()}")
        b = ledger.create_account(s, name=f"rb-{uuid.uuid4()}")
        ledger.deposit(
            s, to_id=a.id, amount_cents=1_000, idempotency_key=str(uuid.uuid4())
        )
        r = RecurringTransfer(
            from_account_id=a.id,
            to_account_id=b.id,
            amount_cents=10,
            interval_seconds=10,
            next_run_at=datetime.now(UTC) - timedelta(seconds=35),
        )
        s.add(r)
        s.commit()
        s.refresh(r)

        executed = ledger.run_due_recurring(s)
        s.refresh(r)
        assert (
            executed == 4
        )  # 35s overdue / 10s interval -> 4 firings (t-35,-25,-15,-5)
        assert r.runs == 4
        assert r.next_run_at > datetime.now(UTC)  # caught up to the future
        assert ledger.account_balance(s, b.id) == 40


def test_projection_is_idempotent_on_redelivery():
    # the event bus is at-least-once; applying the SAME event twice must move the read
    # model exactly once (dedupe on event_id) — no double-counting on redelivery.
    with Session(engine) as s:
        a = ledger.create_account(s, name=f"pa-{uuid.uuid4()}")
        b = ledger.create_account(s, name=f"pb-{uuid.uuid4()}")
        eid = uuid.uuid4()
        assert (
            projection.apply_event(
                s, event_id=eid, debit_id=a.id, credit_id=b.id, amount_cents=250
            )
            is True
        )
        assert (
            projection.apply_event(  # redelivery
                s, event_id=eid, debit_id=a.id, credit_id=b.id, amount_cents=250
            )
            is False
        )
        assert s.get(AccountBalance, a.id).balance_cents == -250
        assert s.get(AccountBalance, b.id).balance_cents == 250
        assert (
            s.get(AccountBalance, b.id).events_applied == 1
        )  # applied once, not twice


def test_projection_converges_to_authoritative():
    # after the worker consumes the outbox events, the materialized balance must equal
    # the authoritative SUM(ledger_entry) — i.e. lag converges to 0.
    with Session(engine) as s:
        a = ledger.create_account(s, name=f"ca-{uuid.uuid4()}")
        b = ledger.create_account(s, name=f"cb-{uuid.uuid4()}")
        ledger.deposit(
            s, to_id=a.id, amount_cents=1_000, idempotency_key=str(uuid.uuid4())
        )
        ledger.transfer(
            s,
            from_id=a.id,
            to_id=b.id,
            amount_cents=400,
            idempotency_key=str(uuid.uuid4()),
        )
        # drain every outbox event into the projection (what the worker does live)
        for e in s.exec(select(OutboxEvent)).all():
            p = e.payload
            projection.apply_event(
                s,
                event_id=e.id,
                debit_id=p["debit"],
                credit_id=p["credit"],
                amount_cents=p["amount_cents"],
            )
        rows = {r["account_id"]: r for r in projection.lag(s)}
        assert rows[str(a.id)]["lag_cents"] == 0
        assert rows[str(b.id)]["lag_cents"] == 0
        assert rows[str(b.id)]["projected_cents"] == 400
