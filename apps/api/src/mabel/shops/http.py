"""Admin shop write path. Not an MCP tool. The model must not create tenants."""

from __future__ import annotations

from datetime import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from mabel.platform.config import ConfigError
from mabel.platform.tenancy import DuplicateDidError
from mabel.shops.auth import AdminAuthError, verify_admin_authorization
from mabel.shops.onboard import onboard_shop
from mabel.shops.packet import DEFAULT_TIMEZONE, PacketError
from mabel.shops.store import SHOP_STATUS_DRAFT

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


@router.post("/shops", status_code=201)
def create_shop(body: OnboardShopBody, request: Request) -> dict[str, object]:
    try:
        verify_admin_authorization(request.headers.get("authorization"))
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AdminAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

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

    return {
        "tenant_id": str(shop.tenant_id),
        "name": shop.packet.name,
        "vertical": shop.packet.vertical,
        "inbound_did": shop.inbound_did,
        "status": shop.status,
        "timezone": shop.packet.timezone,
        "service_area_zips": list(shop.packet.service_area_zips),
        "xai_voice_agent_id": shop.packet.xai_voice_agent_id,
        "live": False,
        "draft": shop.status == SHOP_STATUS_DRAFT,
    }
