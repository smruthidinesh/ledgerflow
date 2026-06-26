"""Outbox relay: polls the outbox table for pending events, publishes them to the
event bus, and marks them published. This decouples writing events (in the same DB
transaction as the ledger) from delivering them — so events are never lost even if
the publish step or the API crashes."""
import logging
import time
from datetime import UTC, datetime

from sqlmodel import Session, select

from app import ledger
from app.core.db import engine
from app.core.eventbus import publish
from app.models import OutboxEvent, OutboxStatus

log = logging.getLogger("ledgerflow.relay")
POLL_SECONDS = 2
BATCH = 100


def relay_once() -> int:
    with Session(engine) as session:
        pending = session.exec(
            select(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.pending.value)
            .order_by(OutboxEvent.created_at)
            .limit(BATCH)
        ).all()
        for ev in pending:
            publish(
                {
                    "id": str(ev.id),
                    "type": ev.event_type,
                    "aggregate_id": str(ev.aggregate_id),
                    "payload": ev.payload,
                }
            )
            ev.status = OutboxStatus.published.value
            ev.published_at = datetime.now(UTC)
            session.add(ev)
        session.commit()
        return len(pending)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("outbox relay started (poll every %ss)", POLL_SECONDS)
    while True:
        try:
            n = relay_once()
            if n:
                log.info("relayed %d outbox event(s) to the bus", n)
            with Session(engine) as session:
                ran = ledger.run_due_recurring(session)
                if ran:
                    log.info("executed %d recurring payment(s)", ran)
        except Exception:
            log.exception("relay iteration failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
