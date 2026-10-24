# SPDX-License-Identifier: AGPL-3.0-or-later
"""Screen layouts (app.layouts): everyone's default (admins) and each technician's own."""

import logging

from fastapi import APIRouter, HTTPException, status

from app import layouts
from app.auth import Admin, Agent, SessionDep
from app.schemas import LayoutIn, LayoutOut, PageLayoutOut

router = APIRouter(tags=["layouts"])
log = logging.getLogger(__name__)


def _page(page: str) -> str:
    if page not in layouts.PANELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No page called {page!r}")
    return page


def _out(session, user, page: str) -> PageLayoutOut:
    own = layouts.mine(user, page)
    return PageLayoutOut(
        page=page, effective=LayoutOut(**layouts.effective(session, user, page)), default=LayoutOut(**layouts.default(session, page)),
        mine=LayoutOut(**own) if own else None, required=sorted(layouts.REQUIRED.get(page, set())),
    )


@router.get("/api/layouts/{page}", response_model=PageLayoutOut)
def get_layout(page: str, user: Agent, session: SessionDep):
    return _out(session, user, _page(page))


@router.put("/api/layouts/{page}/mine", response_model=PageLayoutOut)
def save_mine(page: str, body: LayoutIn, user: Agent, session: SessionDep):
    """My own arrangement of this page."""
    layouts.set_mine(user, _page(page), body.model_dump())
    session.commit()
    return _out(session, user, page)


@router.delete("/api/layouts/{page}/mine", response_model=PageLayoutOut)
def reset_mine(page: str, user: Agent, session: SessionDep):
    """Back to everyone's default."""
    layouts.set_mine(user, _page(page), None)
    session.commit()
    return _out(session, user, page)


@router.put("/api/layouts/{page}/default", response_model=PageLayoutOut)
def save_default(page: str, body: LayoutIn, admin: Admin, session: SessionDep):
    """Everyone's arrangement of this page (technicians with their own keep theirs)."""
    layouts.set_default(session, _page(page), body.model_dump(), admin)
    session.commit()
    log.info("%s changed the default %s page layout", admin.display_name, page, extra={"source": "settings"})
    return _out(session, admin, page)


@router.delete("/api/layouts/{page}/default", response_model=PageLayoutOut)
def reset_default(page: str, admin: Admin, session: SessionDep):
    """Everyone's default back to the app's usual layout."""
    layouts.set_default(session, _page(page), None, admin)
    session.commit()
    return _out(session, admin, page)
