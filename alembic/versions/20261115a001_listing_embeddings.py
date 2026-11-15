"""listing embeddings and taste heads

Stores each candidate's cover photo as an image-encoder vector and, per bucket, a small taste
head trained on verdicts. Embeddings go away with their candidate.

Revision ID: 20261115a001
Revises: 20261107a001
Create Date: 2026-11-15 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261115a001"
down_revision: Union[str, Sequence[str], None] = "20261107a001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "listing_embeddings",
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("encoder", sa.String(length=64), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("head_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("candidate_id"),
    )
    op.create_table(
        "taste_heads",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("clothing_item", sa.String(length=32), nullable=False),
        sa.Column("encoder", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("weights", sa.LargeBinary(), nullable=False),
        sa.Column("like_count", sa.Integer(), nullable=False),
        sa.Column("dislike_count", sa.Integer(), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "clothing_item"),
    )


def downgrade() -> None:
    op.drop_table("taste_heads")
    op.drop_table("listing_embeddings")
