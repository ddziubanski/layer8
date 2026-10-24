"""Meraki credentials remain admin-only; technicians can read cached network observations."""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app import meraki
from app.auth import Admin, Agent, SessionDep
from app.config import settings
from app.models import MerakiClient, WorkerJob
from app.schemas import MerakiClientOut, MerakiStatusOut, MerakiTestOut

router = APIRouter(prefix="/api/meraki", tags=["meraki"])


@router.get("/status", response_model=MerakiStatusOut)
def status(_: Admin, session: SessionDep):
    job = session.get(WorkerJob, "meraki")
    count = session.scalar(select(func.count()).select_from(meraki.visible(session).subquery())) if meraki.on() else 0
    return dict(enabled=settings.meraki_enabled, configured=bool(settings.meraki_api_key and meraki.network_ids()),
                clients=count, networks=meraki.network_ids(), last_sync_at=job.last_finished_at if job else None,
                last_sync_ok=job.last_ok if job else None, last_sync_error=job.last_error if job else None,
                last_sync_summary=job.last_summary if job else None)


@router.post("/test", response_model=MerakiTestOut)
def test(_: Admin):
    client = None
    try:
        client = meraki.from_settings()
        return {"networks": [client.network(n)["name"] for n in meraki.network_ids()]}
    except meraki.MerakiError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        if client:
            client.close()


@router.get("/clients", response_model=list[MerakiClientOut])
def clients(_: Agent, session: SessionDep, q: str = Query("", max_length=100), limit: int = Query(200, ge=1, le=1000)):
    if not meraki.on():
        return []
    stmt = meraki.visible(session)
    if q.strip():
        stmt = stmt.where(MerakiClient.description.icontains(q.strip(), autoescape=True) |
                          MerakiClient.address.icontains(q.strip(), autoescape=True) |
                          MerakiClient.mac.icontains(q.strip(), autoescape=True) |
                          MerakiClient.network_name.icontains(q.strip(), autoescape=True))
    return [meraki.as_dict(item) for item in session.scalars(stmt.order_by(MerakiClient.network_id, MerakiClient.mac).limit(limit))]
