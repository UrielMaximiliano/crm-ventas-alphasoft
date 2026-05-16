"""Agrega columnas de intel del LLM al lead: score, reason, site_analysis,
   pain_points, extracted_contacts.

Las llena el job `enrich` cuando ademas del website_check llama al LLM con
el HTML del sitio del negocio para analisis profundo + extraccion ofuscada
de contactos + scoring de prioridad.

Revision ID: 0002_lead_intel
Revises: 0001_initial
Create Date: 2026-05-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002_lead_intel"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("priority_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("priority_reason", sa.String(255), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("site_analysis", sa.Text(), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("pain_points", sa.Text(), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("recommended_service", sa.String(120), nullable=True),
    )
    # Lista de emails/telefonos extra encontrados (separados por |)
    op.add_column(
        "leads",
        sa.Column("extracted_emails", sa.Text(), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("extracted_phones", sa.Text(), nullable=True),
    )

    op.create_index("ix_leads_priority_score", "leads", ["priority_score"])


def downgrade() -> None:
    op.drop_index("ix_leads_priority_score", table_name="leads")
    op.drop_column("leads", "extracted_phones")
    op.drop_column("leads", "extracted_emails")
    op.drop_column("leads", "recommended_service")
    op.drop_column("leads", "pain_points")
    op.drop_column("leads", "site_analysis")
    op.drop_column("leads", "priority_reason")
    op.drop_column("leads", "priority_score")
