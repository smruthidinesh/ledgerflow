"""Run the outbox relay (which also drives the recurring-payment scheduler) and the
event worker inside the API process as background daemon threads.

This exists for single-instance / free-tier hosting (e.g. Render free plan, where a
standalone Background Worker is not available). In a real production topology these
run as their own processes/containers — see compose.override.yml (`relay`, `worker`)
and docs/PROJECT_GUIDE.md. Toggle with the env var RUN_EMBEDDED_WORKERS=true.
"""
import logging
import threading

log = logging.getLogger("ledgerflow.embedded")


def start_embedded_workers() -> None:
    from app import event_worker, outbox_relay

    for name, target in (("relay", outbox_relay.main), ("worker", event_worker.main)):
        t = threading.Thread(target=target, name=f"embedded-{name}", daemon=True)
        t.start()
        log.info("started embedded %s thread", name)
