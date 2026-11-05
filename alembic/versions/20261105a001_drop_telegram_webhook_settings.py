"""drop telegram webhook settings

Telegram updates are now fetched by long polling, so the webhook URL and secret are unused.

Revision ID: 20261105a001
Revises: 20260728a001
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261105a001"
down_revision: Union[str, Sequence[str], None] = "20260728a001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_column("telegram_webhook_url")
        batch_op.drop_column("telegram_webhook_secret")


def downgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("telegram_webhook_secret", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("telegram_webhook_url", sa.Text(), nullable=False, server_default=""))
