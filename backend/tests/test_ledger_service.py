import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, func, select

from app import ledger
from app.core.db import engine
from app.models import LedgerEntry


def test_transfer_entries_sum_to_zero():
    with Session(engine) as s:
        a = ledger.create_account(s, name=f"z-{uuid.uuid4()}")
        b = ledger.create_account(s, name=f"z-{uuid.uuid4()}")
        ledger.deposit(s, to_id=a.id, amount_cents=1_000, idempotency_key=str(uuid.uuid4()))
        txn = ledger.transfer(
            s, from_id=a.id, to_id=b.id, amount_cents=400, idempotency_key=str(uuid.uuid4())
        )
        entries = s.exec(select(LedgerEntry).where(LedgerEntry.transaction_id == txn.id)).all()
        assert len(entries) == 2
        assert sum(e.amount_cents for e in entries) == 0  # debits == credits


def test_global_zero_drift():
    # the whole ledger must always net to zero — the reconciliation invariant
    with Session(engine) as s:
        total = s.exec(select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0))).one()
        assert total == 0


def test_concurrent_transfers_never_overdraft():
    # Fund Alice with exactly 100, then fire 5 concurrent transfers of 30.
    # Row-locking must serialize them: only 3 succeed (3*30=90 <= 100); Alice never goes negative.
    with Session(engine) as s:
        aid = ledger.create_account(s, name=f"c-{uuid.uuid4()}").id
        bid = ledger.create_account(s, name=f"c-{uuid.uuid4()}").id
        ledger.deposit(s, to_id=aid, amount_cents=100, idempotency_key=str(uuid.uuid4()))

    def attempt(_) -> bool:
        with Session(engine) as s:
            try:
                ledger.transfer(
                    s, from_id=aid, to_id=bid, amount_cents=30, idempotency_key=str(uuid.uuid4())
                )
                return True
            except ledger.InsufficientFunds:
                return False

    with ThreadPoolExecutor(max_workers=5) as ex:
        successes = sum(ex.map(attempt, range(5)))

    with Session(engine) as s:
        assert successes == 3
        assert ledger.account_balance(s, aid) == 10   # 100 - 90, never negative
        assert ledger.account_balance(s, bid) == 90
