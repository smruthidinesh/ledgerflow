import uuid

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse
from sqlmodel import func, select

from app import ledger, reconciliation
from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Account,
    AccountCreate,
    AccountPublic,
    DepositRequest,
    LedgerEntry,
    OutboxEvent,
    OutboxStatus,
    TransactionPublic,
    Transaction,
    TransferRequest,
)

router = APIRouter(prefix="/ledger", tags=["ledger"])


def _public(session: SessionDep, acc: Account) -> AccountPublic:
    return AccountPublic(
        id=acc.id, name=acc.name, currency=acc.currency,
        balance_cents=ledger.account_balance(session, acc.id), created_at=acc.created_at,
    )


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(session: SessionDep):
    """Prometheus metrics (public, for scraping). The drift gauge must stay 0."""
    txns = session.exec(select(func.count()).select_from(Transaction)).one()
    accts = session.exec(select(func.count()).select_from(Account)).one()
    volume = session.exec(
        select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(LedgerEntry.amount_cents > 0)
    ).one()
    drift = session.exec(select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0))).one()
    pending = session.exec(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == OutboxStatus.pending.value)
    ).one()
    lines = [
        "# HELP ledger_transactions_total Total ledger transactions",
        "# TYPE ledger_transactions_total counter",
        f"ledger_transactions_total {txns}",
        "# HELP ledger_accounts_total Total accounts",
        "# TYPE ledger_accounts_total gauge",
        f"ledger_accounts_total {accts}",
        "# HELP ledger_volume_cents_total Total credited volume (cents)",
        "# TYPE ledger_volume_cents_total counter",
        f"ledger_volume_cents_total {int(volume)}",
        "# HELP ledger_drift_cents Global balance drift — must be 0",
        "# TYPE ledger_drift_cents gauge",
        f"ledger_drift_cents {int(drift)}",
        "# HELP ledger_outbox_pending Outbox events awaiting publish",
        "# TYPE ledger_outbox_pending gauge",
        f"ledger_outbox_pending {pending}",
    ]
    return "\n".join(lines) + "\n"


@router.post("/accounts", response_model=AccountPublic)
def create_account(account_in: AccountCreate, session: SessionDep, current_user: CurrentUser):
    acc = ledger.create_account(
        session, name=account_in.name, currency=account_in.currency, owner_id=current_user.id
    )
    return _public(session, acc)


@router.get("/accounts", response_model=list[AccountPublic])
def list_accounts(session: SessionDep, current_user: CurrentUser):
    accts = session.exec(select(Account).where(Account.owner_id == current_user.id)).all()
    return [_public(session, a) for a in accts]


@router.get("/accounts/{account_id}", response_model=AccountPublic)
def get_account(account_id: uuid.UUID, session: SessionDep, current_user: CurrentUser):
    acc = session.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="account not found")
    return _public(session, acc)


@router.post("/deposit", response_model=TransactionPublic)
def deposit(
    body: DepositRequest, session: SessionDep, current_user: CurrentUser,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        txn = ledger.deposit(
            session, to_id=body.to_account_id, amount_cents=body.amount_cents,
            idempotency_key=idempotency_key, description=body.description,
        )
    except ledger.AccountNotFound:
        raise HTTPException(status_code=404, detail="account not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return TransactionPublic(
        id=txn.id, status=txn.status, description=txn.description, created_at=txn.created_at
    )


@router.get("/reconciliation")
def reconciliation_report(session: SessionDep, current_user: CurrentUser):
    return reconciliation.reconcile(session)


@router.get("/transactions")
def transactions(session: SessionDep, current_user: CurrentUser, limit: int = 50):
    return ledger.recent_activity(session, limit=limit)


@router.post("/transfers", response_model=TransactionPublic)
def create_transfer(
    body: TransferRequest, session: SessionDep, current_user: CurrentUser,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        txn = ledger.transfer(
            session, from_id=body.from_account_id, to_id=body.to_account_id,
            amount_cents=body.amount_cents, idempotency_key=idempotency_key,
            description=body.description,
        )
    except ledger.InsufficientFunds:
        raise HTTPException(status_code=409, detail="insufficient funds")
    except ledger.AccountNotFound:
        raise HTTPException(status_code=404, detail="account not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return TransactionPublic(
        id=txn.id, status=txn.status, description=txn.description, created_at=txn.created_at
    )


@router.post("/saga-transfer")
def saga_transfer(
    body: TransferRequest, session: SessionDep, current_user: CurrentUser,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    fail_at: str | None = None,  # set ?fail_at=capture to demo compensation
):
    try:
        return ledger.saga_transfer(
            session, from_id=body.from_account_id, to_id=body.to_account_id,
            amount_cents=body.amount_cents, idempotency_key=idempotency_key, fail_at=fail_at,
        )
    except ledger.InsufficientFunds:
        raise HTTPException(status_code=409, detail="insufficient funds")
    except ledger.AccountNotFound:
        raise HTTPException(status_code=404, detail="account not found")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
