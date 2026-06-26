import uuid

from fastapi.testclient import TestClient

from app.core.config import settings

PREFIX = f"{settings.API_V1_STR}/ledger"


def _account(client, h, name):
    r = client.post(f"{PREFIX}/accounts", headers=h, json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _deposit(client, h, acc_id, cents, key):
    return client.post(
        f"{PREFIX}/deposit", headers={**h, "Idempotency-Key": key},
        json={"to_account_id": acc_id, "amount_cents": cents},
    )


def _transfer(client, h, frm, to, cents, key):
    return client.post(
        f"{PREFIX}/transfers", headers={**h, "Idempotency-Key": key},
        json={"from_account_id": frm, "to_account_id": to, "amount_cents": cents},
    )


def _balance(client, h, acc_id):
    return client.get(f"{PREFIX}/accounts/{acc_id}", headers=h).json()["balance_cents"]


def test_deposit_then_transfer_updates_balances(client: TestClient, superuser_token_headers):
    h = superuser_token_headers
    a, b = _account(client, h, "t-alice"), _account(client, h, "t-bob")
    assert _deposit(client, h, a["id"], 10_000, str(uuid.uuid4())).status_code == 200
    assert _transfer(client, h, a["id"], b["id"], 3_000, str(uuid.uuid4())).status_code == 200
    assert _balance(client, h, a["id"]) == 7_000
    assert _balance(client, h, b["id"]) == 3_000


def test_idempotent_transfer_does_not_double_charge(client: TestClient, superuser_token_headers):
    h = superuser_token_headers
    a, b = _account(client, h, "i-alice"), _account(client, h, "i-bob")
    _deposit(client, h, a["id"], 5_000, str(uuid.uuid4()))
    key = str(uuid.uuid4())
    r1 = _transfer(client, h, a["id"], b["id"], 2_000, key)
    r2 = _transfer(client, h, a["id"], b["id"], 2_000, key)  # retry, same key
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]  # same transaction, not a new one
    assert _balance(client, h, a["id"]) == 3_000  # money moved once
    assert _balance(client, h, b["id"]) == 2_000


def test_insufficient_funds_returns_409(client: TestClient, superuser_token_headers):
    h = superuser_token_headers
    a, b = _account(client, h, "p-alice"), _account(client, h, "p-bob")
    r = _transfer(client, h, a["id"], b["id"], 100, str(uuid.uuid4()))
    assert r.status_code == 409


def test_transfer_requires_idempotency_key(client: TestClient, superuser_token_headers):
    h = superuser_token_headers
    a, b = _account(client, h, "k-alice"), _account(client, h, "k-bob")
    r = client.post(
        f"{PREFIX}/transfers", headers=h,
        json={"from_account_id": a["id"], "to_account_id": b["id"], "amount_cents": 100},
    )
    assert r.status_code == 422  # missing Idempotency-Key header
