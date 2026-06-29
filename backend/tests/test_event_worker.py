"""Dead-letter / retry behaviour of the event worker, exercised against a real Redis
stream isolated by a unique name (so it can't touch the app's live stream)."""

import os
import uuid

import pytest
import redis

from app import event_worker

REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
def stream():
    try:
        r = redis.Redis.from_url(
            REDIS_URL, decode_responses=True, socket_connect_timeout=1
        )
        r.ping()
    except Exception:
        pytest.skip("redis not reachable")
    name = f"test.events.{uuid.uuid4().hex[:8]}"
    group = "g"
    dlq = f"{name}.dead"
    r.xgroup_create(name, group, id="0", mkstream=True)
    try:
        yield r, name, group, dlq
    finally:
        r.delete(name, dlq)


def test_poison_message_is_dead_lettered_not_stuck(stream):
    r, name, group, dlq = stream
    consumer = "c1"
    # an un-parseable payload makes handle() throw every time -> poison
    r.xadd(name, {"data": "this is not json"})
    # initial delivery (delivery count -> 1), handler fails, message stays pending
    resp = r.xreadgroup(group, consumer, {name: ">"}, count=10)
    msg_id, fields = resp[0][1][0]
    assert not event_worker._handle_message(r, msg_id, fields, stream=name, group=group)

    # retries climb the delivery count; after max_deliveries it is quarantined
    dead = 0
    for _ in range(6):
        dead += event_worker.reclaim_stuck(
            r,
            stream=name,
            group=group,
            consumer=consumer,
            dlq=dlq,
            idle_ms=0,
            max_deliveries=3,
        )
        if dead:
            break

    assert dead == 1
    assert r.xlen(dlq) == 1  # the poison message landed in the DLQ
    entry = r.xrevrange(dlq, count=1)[0][1]
    assert entry["reason"] == "max-deliveries"
    # and it no longer blocks the group — nothing left pending
    assert r.xpending(name, group)["pending"] == 0


def test_healthy_message_is_acked(stream):
    r, name, group, dlq = stream
    # a payload with no balance delta is handled and acked immediately (no DB needed)
    import json

    r.xadd(
        name,
        {"data": json.dumps({"id": str(uuid.uuid4()), "type": "noop", "payload": {}})},
    )
    resp = r.xreadgroup(group, "c1", {name: ">"}, count=10)
    msg_id, fields = resp[0][1][0]
    assert (
        event_worker._handle_message(r, msg_id, fields, stream=name, group=group)
        is True
    )
    assert r.xpending(name, group)["pending"] == 0  # acked, nothing pending
