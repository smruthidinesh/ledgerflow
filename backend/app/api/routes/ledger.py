import uuid

from fastapi import APIRouter, Header, HTTPException
from sqlmodel import select

from app import ledger, reconciliation
from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Account,
    AccountCreate,
    AccountPublic,
    DepositRequest,
    TransactionPublic,
    TransferRequest,
)

router = APIRouter(prefix="/ledger", tags=["ledger"])


def _public(session: SessionDep, acc: Account) -> AccountPublic:
    return AccountPublic(
        id=acc.id, name=acc.name, currency=acc.currency,
        balance_cents=ledger.account_balance(session, acc.id), created_at=acc.created_at,
    )


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
