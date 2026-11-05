"""add alert_deliveries.kind for the daily borderline probe

Revision ID: 20261105a003
Revises: 20261105a002
Create Date: 2026-11-05 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261105a003"
down_revision: Union[str, Sequence[str], None] = "20261105a002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("alert_deliveries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(length=16), nullable=False, server_default="alert"))


def downgrade() -> None:
    with op.batch_alter_table("alert_deliveries", schema=None) as batch_op:
        batch_op.drop_column("kind")
