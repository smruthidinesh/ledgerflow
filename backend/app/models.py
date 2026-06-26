import enum
import uuid
from datetime import UTC, datetime

from pydantic import EmailStr
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(UTC)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(SQLModel):
    email: EmailStr | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list[Item] = Relationship(back_populates="owner", cascade_delete=True)


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(SQLModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


# ============================================================
# LedgerFlow — immutable double-entry ledger domain
#   money is integer cents · balance = SUM(entries) · per transfer SUM == 0
# ============================================================
class TxnStatus(str, enum.Enum):
    pending = "pending"          # SAGA in progress
    posted = "posted"            # committed; debits == credits
    failed = "failed"
    compensated = "compensated"  # rolled back via compensation


class OutboxStatus(str, enum.Enum):
    pending = "pending"
    published = "published"


class Account(SQLModel, table=True):
    __tablename__ = "account"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(max_length=120, index=True)
    currency: str = Field(default="USD", max_length=3)
    owner_id: uuid.UUID | None = Field(default=None, foreign_key="user.id", nullable=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)  # type: ignore
    )
    # balance is DERIVED, never stored: SUM(ledger_entry.amount_cents) WHERE account_id = id


class Transaction(SQLModel, table=True):
    __tablename__ = "ledger_transaction"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # idempotency: a retried transfer with the same key can never create a 2nd txn
    idempotency_key: str | None = Field(default=None, unique=True, index=True, max_length=255)
    status: str = Field(default=TxnStatus.pending.value, max_length=20, index=True)
    description: str | None = Field(default=None, max_length=255)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)  # type: ignore
    )
    entries: list["LedgerEntry"] = Relationship(back_populates="transaction", cascade_delete=True)


class LedgerEntry(SQLModel, table=True):
    """Immutable, append-only. Signed minor units (+credit / -debit).
    Per transaction the entries MUST sum to 0 (debits == credits)."""
    __tablename__ = "ledger_entry"
    __table_args__ = (CheckConstraint("amount_cents <> 0", name="entry_amount_nonzero"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    transaction_id: uuid.UUID = Field(
        foreign_key="ledger_transaction.id", nullable=False, index=True, ondelete="CASCADE"
    )
    account_id: uuid.UUID = Field(foreign_key="account.id", nullable=False, index=True)
    amount_cents: int = Field(sa_type=BigInteger)  # integer cents, never float
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)  # type: ignore
    )
    transaction: Transaction | None = Relationship(back_populates="entries")


class OutboxEvent(SQLModel, table=True):
    """Written in the SAME DB transaction as the ledger entries → no lost events.
    A worker later publishes pending rows and marks them published."""
    __tablename__ = "outbox_event"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    aggregate_id: uuid.UUID = Field(index=True)  # the transaction id
    event_type: str = Field(max_length=100)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    status: str = Field(default=OutboxStatus.pending.value, max_length=20, index=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc, sa_type=DateTime(timezone=True)  # type: ignore
    )
    published_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))  # type: ignore


# --- API schemas (request / response) ---
class AccountCreate(SQLModel):
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(default="USD", max_length=3)


class AccountPublic(SQLModel):
    id: uuid.UUID
    name: str
    currency: str
    balance_cents: int
    created_at: datetime | None = None


class TransferRequest(SQLModel):
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount_cents: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=255)


class DepositRequest(SQLModel):
    to_account_id: uuid.UUID
    amount_cents: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=255)


class TransactionPublic(SQLModel):
    id: uuid.UUID
    status: str
    description: str | None = None
    created_at: datetime | None = None
