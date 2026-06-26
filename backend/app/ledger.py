"""LedgerFlow domain logic — immutable double-entry ledger.

Invariants:
  * money is integer cents (never float)
  * an account's balance = SUM(its ledger_entry.amount_cents)
  * every transaction's entries sum to 0  (debits == credits)
  * the "external:world" account funds deposits; its negative balance = total
    money inside the system, so SUM over ALL entries is always 0 (reconciliation)
"""
import uuid

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.models import Account, LedgerEntry, OutboxEvent, Transaction, TxnStatus

EXTERNAL_ACCOUNT_NAME = "external:world"


class InsufficientFunds(Exception): ...


class AccountNotFound(Exception): ...


def account_balance(session: Session, account_id: uuid.UUID) -> int:
    return session.exec(
        select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
            LedgerEntry.account_id == account_id
        )
    ).one()


def create_account(session: Session, *, name: str, currency: str = "USD", owner_id=None) -> Account:
    acc = Account(name=name, currency=currency, owner_id=owner_id)
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


def _get_external_account(session: Session) -> Account:
    acc = session.exec(select(Account).where(Account.name == EXTERNAL_ACCOUNT_NAME)).first()
    if acc is None:
        acc = Account(name=EXTERNAL_ACCOUNT_NAME, currency="USD")
        session.add(acc)
        session.commit()
        session.refresh(acc)
    return acc


def _post_double_entry(
    session: Session, *, debit_id, credit_id, amount_cents, idempotency_key,
    event_type, description=None, check_funds=True,
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
    for acc_id in sorted([debit_id, credit_id], key=str):
        acc = session.exec(select(Account).where(Account.id == acc_id).with_for_update()).first()
        if acc is None:
            raise AccountNotFound(str(acc_id))

    # 3) funds check on the debited account (skipped for deposits from external)
    if check_funds and account_balance(session, debit_id) < amount_cents:
        raise InsufficientFunds()

    # 4) double-entry: debit (-), credit (+) -> sums to 0
    txn = Transaction(
        idempotency_key=idempotency_key, description=description, status=TxnStatus.posted.value
    )
    session.add(txn)
    session.flush()  # populate txn.id
    entries = [
        LedgerEntry(transaction_id=txn.id, account_id=debit_id, amount_cents=-amount_cents),
        LedgerEntry(transaction_id=txn.id, account_id=credit_id, amount_cents=amount_cents),
    ]
    assert sum(e.amount_cents for e in entries) == 0, "debits must equal credits"
    session.add_all(entries)

    # 5) outbox event in the SAME transaction -> no lost events
    session.add(
        OutboxEvent(
            aggregate_id=txn.id,
            event_type=event_type,
            payload={
                "debit": str(debit_id), "credit": str(credit_id), "amount_cents": amount_cents,
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


def deposit(session: Session, *, to_id, amount_cents, idempotency_key, description=None) -> Transaction:
    external = _get_external_account(session)
    return _post_double_entry(
        session, debit_id=external.id, credit_id=to_id, amount_cents=amount_cents,
        idempotency_key=idempotency_key, event_type="deposit.posted",
        description=description, check_funds=False,  # external may go negative
    )


def transfer(session: Session, *, from_id, to_id, amount_cents, idempotency_key, description=None) -> Transaction:
    return _post_double_entry(
        session, debit_id=from_id, credit_id=to_id, amount_cents=amount_cents,
        idempotency_key=idempotency_key, event_type="transfer.posted",
        description=description, check_funds=True,
    )


def _holds_account(session: Session) -> Account:
    acc = session.exec(select(Account).where(Account.name == "holds:system")).first()
    if acc is None:
        acc = Account(name="holds:system", currency="USD")
        session.add(acc)
        session.commit()
        session.refresh(acc)
    return acc


def saga_transfer(session: Session, *, from_id, to_id, amount_cents, idempotency_key, fail_at=None) -> dict:
    """Multi-step transfer as a SAGA: reserve (sender -> holds) then capture
    (holds -> recipient). If capture fails, COMPENSATE by releasing the hold back
    to the sender. Each step is its own idempotent, committed transaction — so a
    partial failure rolls back cleanly and the ledger never drifts.
    `fail_at="capture"` injects a failure to demonstrate compensation."""
    holds = _holds_account(session)

    # Step 1 — reserve funds: sender -> holds
    transfer(session, from_id=from_id, to_id=holds.id, amount_cents=amount_cents,
             idempotency_key=f"{idempotency_key}:reserve", description="saga:reserve")
    try:
        if fail_at == "capture":
            raise RuntimeError("injected failure during capture")
        # Step 2 — capture: holds -> recipient
        txn = transfer(session, from_id=holds.id, to_id=to_id, amount_cents=amount_cents,
                       idempotency_key=f"{idempotency_key}:capture", description="saga:capture")
        return {"status": "posted", "transaction_id": str(txn.id)}
    except Exception:
        # Compensation — release the hold back to the sender
        transfer(session, from_id=holds.id, to_id=from_id, amount_cents=amount_cents,
                 idempotency_key=f"{idempotency_key}:compensate", description="saga:compensate")
        return {"status": "compensated"}
