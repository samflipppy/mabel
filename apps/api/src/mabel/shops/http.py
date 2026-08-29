"""Admin shop write path. Not an MCP tool. The model must not create tenants."""

from __future__ import annotations

from datetime import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from mabel.leads.overnight import office_lead_view, overnight_leads
from mabel.platform.config import ConfigError
from mabel.platform.tenancy import DuplicateDidError
from mabel.shops.auth import AdminAuthError, verify_admin_authorization
from mabel.shops.onboard import onboard_shop
from mabel.shops.packet import DEFAULT_TIMEZONE, PacketError, ShopPacket
from mabel.shops.store import SHOP_STATUS_DRAFT
from mabel.shops.update import UnknownShopError, load_shop, update_shop

router = APIRouter()


class OnboardShopBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    vertical: str
    inbound_did: str
    owner_sms_e164: str
    service_area_zips: list[str] = Field(default_factory=list)
    timezone: str = DEFAULT_TIMEZONE
    after_hours_start: time | None = None
    after_hours_end: time | None = None
    greeting_notes: str | None = None


class PatchShopBody(BaseModel):
    """Owner settings. Emergency vertical rules are not editable here."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    timezone: str | None = None
    owner_sms_e164: str | None = None
    after_hours_start: time | None = None
    after_hours_end: time | None = None
    service_area_zips: list[str] | None = None
    greeting_notes: str | None = None


def _require_admin(request: Request) -> None:
    try:
        verify_admin_authorization(request.headers.get("authorization"))
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AdminAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _shop_payload(
    packet: ShopPacket,
    *,
    inbound_did: str | None,
    status: str,
) -> dict[str, object]:
    return {
        "tenant_id": str(packet.tenant_id),
        "name": packet.name,
        "vertical": packet.vertical,
        "inbound_did": inbound_did,
        "status": status,
        "timezone": packet.timezone,
        "owner_sms_e164": packet.owner_sms_e164,
        "after_hours_start": packet.after_hours_start.strftime("%H:%M"),
        "after_hours_end": packet.after_hours_end.strftime("%H:%M"),
        "service_area_zips": list(packet.service_area_zips),
        "greeting_notes": packet.greeting_notes,
        "xai_voice_agent_id": packet.xai_voice_agent_id,
        "live": False,
        "draft": status == SHOP_STATUS_DRAFT,
    }


@router.post("/shops", status_code=201)
def create_shop(body: OnboardShopBody, request: Request) -> dict[str, object]:
    _require_admin(request)
    if "tenant_id" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="Mabel assigns the tenant. Do not send tenant_id.",
        )
    try:
        shop = onboard_shop(
            name=body.name,
            vertical=body.vertical,
            inbound_did=body.inbound_did,
            owner_sms_e164=body.owner_sms_e164,
            service_area_zips=body.service_area_zips,
            timezone=body.timezone,
            after_hours_start=body.after_hours_start,
            after_hours_end=body.after_hours_end,
            greeting_notes=body.greeting_notes,
        )
    except DuplicateDidError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PacketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _shop_payload(
        shop.packet,
        inbound_did=shop.inbound_did,
        status=shop.status,
    )


@router.get("/shops/{tenant_id}")
def get_shop(tenant_id: UUID, request: Request) -> dict[str, object]:
    _require_admin(request)
    try:
        packet, status, inbound_did = load_shop(tenant_id)
    except UnknownShopError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PacketError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _shop_payload(packet, inbound_did=inbound_did, status=status)


@router.patch("/shops/{tenant_id}")
def patch_shop(tenant_id: UUID, body: PatchShopBody, request: Request) -> dict[str, object]:
    _require_admin(request)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Mabel needs something to change.")
    if "vertical" in request.query_params or "emergency" in fields:
        raise HTTPException(
            status_code=400,
            detail="Mabel does not edit emergency rules here.",
        )
    try:
        packet = update_shop(
            tenant_id,
            name=fields.get("name"),
            timezone=fields.get("timezone"),
            owner_sms_e164=fields.get("owner_sms_e164"),
            after_hours_start=fields.get("after_hours_start"),
            after_hours_end=fields.get("after_hours_end"),
            service_area_zips=fields.get("service_area_zips"),
            greeting_notes=fields.get("greeting_notes"),
            greeting_notes_set="greeting_notes" in fields,
        )
        _, status, inbound_did = load_shop(tenant_id)
    except UnknownShopError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PacketError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _shop_payload(packet, inbound_did=inbound_did, status=status)


@router.get("/shops/{tenant_id}/overnight")
def get_overnight(tenant_id: UUID, request: Request) -> dict[str, object]:
    _require_admin(request)
    try:
        packet, _status, _did = load_shop(tenant_id)
    except UnknownShopError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PacketError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    leads = overnight_leads(tenant_id)
    return {
        "tenant_id": str(tenant_id),
        "shop_name": packet.name,
        "leads": [office_lead_view(lead) for lead in leads],
    }
