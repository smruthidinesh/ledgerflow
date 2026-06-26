"""Event worker: consumes domain events from the Redis Streams consumer group and
handles them (at-least-once; acks only after successful handling, so a crash leaves
the event for redelivery). This is where event-driven side-effects live."""
import json
import logging

from app.core.eventbus import GROUP, STREAM, ensure_group, get_redis

log = logging.getLogger("ledgerflow.worker")
CONSUMER = "worker-1"


def handle(event: dict) -> None:
    # Business side-effect of the domain event. Demo: log it. In production this is
    # where you'd update read-models, send notifications, trigger downstream sagas, etc.
    log.info(
        "handled event=%s txn=%s payload=%s",
        event.get("type"), event.get("aggregate_id"), event.get("payload"),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ensure_group()
    r = get_redis()
    log.info("event worker started (group=%s, consumer=%s)", GROUP, CONSUMER)
    while True:
        resp = r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000)
        if not resp:
            continue
        for _stream, messages in resp:
            for msg_id, fields in messages:
                try:
                    handle(json.loads(fields["data"]))
                    r.xack(STREAM, GROUP, msg_id)  # ack only after success
                except Exception:
                    log.exception("handler failed for %s (left for redelivery)", msg_id)


if __name__ == "__main__":
    main()
