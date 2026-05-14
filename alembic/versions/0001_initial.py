"""initial schema (leads, messages, catalog_items, job_runs) + pgvector

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EMBEDDING_DIM = 384

# Los enums se crean una sola vez via raw SQL; las columnas referencian el tipo
# con create_type=False para evitar el "type already exists" duplicado.
lead_status = postgresql.ENUM(
    "new", "qualified", "contacted", "replied", "discarded",
    name="lead_status",
    create_type=False,
)
message_channel = postgresql.ENUM(
    "whatsapp", "email",
    name="message_channel",
    create_type=False,
)


def upgrade() -> None:
    # Extension para embeddings
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Tipos enum (uno solo, explicito)
    op.execute(
        "CREATE TYPE lead_status AS ENUM "
        "('new', 'qualified', 'contacted', 'replied', 'discarded')"
    )
    op.execute("CREATE TYPE message_channel AS ENUM ('whatsapp', 'email')")

    # leads
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("province", sa.String(120), nullable=True),
        sa.Column("country", sa.String(8), nullable=False, server_default="AR"),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("website", sa.String(512), nullable=True),
        sa.Column("address", sa.String(512), nullable=True),
        sa.Column("address_normalized", sa.String(512), nullable=False, server_default=""),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("reviews_count", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="mock"),
        sa.Column("source_id", sa.String(255), nullable=True),
        sa.Column("search_query", sa.String(255), nullable=True),
        sa.Column(
            "status",
            lead_status,
            nullable=False,
            server_default="new",
        ),
        sa.Column("qualified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("qualification_reason", sa.String(255), nullable=True),
        sa.Column("website_status", sa.String(64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", "address_normalized", name="uq_lead_identity"),
    )
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_city", "leads", ["city"])
    op.create_index("ix_leads_category", "leads", ["category"])
    op.create_index("ix_leads_qualified", "leads", ["qualified"])

    # messages
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lead_id",
            sa.Integer(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", message_channel, nullable=False),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_messages_lead_id", "messages", ["lead_id"])

    # catalog_items
    op.create_table(
        "catalog_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("short_description", sa.String(512), nullable=False),
        sa.Column("long_description", sa.Text(), nullable=True),
        sa.Column("target_audience", sa.String(255), nullable=True),
        sa.Column("price_range", sa.String(120), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # job_runs
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_name", sa.String(64), nullable=False),
        sa.Column("key", sa.String(255), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("items_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_job_runs_job_name", "job_runs", ["job_name"])
    op.create_index("ix_job_runs_key", "job_runs", ["key"])


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_table("catalog_items")
    op.drop_table("messages")
    op.drop_table("leads")
    op.execute("DROP TYPE IF EXISTS message_channel")
    op.execute("DROP TYPE IF EXISTS lead_status")
    op.execute("DROP EXTENSION IF EXISTS vector")
