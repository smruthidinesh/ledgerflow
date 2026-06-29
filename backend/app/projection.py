"""CQRS read-model projection.

The event worker calls apply_event() for each domain event it consumes. We maintain
a materialized per-account balance (account_balance) so reads don't re-scan the whole
ledger. Because the bus is at-least-once, apply_event is made idempotent via the
processed_event dedupe table: a redelivered event is recognised and skipped, so each
event moves the read model exactly once.

The authoritative balance is still SUM(ledger_entry) — this is a cache. lag() exposes
how far the projection trails the source of truth (0 == fully caught up).
"""

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.models import Account, AccountBalance, LedgerEntry, ProcessedEvent

log = logging.getLogger("ledgerflow.projection")


def _bump(session: Session, account_id: uuid.UUID, delta: int) -> None:
    row = session.get(AccountBalance, account_id)
    if row is None:
        row = AccountBalance(account_id=account_id, balance_cents=0, events_applied=0)
        session.add(row)
    row.balance_cents += delta
    row.events_applied += 1


def apply_event(
    session: Session, *, event_id, debit_id, credit_id, amount_cents: int
) -> bool:
    """Apply one ledger event to the read model. Returns True if applied, False if it
    was a duplicate (already projected). Atomic: the dedupe row and both balance
    updates commit together, so we can never half-apply an event."""
    event_id = uuid.UUID(str(event_id))
    # claim the event id first; the unique PK is the idempotency gate
    session.add(ProcessedEvent(event_id=event_id))
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False  # already projected — at-least-once redelivery, safely ignored

    _bump(session, uuid.UUID(str(debit_id)), -int(amount_cents))
    _bump(session, uuid.UUID(str(credit_id)), int(amount_cents))
    session.commit()
    return True


def lag(session: Session) -> list[dict]:
    """Per-account comparison of the projected balance vs the authoritative
    SUM(ledger_entry). lag_cents must converge to 0; a persistent non-zero lag means
    the worker is down or behind."""
    authoritative = dict(
        session.exec(
            select(
                LedgerEntry.account_id,
                func.coalesce(func.sum(LedgerEntry.amount_cents), 0),
            ).group_by(LedgerEntry.account_id)
        ).all()
    )
    projected = {b.account_id: b for b in session.exec(select(AccountBalance)).all()}
    names = {a.id: a.name for a in session.exec(select(Account)).all()}

    out = []
    for acc_id in set(authoritative) | set(projected):
        truth = int(authoritative.get(acc_id, 0))
        proj = projected.get(acc_id)
        proj_bal = proj.balance_cents if proj else 0
        out.append(
            {
                "account_id": str(acc_id),
                "name": names.get(acc_id),
                "authoritative_cents": truth,
                "projected_cents": proj_bal,
                "lag_cents": truth - proj_bal,
                "events_applied": proj.events_applied if proj else 0,
            }
        )
    out.sort(key=lambda r: abs(r["lag_cents"]), reverse=True)
    return out
