"""add the buckets table and switch search category filters to catalog ids

Revision ID: 20261105a004
Revises: 20261105a003
Create Date: 2026-11-05 00:00:03.000000

"""
import json
from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261105a004"
down_revision: Union[str, Sequence[str], None] = "20261105a003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# key, label, description, catalog ids, size keywords: the buckets that were hardcoded until now.
_BUCKETS = [
    ("schuhe", "Schuhe", "Shoes and sneakers.", [16, 2632, 2630, 1049], ["schuhe"]),
    ("hosen", "Hosen", "Trousers, jeans, cargos, shorts, and other legwear.", [9, 183, 15, 573, 1070, 1071], ["hose"]),
    (
        "obenrum_warm",
        "Obenrum Warm",
        "Warm-weather tops such as T-shirts and short-sleeve shirts.",
        [12, 221, 222, 1043, 534, 1041],
        ["hemd", "shirt", "kleidergroesse"],
    ),
    (
        "obenrum_mittel",
        "Obenrum Mittel",
        "Medium-layer tops such as longsleeves and light pullovers.",
        [12, 13, 222, 1043, 196, 1917, 194, 1045],
        ["hemd", "shirt", "kleidergroesse"],
    ),
    (
        "obenrum_kalt",
        "Obenrum Kalt",
        "Cold-weather upper-body pieces such as heavy pullovers and jackets.",
        [196, 13, 1037, 1908, 1907, 2524, 1086, 2532, 2614, 1078, 1079, 2529, 2528],
        ["jacke", "pullover", "kleidergroesse"],
    ),
    ("kopf", "Kopf", "Headwear, especially funny or weird baseball caps.", [88], ["hut", "huete", "muetze"]),
    ("ringe", "Ringe", "Rings worn on the fingers.", [553], ["ring"]),
]


def upgrade() -> None:
    buckets = op.create_table(
        "buckets",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("catalog_ids", sa.JSON(), nullable=False),
        sa.Column("size_keywords", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        buckets,
        [
            {
                "key": key,
                "label": label,
                "description": description,
                "catalog_ids": catalog_ids,
                "size_keywords": size_keywords,
                "position": position,
                "archived": False,
                "created_at": now,
                "updated_at": now,
            }
            for position, (key, label, description, catalog_ids, size_keywords) in enumerate(_BUCKETS)
        ],
    )

    # Category filters held alias names ("sneakers"); they now hold catalog ids. Each search
    # gets its bucket's full category set; the next draft apply narrows it again.
    ids_by_key = {key: catalog_ids for key, _, _, catalog_ids, _ in _BUCKETS}
    connection = op.get_bind()
    for search_id, clothing_item, raw_filters in connection.execute(
        sa.text("SELECT id, clothing_item, filters FROM searches")
    ).all():
        filters = json.loads(raw_filters) if isinstance(raw_filters, str) else (raw_filters or [])
        kept = [f for f in filters if str(f.get("field", "")).strip().lower() not in {"category", "catalog"}]
        category = {
            "field": "category",
            "label": "Vinted category",
            "mode": "include",
            "values": [str(catalog_id) for catalog_id in ids_by_key.get(clothing_item, [])],
        }
        connection.execute(
            sa.text("UPDATE searches SET filters = :filters WHERE id = :id"),
            {"filters": json.dumps([category, *kept]), "id": search_id},
        )


def downgrade() -> None:
    # Category filters keep their catalog ids; the old alias lookup does not resolve them.
    op.drop_table("buckets")
