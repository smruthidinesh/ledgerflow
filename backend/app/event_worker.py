"""Event worker: consumes domain events from the Redis Streams consumer group and
handles them (at-least-once; acks only after successful handling, so a crash leaves
the event for redelivery). This is where event-driven side-effects live — here it
maintains the CQRS read model (materialized account balances)."""

import json
import logging

from sqlmodel import Session

from app import projection
from app.core.db import engine
from app.core.eventbus import GROUP, STREAM, ensure_group, get_redis

log = logging.getLogger("ledgerflow.worker")
CONSUMER = "worker-1"


def handle(event: dict) -> None:
    """Side-effect of a domain event: update the read-model projection. Idempotent —
    apply_event dedupes on the outbox event id, so at-least-once redelivery is safe."""
    payload = event.get("payload") or {}
    debit, credit, amount = (
        payload.get("debit"),
        payload.get("credit"),
        payload.get("amount_cents"),
    )
    if debit and credit and amount is not None:
        with Session(engine) as session:
            applied = projection.apply_event(
                session,
                event_id=event["id"],
                debit_id=debit,
                credit_id=credit,
                amount_cents=amount,
            )
        log.info(
            "event=%s txn=%s %s",
            event.get("type"),
            event.get("aggregate_id"),
            "projected" if applied else "duplicate-skipped",
        )
    else:
        log.info(
            "event=%s txn=%s (no balance delta)",
            event.get("type"),
            event.get("aggregate_id"),
        )


DLQ_STREAM = "ledger.events.deadletter"
MAX_DELIVERIES = 5  # a message that fails this many times is treated as poison
CLAIM_IDLE_MS = 30_000  # only reclaim messages a consumer has held idle this long


def _handle_message(r, msg_id, fields, *, stream=STREAM, group=GROUP) -> bool:
    """Run the handler and ack only on success (at-least-once). On failure the message
    is left un-acked in the consumer group's pending list for reclaim_stuck to retry."""
    try:
        handle(json.loads(fields["data"]))
        r.xack(stream, group, msg_id)
        return True
    except Exception:
        log.exception("handler failed for %s (will retry / dead-letter)", msg_id)
        return False


def reclaim_stuck(
    r,
    *,
    stream=STREAM,
    group=GROUP,
    consumer=CONSUMER,
    dlq=DLQ_STREAM,
    idle_ms=CLAIM_IDLE_MS,
    max_deliveries=MAX_DELIVERIES,
    count=50,
) -> int:
    """Recover messages a crashed/slow consumer left un-acked, and quarantine poison.

    Without this, a message whose handler always throws is never acked and (because we
    only read new messages with '>') is never retried either — it silently stalls in
    the pending list forever. Here we re-deliver stuck messages; once one has been
    delivered max_deliveries times without success it is moved to the dead-letter
    stream and acked, so a single poison event can never block the whole pipeline.
    Returns the number of messages dead-lettered this pass."""
    dead = 0
    pending = r.xpending_range(
        stream, group, min="-", max="+", count=count, idle=idle_ms
    )
    for p in pending:
        msg_id, delivered = p["message_id"], p["times_delivered"]
        claimed = r.xclaim(stream, group, consumer, idle_ms, [msg_id])
        if not claimed or claimed[0][1] is None:
            continue
        _cid, fields = claimed[0]
        if delivered >= max_deliveries:
            r.xadd(
                dlq,
                {
                    "data": fields.get("data", ""),
                    "orig_id": str(msg_id),
                    "deliveries": str(delivered),
                    "reason": "max-deliveries",
                },
            )
            r.xack(stream, group, msg_id)
            dead += 1
            log.error("dead-lettered %s after %d deliveries", msg_id, delivered)
        else:
            _handle_message(r, _cid, fields, stream=stream, group=group)
    return dead


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ensure_group()
    r = get_redis()
    log.info("event worker started (group=%s, consumer=%s)", GROUP, CONSUMER)
    while True:
        reclaim_stuck(r)  # retry/quarantine anything stuck before taking new work
        resp = r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000)
        if not resp:
            continue
        for _stream, messages in resp:
            for msg_id, fields in messages:
                _handle_message(r, msg_id, fields)


if __name__ == "__main__":
    main()
