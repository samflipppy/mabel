"""Closed vocabularies. Every one of these mirrors a CHECK constraint in
01-SCHEMA.sql. If you add a member here, add it to the constraint in the same
PR, or the database will reject a row the type system said was fine.
"""

from __future__ import annotations

from enum import StrEnum


class TenantStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    CHURNED = "churned"


class UserRole(StrEnum):
    OWNER = "owner"
    OFFICE = "office"
    TECH = "tech"
    INTERNAL = "internal"


class Trade(StrEnum):
    """The seven trades with a shipped ruleset. `tenants.trade` is free text in
    the schema so a shop can be onboarded before its ruleset is written; this
    enum is what the verticals engine knows about."""

    PLUMBING = "plumbing"
    HVAC = "hvac"
    ELECTRICAL = "electrical"
    RESTORATION = "restoration"
    ROOFING = "roofing"
    LOCKSMITH = "locksmith"
    TOWING = "towing"


class Urgency(StrEnum):
    ROUTINE = "routine"
    SOON = "soon"
    EMERGENCY = "emergency"


class Severity(StrEnum):
    """What a matched emergency trigger costs the owner in sleep."""

    WAKE_NOW = "wake_now"
    MORNING = "morning"
    ROUTINE = "routine"


class CallOutcome(StrEnum):
    LEAD = "lead"
    EMERGENCY = "emergency"
    EXISTING_CUSTOMER = "existing_customer"
    SPAM = "spam"
    WRONG_NUMBER = "wrong_number"
    HANGUP = "hangup"
    TRANSFERRED = "transferred"
    FAILED = "failed"


class LeadStatus(StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    ESTIMATE_SCHEDULED = "estimate_scheduled"
    ESTIMATE_SENT = "estimate_sent"
    WON = "won"
    LOST = "lost"
    SPAM = "spam"


class EventKind(StrEnum):
    CALL = "call"
    SMS_IN = "sms_in"
    SMS_OUT = "sms_out"
    EMAIL_IN = "email_in"
    EMAIL_OUT = "email_out"
    NOTE = "note"
    ESTIMATE = "estimate"
    PHOTO = "photo"
    STATUS_CHANGE = "status_change"
    IDENTITY_MERGED = "identity_merged"
    SYSTEM = "system"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL = "internal"


class NotificationKind(StrEnum):
    EMERGENCY = "emergency"
    MORNING_RECAP = "morning_recap"
    WEEKLY_SUMMARY = "weekly_summary"
    FOLLOWUP_NUDGE = "followup_nudge"
    MONTHLY_REPORT = "monthly_report"
    SYSTEM = "system"


class NotificationChannel(StrEnum):
    SMS = "sms"
    EMAIL = "email"
    PUSH = "push"


class NotificationStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


class AppointmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    CANCELLED = "cancelled"


class IntegrationProvider(StrEnum):
    GOOGLE_CALENDAR = "google_calendar"
    JOBBER = "jobber"
    HOUSECALL_PRO = "housecall_pro"
    WEBHOOK = "webhook"


class IntegrationStatus(StrEnum):
    CONNECTED = "connected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class Plan(StrEnum):
    MABEL = "mabel"
    FULLTIME = "fulltime"
    PLUS = "plus"


class QaFlag(StrEnum):
    """Set by the post-call QA pass. Present on `calls.qa_flags`."""

    QUOTED_PRICE = "quoted_price"
    MISSED_EMERGENCY = "missed_emergency"
    OVER_ESCALATED = "over_escalated"
    LOST_CALLER_EARLY = "lost_caller_early"
    PROMISED_ARRIVAL = "promised_arrival"
    CAPTURE_INCOMPLETE = "capture_incomplete"


class NeverSay(StrEnum):
    """The default contents of `agent_configs.never_say`."""

    PRICE = "price"
    ESTIMATE_RANGE = "estimate_range"
    HOURLY_RATE = "hourly_rate"
    ARRIVAL_TIME = "arrival_time"
