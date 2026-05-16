from __future__ import annotations

import enum
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


EMBEDDING_DIM = 384


class Base(DeclarativeBase):
    pass


class LeadStatus(str, enum.Enum):
    NEW = "new"
    QUALIFIED = "qualified"
    CONTACTED = "contacted"
    REPLIED = "replied"
    DISCARDED = "discarded"


class MessageChannel(str, enum.Enum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class FollowUpStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class ReplyIntent(str, enum.Enum):
    INTERESTED = "interested"
    PRICING_OBJECTION = "pricing_objection"
    NOT_INTERESTED = "not_interested"
    ASK_INFO = "ask_info"
    ASK_MEETING = "ask_meeting"
    WRONG_CONTACT = "wrong_contact"
    SPAM = "spam"
    UNKNOWN = "unknown"


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("name", "address_normalized", name="uq_lead_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    province: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str] = mapped_column(String(8), nullable=False, default="AR")
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    address: Mapped[str | None] = mapped_column(String(512), nullable=True)
    address_normalized: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviews_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[str] = mapped_column(String(64), nullable=False, default="mock")
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    search_query: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[LeadStatus] = mapped_column(
        SAEnum(
            LeadStatus,
            name="lead_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=LeadStatus.NEW,
    )
    qualified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qualification_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Intel del LLM (lo llena el job enrich + analyze_lead). Null si nunca se analizo.
    priority_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    priority_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    pain_points: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_service: Mapped[str | None] = mapped_column(String(120), nullable=True)
    extracted_emails: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_phones: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Workflow comercial: cuando recontactar, ultima respuesta, valor estimado
    next_followup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_reply_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    conversion_value_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
    )
    notes: Mapped[list["LeadNote"]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="LeadNote.created_at.desc()",
    )
    tags: Mapped[list["LeadTag"]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
    )
    follow_ups: Mapped[list["FollowUpTask"]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="FollowUpTask.scheduled_for",
    )
    reply_classifications: Mapped[list["ReplyClassification"]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        order_by="ReplyClassification.created_at.desc()",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[MessageChannel] = mapped_column(
        SAEnum(
            MessageChannel,
            name="message_channel",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
    )
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    lead: Mapped[Lead] = relationship(back_populates="messages")


class CatalogItem(Base):
    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    short_description: Mapped[str] = mapped_column(String(512), nullable=False)
    long_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_audience: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price_range: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class JobRun(Base):
    """Bitacora minima de cuando corrio cada job, para idempotencia."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    items_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LeadNote(Base):
    """Comentarios libres del equipo por lead. Historial de actividad."""

    __tablename__ = "lead_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    lead: Mapped[Lead] = relationship(back_populates="notes")


class LeadTag(Base):
    """Tags arbitrarios para segmentacion (#caliente, #esperando-respuesta)."""

    __tablename__ = "lead_tags"
    __table_args__ = (UniqueConstraint("lead_id", "tag", name="uq_lead_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tag: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    lead: Mapped[Lead] = relationship(back_populates="tags")


class FollowUpTask(Base):
    """Cadencia de recontactos programados.

    Cada lead contacted que no respondio genera follow-ups en 5/14/30 dias
    con angulos diferentes. El scheduler los dispara y los marca como done
    cuando el LLM genero el nuevo mensaje.
    """

    __tablename__ = "follow_up_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="followup")
    status: Mapped[FollowUpStatus] = mapped_column(
        SAEnum(
            FollowUpStatus,
            name="follow_up_status",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=FollowUpStatus.PENDING,
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lead: Mapped[Lead] = relationship(back_populates="follow_ups")


class ReplyClassification(Base):
    """Cuando el cliente responde, el LLM lo lee y clasifica:
    intent (interested, pricing_objection, ...), sentiment, summary,
    suggested_action y suggested_reply. Hace que el equipo sepa que hacer
    sin leer el thread completo.
    """

    __tablename__ = "reply_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_reply: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[ReplyIntent] = mapped_column(
        SAEnum(
            ReplyIntent,
            name="reply_intent",
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
        ),
        nullable=False,
        default=ReplyIntent.UNKNOWN,
    )
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    lead: Mapped[Lead] = relationship(back_populates="reply_classifications")
