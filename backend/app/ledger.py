"""LedgerFlow domain logic — immutable double-entry ledger.

Invariants:
  * money is integer cents (never float)
  * an account's balance = SUM(its ledger_entry.amount_cents)
  * every transaction's entries sum to 0  (debits == credits)
  * the "external:world" account funds deposits; its negative balance = total
    money inside the system, so SUM over ALL entries is always 0 (reconciliation)
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.models import (
    Account,
    LedgerEntry,
    OutboxEvent,
    RecurringTransfer,
    Transaction,
    TxnStatus,
)

EXTERNAL_ACCOUNT_NAME = "external:world"


class InsufficientFunds(Exception): ...


class AccountNotFound(Exception): ...


class CurrencyMismatch(Exception):
    """Both legs of a transaction must be in the same currency — otherwise a
    -500 USD / +500 EUR pair would 'sum to zero' yet be economically nonsense."""


def account_balance(session: Session, account_id: uuid.UUID) -> int:
    return session.exec(
        select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
            LedgerEntry.account_id == account_id
        )
    ).one()


def create_account(
    session: Session, *, name: str, currency: str = "USD", owner_id=None
) -> Account:
    acc = Account(name=name, currency=currency, owner_id=owner_id)
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


def _get_external_account(session: Session, currency: str = "USD") -> Account:
    """The synthetic funding source for deposits, one per currency. USD keeps the
    legacy name "external:world" (so existing data is untouched); other currencies
    get "external:world:<CCY>". Each goes negative by the value injected, so the
    ledger still nets to zero within every currency."""
    name = (
        EXTERNAL_ACCOUNT_NAME
        if currency == "USD"
        else f"{EXTERNAL_ACCOUNT_NAME}:{currency}"
    )
    acc = session.exec(select(Account).where(Account.name == name)).first()
    if acc is None:
        acc = Account(name=name, currency=currency)
        session.add(acc)
        session.commit()
        session.refresh(acc)
    return acc


def _post_double_entry(
    session: Session,
    *,
    debit_id,
    credit_id,
    amount_cents,
    idempotency_key,
    event_type,
    description=None,
    check_funds=True,
) -> Transaction:
    """Atomically post a balanced 2-entry transaction + an outbox event.
    Idempotent on idempotency_key; locks accounts; enforces debits == credits."""
    if amount_cents <= 0:
        raise ValueError("amount must be positive")
    if debit_id == credit_id:
        raise ValueError("debit and credit accounts must differ")

    # 1) idempotency fast-path
    if idempotency_key:
        existing = session.exec(
            select(Transaction).where(Transaction.idempotency_key == idempotency_key)
        ).first()
        if existing:
            return existing

    # 2) lock both accounts in a stable order (deadlock-safe)
    locked: dict = {}
    for acc_id in sorted([debit_id, credit_id], key=str):
        acc = session.exec(
            select(Account).where(Account.id == acc_id).with_for_update()
        ).first()
        if acc is None:
            raise AccountNotFound(str(acc_id))
        locked[acc_id] = acc

    # 2b) currency guard: both legs must share a currency, else "debits == credits"
    # would hold numerically while moving value across incompatible units.
    if locked[debit_id].currency != locked[credit_id].currency:
        raise CurrencyMismatch(
            f"{locked[debit_id].currency} -> {locked[credit_id].currency}"
        )

    # 3) funds check on the debited account (skipped for deposits from external)
    if check_funds and account_balance(session, debit_id) < amount_cents:
        raise InsufficientFunds()

    # 4) double-entry: debit (-), credit (+) -> sums to 0
    txn = Transaction(
        idempotency_key=idempotency_key,
        description=description,
        status=TxnStatus.posted.value,
    )
    session.add(txn)
    session.flush()  # populate txn.id
    entries = [
        LedgerEntry(
            transaction_id=txn.id, account_id=debit_id, amount_cents=-amount_cents
        ),
        LedgerEntry(
            transaction_id=txn.id, account_id=credit_id, amount_cents=amount_cents
        ),
    ]
    assert sum(e.amount_cents for e in entries) == 0, "debits must equal credits"
    session.add_all(entries)

    # 5) outbox event in the SAME transaction -> no lost events
    session.add(
        OutboxEvent(
            aggregate_id=txn.id,
            event_type=event_type,
            payload={
                "debit": str(debit_id),
                "credit": str(credit_id),
                "amount_cents": amount_cents,
            },
        )
    )

    try:
        session.commit()
    except IntegrityError:  # concurrent request with the same idempotency_key
        session.rollback()
        return session.exec(
            select(Transaction).where(Transaction.idempotency_key == idempotency_key)
        ).first()
    session.refresh(txn)
    return txn


def deposit(
    session: Session, *, to_id, amount_cents, idempotency_key, description=None
) -> Transaction:
    dest = session.get(Account, to_id)
    if dest is None:
        raise AccountNotFound(str(to_id))
    external = _get_external_account(session, currency=dest.currency)
    return _post_double_entry(
        session,
        debit_id=external.id,
        credit_id=to_id,
        amount_cents=amount_cents,
        idempotency_key=idempotency_key,
        event_type="deposit.posted",
        description=description,
        check_funds=False,  # external may go negative
    )


def transfer(
    session: Session, *, from_id, to_id, amount_cents, idempotency_key, description=None
) -> Transaction:
    return _post_double_entry(
        session,
        debit_id=from_id,
        credit_id=to_id,
        amount_cents=amount_cents,
        idempotency_key=idempotency_key,
        event_type="transfer.posted",
        description=description,
        check_funds=True,
    )


def recent_activity(session: Session, limit: int = 50, account_ids=None) -> list[dict]:
    """Recent transactions as a human-readable feed: from -> to, amount, status, time.
    N+1-free: one query for the txns, one for all their entries, one for account names.
    If account_ids is given, only transactions touching those accounts are returned
    (per-user scoping — prevents cross-tenant disclosure)."""
    q = select(Transaction).order_by(Transaction.created_at.desc())
    if account_ids is not None:
        if not account_ids:
            return []
        q = q.where(
            Transaction.id.in_(
                select(LedgerEntry.transaction_id).where(
                    LedgerEntry.account_id.in_(account_ids)
                )
            )
        )
    txns = session.exec(q.limit(limit)).all()
    if not txns:
        return []

    txn_ids = [t.id for t in txns]
    entries = session.exec(
        select(LedgerEntry).where(LedgerEntry.transaction_id.in_(txn_ids))
    ).all()
    by_txn: dict[uuid.UUID, list[LedgerEntry]] = {}
    for e in entries:
        by_txn.setdefault(e.transaction_id, []).append(e)
    names = {a.id: a.name for a in session.exec(select(Account)).all()}

    feed = []
    for t in txns:
        es = by_txn.get(t.id, [])
        debit = next((e for e in es if e.amount_cents < 0), None)
        credit = next((e for e in es if e.amount_cents > 0), None)
        feed.append(
            {
                "id": str(t.id),
                "created_at": t.created_at,
                "status": t.status,
                "description": t.description,
                "amount_cents": abs(credit.amount_cents) if credit else 0,
                "from_account": names.get(debit.account_id) if debit else None,
                "to_account": names.get(credit.account_id) if credit else None,
            }
        )
    return feed


def account_statement(
    session: Session, account_id: uuid.UUID, limit: int = 500
) -> list[dict]:
    """One account's ledger entries, oldest→newest, with a running balance. Ordered
    chronologically (created_at, then id as a stable tiebreak) so the running balance
    accumulates correctly. Joins the transaction once to carry its description."""
    rows = session.exec(
        select(LedgerEntry, Transaction)
        .join(Transaction, Transaction.id == LedgerEntry.transaction_id)
        .where(LedgerEntry.account_id == account_id)
        .order_by(LedgerEntry.created_at, LedgerEntry.id)
        .limit(limit)
    ).all()
    running = 0
    out = []
    for entry, txn in rows:
        running += entry.amount_cents
        out.append(
            {
                "created_at": entry.created_at,
                "transaction_id": str(entry.transaction_id),
                "description": txn.description,
                "amount_cents": entry.amount_cents,  # signed: +credit / -debit
                "running_balance_cents": running,
            }
        )
    return out


def create_recurring(
    session: Session, *, from_id, to_id, amount_cents, interval_seconds
) -> RecurringTransfer:
    r = RecurringTransfer(
        from_account_id=from_id,
        to_account_id=to_id,
        amount_cents=amount_cents,
        interval_seconds=interval_seconds,
    )
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def list_recurring(session: Session, account_ids=None):
    q = select(RecurringTransfer).order_by(RecurringTransfer.created_at.desc())
    if account_ids is not None:
        if not account_ids:
            return []
        q = q.where(RecurringTransfer.from_account_id.in_(account_ids))
    return session.exec(q).all()


def stop_recurring(session: Session, rid) -> RecurringTransfer | None:
    r = session.get(RecurringTransfer, rid)
    if r:
        r.active = False
        session.add(r)
        session.commit()
        session.refresh(r)
    return r


MAX_CATCHUP_RUNS = 10  # bound a single tick so a long outage can't fire a huge burst


def run_due_recurring(session: Session) -> int:
    """Executed by the scheduler each tick: run every active standing order whose
    next_run_at has passed. Returns the number of payments executed.

    Schedule is anchored to next_run_at (advanced by += interval), NOT to "now", so
    the cadence never drifts by the poll latency. If a standing order is overdue by
    several intervals (e.g. the worker was down), it CATCHES UP — firing once per
    missed interval — bounded by MAX_CATCHUP_RUNS so a multi-day outage drains over
    several ticks instead of one giant burst. Idempotent per run; stops when dry."""
    now = datetime.now(UTC)
    due = session.exec(
        select(RecurringTransfer).where(
            RecurringTransfer.active == True,  # noqa: E712
            RecurringTransfer.next_run_at <= now,
        )
    ).all()
    executed = 0
    for r in due:
        fired = 0
        while r.active and r.next_run_at <= now and fired < MAX_CATCHUP_RUNS:
            try:
                transfer(
                    session,
                    from_id=r.from_account_id,
                    to_id=r.to_account_id,
                    amount_cents=r.amount_cents,
                    idempotency_key=f"rec:{r.id}:{r.runs}",
                    description="recurring payment",
                )
            except InsufficientFunds:
                r.active = False  # stop the standing order when funds run out
                break
            r.runs += 1
            executed += 1
            fired += 1
            r.next_run_at = r.next_run_at + timedelta(seconds=r.interval_seconds)
        session.add(r)
        session.commit()
    return executed


def _holds_account(session: Session) -> Account:
    acc = session.exec(select(Account).where(Account.name == "holds:system")).first()
    if acc is None:
        acc = Account(name="holds:system", currency="USD")
        session.add(acc)
        session.commit()
        session.refresh(acc)
    return acc


def saga_transfer(
    session: Session, *, from_id, to_id, amount_cents, idempotency_key, fail_at=None
) -> dict:
    """Multi-step transfer as a SAGA: reserve (sender -> holds) then capture
    (holds -> recipient). If capture fails, COMPENSATE by releasing the hold back
    to the sender. Each step is its own idempotent, committed transaction — so a
    partial failure rolls back cleanly and the ledger never drifts.
    `fail_at="capture"` injects a failure to demonstrate compensation."""
    holds = _holds_account(session)

    # Step 1 — reserve funds: sender -> holds
    transfer(
        session,
        from_id=from_id,
        to_id=holds.id,
        amount_cents=amount_cents,
        idempotency_key=f"{idempotency_key}:reserve",
        description="saga:reserve",
    )
    try:
        if fail_at == "capture":
            raise RuntimeError("injected failure during capture")
        # Step 2 — capture: holds -> recipient
        txn = transfer(
            session,
            from_id=holds.id,
            to_id=to_id,
            amount_cents=amount_cents,
            idempotency_key=f"{idempotency_key}:capture",
            description="saga:capture",
        )
        return {"status": "posted", "transaction_id": str(txn.id)}
    except Exception:
        # Compensation — release the hold back to the sender
        transfer(
            session,
            from_id=holds.id,
            to_id=from_id,
            amount_cents=amount_cents,
            idempotency_key=f"{idempotency_key}:compensate",
            description="saga:compensate",
        )
        return {"status": "compensated"}
