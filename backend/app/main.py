import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api import action1, admin, attachments, auth, branding, changes, checklists, devices, directory, fields, integrations, ipam, kb, meraki, network, notices, portal, reports, rules, surveys, system, tickets, vendors
from app.api import adchanges as adchanges_api
from app.api import layouts as layouts_api
from app.api import permissions as permissions_api
from app.api import purchasing as purchasing_api
from app.api import receiving as receiving_api
from app.api import remote as remote_api
from app.api import runs as runs_api
from app.api import script_schedules as script_schedules_api
from app.api import scripts as scripts_api
from app.api import teams_chat as teams_chat_api
from app.api import wallboards as wallboards_api
from app.api import settings as settings_api
from app import syslog
from app.config import settings
from app.db import engine
from app.rules import engine as _rules_engine  # noqa: F401  (registers the on-save rule hooks)
from app.services import ServiceError

SECURITY_HEADERS = {
    # Everything the UI needs is served from here, fonts included.
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Warnings, errors and notable events go to Settings > Log as well as stderr.
    syslog.install(engine, "api")
    logging.getLogger("app").info("API started", extra={"source": "server"})
    yield


from app.meraki_oauth import RedactCallbackQuery

app = FastAPI(title="Layer 8", version="0.1.0", lifespan=lifespan)
app.add_middleware(RedactCallbackQuery)
app.include_router(auth.router)
app.include_router(teams_chat_api.router)
app.include_router(tickets.router)
app.include_router(admin.router)
app.include_router(attachments.router)
app.include_router(portal.router)
app.include_router(rules.router)
app.include_router(directory.router)
app.include_router(settings_api.router)
app.include_router(system.router)
app.include_router(reports.router)
app.include_router(fields.router)
app.include_router(branding.router)
app.include_router(notices.router)
app.include_router(devices.router)
app.include_router(scripts_api.router)
app.include_router(runs_api.router)
app.include_router(remote_api.router)
app.include_router(vendors.router)
app.include_router(ipam.router)
app.include_router(network.router)
app.include_router(integrations.router)
app.include_router(wallboards_api.router)
app.include_router(action1.router)
app.include_router(meraki.router)
app.include_router(checklists.router)
app.include_router(changes.router)
app.include_router(kb.router)
app.include_router(surveys.router)
app.include_router(permissions_api.router)
app.include_router(purchasing_api.router)
app.include_router(receiving_api.router)
app.include_router(adchanges_api.router)
app.include_router(layouts_api.router)
app.include_router(script_schedules_api.router)


@app.exception_handler(ServiceError)
def service_error(_: Request, exc: ServiceError):
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.exception_handler(IntegrityError)
def integrity_error(_: Request, exc: IntegrityError):
    # Usually a duplicate: email, queue name, directory ID.
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": "Conflicts with existing data"})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        # Downloads set a stricter policy of their own.
        response.headers.setdefault(name, value)
    return response


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    """The built agent UI. Unknown paths get index.html so client-side routes work on reload."""
    root = settings.frontend_dir.resolve()
    if path.startswith("api/") or not (root / "index.html").exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    file = (root / path).resolve()
    if path and file.is_file() and file.is_relative_to(root):
        cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        return FileResponse(file, headers={"Cache-Control": cache})
    if path.startswith("assets/"):
        # A file from an older build (a tab open across an update): a 404 lets the page notice and
        # reload, where index.html would be refused as a script.
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
