"""separate data per user

Adds a users table and a user_id on everything a person owns: buckets (now keyed by user and
key), searches, candidates, taste samples, the taste state and learning snapshots. All
existing data becomes user 1's. The chat ID, alert threshold and blocked brands move from
app_settings to the user; user 1 keeps the stored chat ID, or is linked to the first allowed
chat on the next start.

Revision ID: 20261107a001
Revises: 20261106a001
Create Date: 2026-11-07 00:00:01.000000

"""
import json
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261107a001"
down_revision: Union[str, Sequence[str], None] = "20261106a001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OWNED = ("searches", "candidates", "taste_samples", "learning_snapshots")
_BUCKET_COLUMNS = "key, label, description, catalog_ids, sizes, position, archived, created_at, updated_at"


def _bucket_columns() -> list[sa.Column]:
    return [
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("catalog_ids", sa.JSON(), nullable=False),
        sa.Column("sizes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column("alert_threshold", sa.Integer(), nullable=False, server_default="95"),
        sa.Column("blocked_brands", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_chat_id", name="uq_users_telegram_chat_id"),
    )

    connection = op.get_bind()
    settings = connection.execute(
        sa.text("SELECT telegram_chat_id, alert_threshold, blocked_brands FROM app_settings WHERE id = 1")
    ).first()
    chat_id, alert_threshold, blocked_brands = settings if settings is not None else ("", 95, "[]")
    chat_id = (chat_id or "").strip()
    connection.execute(
        sa.text(
            "INSERT INTO users (id, name, telegram_chat_id, alert_threshold, blocked_brands, created_at) "
            "VALUES (1, 'Profile 1', :chat_id, :alert_threshold, :blocked_brands, :created_at)"
        ),
        {
            "chat_id": chat_id if chat_id and chat_id != "put-your-telegram-chat-id-here" else None,
            "alert_threshold": alert_threshold or 95,
            "blocked_brands": blocked_brands if isinstance(blocked_brands, str) else json.dumps(blocked_brands or []),
            "created_at": datetime.now(UTC),
        },
    )

    op.create_table(
        "buckets_new",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        *_bucket_columns(),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )
    op.execute(f"INSERT INTO buckets_new (user_id, {_BUCKET_COLUMNS}) SELECT 1, {_BUCKET_COLUMNS} FROM buckets")
    op.drop_table("buckets")
    op.rename_table("buckets_new", "buckets")

    for table in _OWNED:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("user_id", sa.Integer(), nullable=False, server_default="1"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("user_id", server_default=None)
            batch.create_foreign_key(f"fk_{table}_user_id", "users", ["user_id"], ["id"])
            batch.create_index(f"ix_{table}_user_id", ["user_id"])
    with op.batch_alter_table("searches") as batch:
        batch.drop_constraint("uq_searches_clothing_item", type_="unique")
        batch.create_unique_constraint("uq_searches_user_clothing_item", ["user_id", "clothing_item"])

    with op.batch_alter_table("taste_state") as batch:
        batch.alter_column("id", new_column_name="user_id")
    op.execute("UPDATE taste_state SET user_id = 1")
    with op.batch_alter_table("taste_state") as batch:
        batch.create_foreign_key("fk_taste_state_user_id", "users", ["user_id"], ["id"])

    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("telegram_chat_id")
        batch.drop_column("alert_threshold")
        batch.drop_column("blocked_brands")


def downgrade() -> None:
    # Only user 1's data fits the single-user schema; everyone else's is dropped.
    connection = op.get_bind()
    user = connection.execute(
        sa.text("SELECT telegram_chat_id, alert_threshold, blocked_brands FROM users WHERE id = 1")
    ).first()
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(sa.Column("telegram_chat_id", sa.String(length=64), nullable=False, server_default=""))
        batch.add_column(sa.Column("alert_threshold", sa.Integer(), nullable=False, server_default="95"))
        batch.add_column(sa.Column("blocked_brands", sa.JSON(), nullable=False, server_default="[]"))
    if user is not None:
        connection.execute(
            sa.text(
                "UPDATE app_settings SET telegram_chat_id = :chat_id, alert_threshold = :alert_threshold, "
                "blocked_brands = :blocked_brands WHERE id = 1"
            ),
            {"chat_id": user[0] or "", "alert_threshold": user[1], "blocked_brands": user[2]},
        )

    op.execute("DELETE FROM alert_deliveries WHERE candidate_id IN (SELECT id FROM candidates WHERE user_id != 1)")
    for table in ("candidates", "taste_samples", "learning_snapshots", "searches", "taste_state"):
        op.execute(f"DELETE FROM {table} WHERE user_id != 1")

    with op.batch_alter_table("taste_state") as batch:
        batch.drop_constraint("fk_taste_state_user_id", type_="foreignkey")
        batch.alter_column("user_id", new_column_name="id")
    with op.batch_alter_table("searches") as batch:
        batch.drop_constraint("uq_searches_user_clothing_item", type_="unique")
        batch.create_unique_constraint("uq_searches_clothing_item", ["clothing_item"])
    for table in _OWNED:
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_user_id")
            batch.drop_constraint(f"fk_{table}_user_id", type_="foreignkey")
            batch.drop_column("user_id")

    op.create_table("buckets_old", *_bucket_columns(), sa.PrimaryKeyConstraint("key"))
    op.execute(f"INSERT INTO buckets_old ({_BUCKET_COLUMNS}) SELECT {_BUCKET_COLUMNS} FROM buckets WHERE user_id = 1")
    op.drop_table("buckets")
    op.rename_table("buckets_old", "buckets")
    op.drop_table("users")
