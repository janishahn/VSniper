"""drop the AI model registry and grid judging settings

The vision and decision models now come from OPENAI_VISION_MODEL and OPENAI_DECISION_MODEL,
and every listing is judged on its own, so the registry, the model selections, the grid
settings and the per-run fallback fields are unused.

Revision ID: 20261105a002
Revises: 20261105a001
Create Date: 2026-11-05 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261105a002"
down_revision: Union[str, Sequence[str], None] = "20261105a001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.drop_column("judge_model_id")
        batch_op.drop_column("judge_fallback_model_id")
        batch_op.drop_column("learn_model_id")
        batch_op.drop_column("observation_model_id")
        batch_op.drop_column("vlm_grid_size")
        batch_op.drop_column("vlm_pack_multiple_listing_images")
        batch_op.drop_column("vlm_judge_parallel_requests")
        batch_op.drop_column("ai_judge_image_max_px")
    with op.batch_alter_table("search_runs", schema=None) as batch_op:
        batch_op.drop_column("judge_provider")
        batch_op.drop_column("fallback_used")
    op.drop_table("ai_models")


def downgrade() -> None:
    op.create_table(
        "ai_models",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=32), nullable=False),
        sa.Column("local_base_url", sa.String(length=512), nullable=True),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("search_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("judge_provider", sa.String(length=64), nullable=True))
    with op.batch_alter_table("app_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ai_judge_image_max_px", sa.Integer(), nullable=False, server_default="512"))
        batch_op.add_column(sa.Column("vlm_judge_parallel_requests", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(
            sa.Column("vlm_pack_multiple_listing_images", sa.Boolean(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("vlm_grid_size", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("observation_model_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("learn_model_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("judge_fallback_model_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("judge_model_id", sa.String(length=64), nullable=True))
