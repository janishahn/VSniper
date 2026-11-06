"""scan Vinted anonymously and keep sizes on buckets

Drops the stored Vinted cookie, refresh token, region and session health, replaces bucket
size keywords with chosen Vinted sizes, and removes size filters from searches: a bucket's
sizes now apply to its search at scan time.

Revision ID: 20261106a001
Revises: 20261105a004
Create Date: 2026-11-06 00:00:01.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261106a001"
down_revision: Union[str, Sequence[str], None] = "20261105a004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("vinted_region")
        batch.drop_column("vinted_cookie")
        batch.drop_column("vinted_refresh_token")
        batch.drop_column("session_health")
        batch.drop_column("refresh_token_expiry_warning_sent_for")
    with op.batch_alter_table("buckets") as batch:
        batch.drop_column("size_keywords")
        batch.add_column(sa.Column("sizes", sa.JSON(), nullable=False, server_default="[]"))

    connection = op.get_bind()
    for search_id, filters in connection.execute(sa.text("SELECT id, filters FROM searches")).all():
        current = json.loads(filters) if isinstance(filters, str) else (filters or [])
        kept = [item for item in current if str(item.get("field") or "").strip().lower() != "size"]
        if kept != current:
            connection.execute(
                sa.text("UPDATE searches SET filters = :filters WHERE id = :id"),
                {"filters": json.dumps(kept), "id": search_id},
            )


def downgrade() -> None:
    with op.batch_alter_table("buckets") as batch:
        batch.drop_column("sizes")
        batch.add_column(sa.Column("size_keywords", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("app_settings") as batch:
        batch.add_column(sa.Column("refresh_token_expiry_warning_sent_for", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("session_health", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("vinted_refresh_token", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("vinted_cookie", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("vinted_region", sa.String(length=16), nullable=False, server_default="de"))
