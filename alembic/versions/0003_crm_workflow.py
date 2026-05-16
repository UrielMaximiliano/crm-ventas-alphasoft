"""CRM workflow: notas, tags, follow-ups y clasificacion de respuestas

Tablas nuevas:
- lead_notes: comentarios libres del equipo por lead (historial)
- lead_tags: many-to-many con tags arbitrarios (#caliente, #esperando-presupuesto)
- follow_up_tasks: cadencia automatica de recontactos
- reply_classifications: cuando el cliente responde, el LLM clasifica e indica
  el proximo paso

Tambien agrego en `leads`:
- next_followup_at: cuando recontactar (lo setea el scheduler / generate_followup)
- last_reply_at: ultima vez que el cliente respondio
- conversion_value_estimate: estimacion de valor en USD (lo da el LLM)

Revision ID: 0003_crm_workflow
Revises: 0002_lead_intel
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003_crm_workflow"
down_revision: Union[str, None] = "0002_lead_intel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


follow_up_status = postgresql.ENUM(
    "pending", "done", "skipped", "failed",
    name="follow_up_status",
    create_type=False,
)
reply_intent = postgresql.ENUM(
    "interested", "pricing_objection", "not_interested",
    "ask_info", "ask_meeting", "wrong_contact", "spam", "unknown",
    name="reply_intent",
    create_type=False,
)


def upgrade() -> None:
    # Columnas extra en leads para tracking de followups y conversion
    op.add_column(
        "leads",
        sa.Column("next_followup_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("last_reply_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("conversion_value_estimate", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_leads_next_followup_at", "leads", ["next_followup_at"]
    )

    # Tabla de notas (historial del equipo)
    op.create_table(
        "lead_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lead_id",
            sa.Integer(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Tags simples (m2m por id)
    op.create_table(
        "lead_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lead_id",
            sa.Integer(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("lead_id", "tag", name="uq_lead_tag"),
    )
    op.create_index("ix_lead_tags_tag", "lead_tags", ["tag"])

    # Crear ENUMs explicitamente con SQL para evitar duplicados via sa.Enum.
    op.execute(
        "CREATE TYPE follow_up_status AS ENUM "
        "('pending', 'done', 'skipped', 'failed')"
    )
    op.execute(
        "CREATE TYPE reply_intent AS ENUM "
        "('interested', 'pricing_objection', 'not_interested', "
        "'ask_info', 'ask_meeting', 'wrong_contact', 'spam', 'unknown')"
    )

    # Follow-up tasks programadas
    op.create_table(
        "follow_up_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lead_id",
            sa.Integer(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("kind", sa.String(32), nullable=False, server_default="followup"),
        sa.Column(
            "status",
            follow_up_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("message_id", sa.Integer(),
                  sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Clasificacion de respuestas del cliente por el LLM
    op.create_table(
        "reply_classifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lead_id",
            sa.Integer(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("raw_reply", sa.Text(), nullable=False),
        sa.Column(
            "intent",
            reply_intent,
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("sentiment", sa.String(16), nullable=True),  # positive/neutral/negative
        sa.Column("summary", sa.String(512), nullable=True),
        sa.Column("suggested_action", sa.Text(), nullable=True),
        sa.Column("suggested_reply", sa.Text(), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("reply_classifications")
    op.execute("DROP TYPE IF EXISTS reply_intent")
    op.drop_table("follow_up_tasks")
    op.execute("DROP TYPE IF EXISTS follow_up_status")
    op.drop_index("ix_lead_tags_tag", table_name="lead_tags")
    op.drop_table("lead_tags")
    op.drop_table("lead_notes")
    op.drop_index("ix_leads_next_followup_at", table_name="leads")
    op.drop_column("leads", "conversion_value_estimate")
    op.drop_column("leads", "last_reply_at")
    op.drop_column("leads", "next_followup_at")
