"""Domain models. One per table in 01-SCHEMA.sql, plus the value objects the
call path passes around.

Pure. No I/O, no DB, no network. These are the shapes that cross a boundary —
`packages/db/queries/` maps rows onto them, the API serialises them, the MCP
tools return them. Nothing here opens a connection.

Money fields are `_cents` integers, typed `int`, and never `float`.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mabel_domain.enums import (
    AppointmentStatus,
    CallOutcome,
    Direction,
    EventKind,
    IntegrationProvider,
    IntegrationStatus,
    LeadStatus,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    Plan,
    TenantStatus,
    Urgency,
    UserRole,
)
from mabel_domain.phone import E164

Cents = Annotated[int, Field(description="Integer cents. Never float.")]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ============================================================
# TENANCY
# ============================================================


class Tenant(Base):
    id: UUID
    business_name: str
    legal_name: str | None = None
    trade: str
    timezone: str = "America/New_York"
    status: TenantStatus = TenantStatus.TRIAL
    did_e164: E164 | None = None
    sip_registered_at: datetime | None = None
    xai_agent_id: str | None = None
    stripe_customer_id: str | None = None
    created_at: datetime
    deleted_at: datetime | None = None

    @field_validator("timezone")
    @classmethod
    def _iana(cls, v: str) -> str:
        # Invariant 6: tenant-local time is computed from an IANA zone, never
        # hardcoded, even though every customer today is in America/New_York.
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"not an IANA timezone: {v!r}") from exc
        return v


class Location(Base):
    id: UUID
    tenant_id: UUID
    name: str
    address: str | None = None
    did_e164: E164 | None = None
    is_primary: bool = False
    deleted_at: datetime | None = None


class User(Base):
    id: UUID
    tenant_id: UUID | None = None  # NULL = internal staff
    supabase_uid: UUID | None = None
    email: str
    full_name: str | None = None
    phone_e164: E164 | None = None
    role: UserRole = UserRole.OFFICE
    notify_emergencies: bool = False
    notify_recap: bool = False
    created_at: datetime
    deleted_at: datetime | None = None


# ============================================================
# CONFIGURATION
# ============================================================


class DayHours(Base):
    open: time
    close: time


class BusinessHours(Base):
    """`agent_configs.business_hours` jsonb. Keys are lowercase three-letter
    day names; a missing day means closed all day."""

    mon: DayHours | None = None
    tue: DayHours | None = None
    wed: DayHours | None = None
    thu: DayHours | None = None
    fri: DayHours | None = None
    sat: DayHours | None = None
    sun: DayHours | None = None

    def for_weekday(self, weekday: int) -> DayHours | None:
        """`weekday` as `datetime.weekday()` — Monday is 0."""
        return (self.mon, self.tue, self.wed, self.thu, self.fri, self.sat, self.sun)[weekday]


class AgentConfig(Base):
    id: UUID
    tenant_id: UUID
    version: int
    is_live: bool = False

    greeting: str
    voice: str = "carina"
    speaking_rate: float = 1.0

    services: list[str] = Field(default_factory=list)
    service_area_zips: list[str] = Field(default_factory=list)
    service_area_note: str | None = None

    business_hours: BusinessHours
    after_hours_only: bool = True

    never_say: list[str] = Field(
        default_factory=lambda: ["price", "estimate_range", "hourly_rate", "arrival_time"]
    )
    custom_rules: str | None = None
    keyterms: list[str] = Field(default_factory=list)

    vertical_ruleset_id: UUID | None = None
    emergency_overrides: dict[str, Any] = Field(default_factory=dict)

    created_by: UUID | None = None
    created_at: datetime
    published_at: datetime | None = None

    @field_validator("speaking_rate")
    @classmethod
    def _rate_in_range(cls, v: float) -> float:
        # numeric(3,2) in the schema, and a rate outside this range makes her
        # unintelligible on a G.711 line.
        if not 0.5 <= v <= 2.0:
            raise ValueError(f"speaking_rate must be between 0.5 and 2.0, got {v}")
        return v

    @field_validator("never_say")
    @classmethod
    def _price_always_forbidden(cls, v: list[str]) -> list[str]:
        # Invariant 4. A tenant may add to this list. They may not remove
        # `price` — that one is not theirs to turn off.
        if "price" not in v:
            raise ValueError("never_say must always contain 'price'")
        return v


class KnowledgeItem(Base):
    id: UUID
    tenant_id: UUID
    question: str
    answer: str
    sort_order: int = 0
    is_active: bool = True
    updated_at: datetime


class OncallShift(Base):
    user_id: UUID
    days: list[int]  # 1 = Monday, matching the rotation jsonb in the schema
    start: time
    end: time


class OncallSchedule(Base):
    id: UUID
    tenant_id: UUID
    name: str
    rotation: list[OncallShift]
    is_active: bool = True


class VerticalRuleset(Base):
    id: UUID
    trade: str
    version: int
    effective_from: date
    rules: dict[str, Any]
    verified_by: UUID | None = None
    verified_at: datetime | None = None


# ============================================================
# CONTACTS & THREAD
# ============================================================


class Contact(Base):
    id: UUID
    tenant_id: UUID
    display_name: str | None = None
    primary_phone: E164 | None = None
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    addresses: list[dict[str, Any]] = Field(default_factory=list)
    merged_into: UUID | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    deleted_at: datetime | None = None

    @property
    def is_merged_away(self) -> bool:
        return self.merged_into is not None


class CommunicationEvent(Base):
    """Append-only. One row per interaction, never updated."""

    id: UUID
    tenant_id: UUID
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    kind: EventKind
    direction: Direction | None = None
    occurred_at: datetime
    body: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    storage_path: str | None = None
    actor_user_id: UUID | None = None
    created_at: datetime


# ============================================================
# CALLS
# ============================================================


class TranscriptTurn(Base):
    role: Literal["assistant", "caller", "system"]
    text: str
    started_ms: int | None = None
    ended_ms: int | None = None


class Transcript(Base):
    id: UUID
    tenant_id: UUID
    call_id: UUID
    turns: list[TranscriptTurn]
    full_text: str | None = None
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    created_at: datetime


class Call(Base):
    id: UUID
    tenant_id: UUID
    location_id: UUID | None = None
    contact_id: UUID | None = None
    lead_id: UUID | None = None

    xai_call_id: str | None = None
    telnyx_call_id: str | None = None
    from_e164: E164 | None = None
    to_e164: E164 | None = None

    started_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_sec: int | None = None

    outcome: CallOutcome | None = None
    agent_config_id: UUID | None = None

    recording_path: str | None = None
    archived_at: datetime | None = None

    voice_cost_cents: Cents | None = None
    telephony_cost_cents: Cents | None = None

    qa_flags: list[str] = Field(default_factory=list)
    qa_reviewed_at: datetime | None = None

    created_at: datetime

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


# ============================================================
# LEADS & JOBS
# ============================================================


class Lead(Base):
    id: UUID
    tenant_id: UUID
    contact_id: UUID | None = None
    call_id: UUID | None = None

    caller_name: str | None = None
    service_address: str | None = None
    callback_e164: E164 | None = None
    job_type: str | None = None
    description: str | None = None
    urgency: Urgency = Urgency.ROUTINE
    source: str | None = None

    status: LeadStatus = LeadStatus.NEW
    value_cents: Cents | None = None  # owner-entered. Never computed.
    currency: str = "USD"
    lost_reason: str | None = None

    escalated_at: datetime | None = None
    first_touched_at: datetime | None = None
    won_at: datetime | None = None

    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _won_needs_a_won_at(self) -> Lead:
        if self.status is LeadStatus.WON and self.won_at is None:
            raise ValueError("a won lead must carry won_at")
        return self

    @property
    def is_untouched(self) -> bool:
        return self.first_touched_at is None and self.status is LeadStatus.NEW


class Appointment(Base):
    id: UUID
    tenant_id: UUID
    lead_id: UUID | None = None
    contact_id: UUID | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    kind: str = "estimate"
    status: AppointmentStatus = AppointmentStatus.SCHEDULED
    external_ref: str | None = None
    created_at: datetime


# ============================================================
# NOTIFICATIONS
# ============================================================


class Notification(Base):
    id: UUID
    tenant_id: UUID
    user_id: UUID | None = None
    kind: NotificationKind
    channel: NotificationChannel
    to_address: str
    body: str
    lead_id: UUID | None = None
    status: NotificationStatus = NotificationStatus.QUEUED
    provider_ref: str | None = None
    error: str | None = None
    scheduled_for: datetime | None = None
    sent_at: datetime | None = None
    created_at: datetime


class SmsSession(Base):
    id: UUID
    tenant_id: UUID
    user_id: UUID | None = None
    phone_e164: E164
    context: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime
    updated_at: datetime


# ============================================================
# INTEGRATIONS
# ============================================================


class Integration(Base):
    id: UUID
    tenant_id: UUID
    provider: IntegrationProvider
    status: IntegrationStatus = IntegrationStatus.CONNECTED
    external_account_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    vault_key: str | None = None  # tokens live in the vault, never here
    last_synced_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime

    @field_validator("config")
    @classmethod
    def _no_tokens_in_config(cls, v: dict[str, Any]) -> dict[str, Any]:
        # We never hold a tenant's credentials. The vault holds the token; we
        # hold the key name. Catching it here means a careless integration
        # handler fails loudly instead of quietly persisting a refresh token.
        forbidden = {"access_token", "refresh_token", "client_secret", "api_key", "password"}
        leaked = forbidden & set(v)
        if leaked:
            raise ValueError(
                f"credentials belong in the vault, not integrations.config: {sorted(leaked)}"
            )
        return v


class IntegrationEvent(Base):
    id: UUID
    tenant_id: UUID
    integration_id: UUID | None = None
    direction: str
    entity: str | None = None
    external_ref: str | None = None
    status: str
    payload: dict[str, Any] | None = None
    created_at: datetime


# ============================================================
# BILLING & USAGE
# ============================================================


class Subscription(Base):
    id: UUID
    tenant_id: UUID
    stripe_subscription_id: str | None = None
    plan: Plan
    price_cents: Cents
    currency: str = "USD"
    included_minutes: int
    overage_cents_per_min: Cents = 0
    addons: dict[str, Any] = Field(default_factory=dict)
    status: str
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at: datetime | None = None
    created_at: datetime


class UsageDay(Base):
    tenant_id: UUID
    day: date
    calls_answered: int = 0
    voice_minutes: float = 0.0  # numeric(10,2) — minutes, not money
    sms_sent: int = 0
    leads_created: int = 0
    emergencies: int = 0
    cost_cents: Cents = 0


class MonthlyReport(Base):
    id: UUID
    tenant_id: UUID
    period_start: date
    period_end: date
    calls_answered: int
    leads_created: int
    emergencies: int
    jobs_won: int
    won_value_cents: Cents
    source_breakdown: dict[str, int] = Field(default_factory=dict)
    untouched_leads: list[dict[str, Any]] = Field(default_factory=list)
    pdf_path: str | None = None
    sent_at: datetime | None = None
    created_at: datetime


# ============================================================
# QUEUE & AUDIT
# ============================================================


class QueuedJob(Base):
    id: int
    tenant_id: UUID | None = None
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    run_after: datetime
    attempts: int = 0
    max_attempts: int = 5
    locked_at: datetime | None = None
    locked_by: str | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime


class AuditEntry(Base):
    id: int
    tenant_id: UUID | None = None
    actor_id: UUID | None = None
    actor_type: Literal["user", "system", "agent", "internal"] = "user"
    action: str
    entity: str | None = None
    entity_id: UUID | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    ip: str | None = None
    created_at: datetime
