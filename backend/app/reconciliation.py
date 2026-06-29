"""Reconciliation: proves the ledger has not drifted.

Three checks:
  * per-currency drift — within EACH currency, SUM(entries) must be exactly 0.
    (A bare global sum of cents could hide offsetting drift, e.g. +100 USD and
    -100 EUR netting to "0" while both currencies are actually broken.)
  * global drift — SUM(all entries) must be 0 (kept for the Prometheus gauge).
  * per-transaction balance — every transaction's entries must net to 0.
A non-zero result means a bug let the books drift; in a real system this pages someone.
"""

import logging

from sqlmodel import Session, desc, func, select

from app.models import Account, LedgerEntry, ReconciliationCheck

log = logging.getLogger("ledgerflow.reconcile")


def reconcile(session: Session) -> dict:
    global_drift = session.exec(
        select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
    ).one()

    # drift grouped by the currency of the entry's account
    by_currency = session.exec(
        select(Account.currency, func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
        .join(Account, Account.id == LedgerEntry.account_id)
        .group_by(Account.currency)
    ).all()
    currency_drift = {ccy: int(total) for ccy, total in by_currency}
    drifted_currencies = {ccy: d for ccy, d in currency_drift.items() if d != 0}

    unbalanced = session.exec(
        select(LedgerEntry.transaction_id)
        .group_by(LedgerEntry.transaction_id)
        .having(func.sum(LedgerEntry.amount_cents) != 0)
    ).all()

    return {
        "global_drift_cents": int(global_drift),
        "currency_drift_cents": currency_drift,
        "unbalanced_transactions": [str(t) for t in unbalanced],
        "balanced": not drifted_currencies and len(unbalanced) == 0,
    }


def run_scheduled_check(session: Session) -> dict:
    """Run reconciliation and persist the result (ReconciliationCheck). Called on a
    timer by the relay so drift is caught proactively, not only when someone opens the
    dashboard. A failed check is logged at ERROR — the hook where real alerting fires."""
    report = reconcile(session)
    session.add(
        ReconciliationCheck(
            balanced=report["balanced"],
            global_drift_cents=report["global_drift_cents"],
            detail=report,
        )
    )
    session.commit()
    if not report["balanced"]:
        log.error("RECONCILIATION FAILED — ledger has drifted: %s", report)
    return report


def latest_check(session: Session) -> ReconciliationCheck | None:
    return session.exec(
        select(ReconciliationCheck)
        .order_by(desc(ReconciliationCheck.checked_at))
        .limit(1)
    ).first()
