"""Reconciliation: proves the ledger has not drifted.

Two checks:
  * global drift — SUM(all entries) must be exactly 0
  * per-transaction balance — every transaction's entries must net to 0 (debits == credits)
A non-zero result means a bug let the books drift; in a real system this pages someone.
"""
from sqlmodel import Session, func, select

from app.models import LedgerEntry


def reconcile(session: Session) -> dict:
    global_drift = session.exec(
        select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
    ).one()

    unbalanced = session.exec(
        select(LedgerEntry.transaction_id)
        .group_by(LedgerEntry.transaction_id)
        .having(func.sum(LedgerEntry.amount_cents) != 0)
    ).all()

    return {
        "global_drift_cents": int(global_drift),
        "unbalanced_transactions": [str(t) for t in unbalanced],
        "balanced": int(global_drift) == 0 and len(unbalanced) == 0,
    }
