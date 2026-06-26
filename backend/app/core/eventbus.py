"""Redis Streams event bus.

The outbox relay publishes domain events to a stream; workers consume them via a
consumer group (at-least-once delivery + acks). This is the Kafka-free event bus.
"""
import json
import os

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
STREAM = "ledger.events"
GROUP = "ledger-workers"

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def ensure_group() -> None:
    """Create the consumer group (and the stream) if it doesn't exist yet."""
    try:
        get_redis().xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):  # already exists is fine
            raise


def publish(event: dict) -> str:
    return get_redis().xadd(STREAM, {"data": json.dumps(event)})
