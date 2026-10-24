# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ticket's AD account card (the requester's groups and OU) and onboarding templates."""

import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app import adchanges, permissions
from app.api.runs import _run_out
from app.api.tickets import staff_ticket
from app.auth import Agent, SessionDep
from app.config import settings
from app.directory.types import DirectoryError
from app.models import OnboardingTemplate, User
from app.schemas import (
    ADChangeIn, ADGroupListOut, ADGroupOut, ADPlanOut, NewADAccountIn, NewADAccountOut, NewADAccountPlanOut,
)
from app.schemas import OnboardingTemplateIn, OnboardingTemplateOut, OUOut, RequesterADOut, RunOut

router = APIRouter(tags=["ad changes"])
TemplatesManager = permissions.require("templates_manage")
log = logging.getLogger(__name__)


def _err(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _g(g: adchanges.GroupInfo) -> ADGroupOut:
    return ADGroupOut(dn=g.dn, name=g.name, changeable=g.changeable, nested=g.nested, warning=g.warning)


@router.get("/api/tickets/{ticket_id}/requester-ad", response_model=RequesterADOut)
def requester_ad(ticket_id: int, user: Agent, session: SessionDep):
    """The requester's AD account as AD has it now: direct groups (which may be changed) and OU."""
    ticket = staff_ticket(session, ticket_id, user)
    if not adchanges.enabled():
        return RequesterADOut(enabled=False, problem="AD changes aren't set up (Settings > Directory: the groups technicians may change, and OUs)")
    try:
        account = adchanges.requester_account(ticket)
        groups = adchanges.current_groups(account)
    except adchanges.ADChangeError as exc:
        return RequesterADOut(enabled=True, can_create=adchanges.can_create(session, user), problem=str(exc))
    return RequesterADOut(enabled=True, can_create=adchanges.can_create(session, user), name=account.display_name, dn=account.directory_dn, ou=adchanges.parent_ou(account.directory_dn), groups=[_g(g) for g in groups])


@router.get("/api/ad/groups", response_model=list[ADGroupOut])
def find_groups(_: Agent, q: str = Query(min_length=2, max_length=100)):
    """Search-as-you-type: groups technicians may change (under Settings > Directory's group OUs)."""
    try:
        found = adchanges.directory().search_groups(q, limit=50)
    except (adchanges.ADChangeError, DirectoryError) as exc:
        raise _err(exc) from None
    return [ADGroupOut(dn=g.dn, name=g.name, changeable=True) for g in found if adchanges.under(g.dn, adchanges.group_bases())][:25]


@router.get("/api/ad/users/{user_id}/groups", response_model=list[ADGroupOut])
def copy_groups_from(user_id: int, _: Agent, session: SessionDep):
    """Another person's direct groups, to copy as a starting point (a new starter like a colleague).
    Only changeable ones can be added; nesting is flagged as anywhere else."""
    other = session.get(User, user_id)
    if other is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such person")
    if not other.directory_dn:
        raise _err(adchanges.ADChangeError(f"{other.display_name} isn't an AD account here, so there are no groups to copy"))
    try:
        d = adchanges.directory()
        out = []
        for g in adchanges.current_groups(other):
            nested, warning = adchanges.nesting(d, g.dn) if g.changeable else (False, None)
            out.append(ADGroupOut(dn=g.dn, name=g.name, changeable=g.changeable, nested=nested, warning=warning))
    except adchanges.ADChangeError as exc:
        raise _err(exc) from None
    return out


@router.get("/api/ad/groups/nesting", response_model=ADGroupOut)
def group_nesting(_: Agent, dn: str = Query(min_length=3, max_length=1000)):
    """Whether a group is nested (Entra ID won't follow that for licensing, app assignment, Conditional Access)."""
    try:
        nested, warning = adchanges.nesting(adchanges.directory(), dn)
    except adchanges.ADChangeError as exc:
        raise _err(exc) from None
    return ADGroupOut(dn=dn, name=dn.split(",", 1)[0].split("=", 1)[-1], changeable=adchanges.under(dn, adchanges.group_bases()), nested=nested, warning=warning)


@router.get("/api/ad/group-lists", response_model=list[ADGroupListOut])
def group_lists(_: Agent):
    """Settings > Directory's group lists (e.g. every [EMAIL] distribution list), to tick several at once."""
    try:
        lists = adchanges.group_lists()
    except adchanges.ADChangeError as exc:
        raise _err(exc) from None
    return [ADGroupListOut(label=label, prefix=prefix, groups=[ADGroupOut(dn=g.dn, name=g.name, changeable=True) for g in groups]) for label, prefix, groups in lists]


@router.get("/api/ad/ous", response_model=list[OUOut])
def find_ous(_: Agent, q: str = ""):
    """OUs accounts may be moved into (under Settings > Directory's allowed OUs)."""
    if not settings.ad_change_ou_bases:
        return []
    try:
        found = adchanges.directory().search_ous(q, settings.ad_change_ou_bases)
    except (adchanges.ADChangeError, DirectoryError) as exc:
        raise _err(exc) from None
    return [OUOut(dn=dn, name=name) for dn, name in found]


def _plan(session, ticket, body: ADChangeIn):
    template = None
    if body.template_id is not None:
        template = session.get(OnboardingTemplate, body.template_id)
        if template is None or not template.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    try:
        p = adchanges.plan(session, ticket, add=body.add, remove=body.remove, move_to_ou=body.move_to_ou, department=body.department, title=body.title, template=template)
    except (adchanges.ADChangeError, DirectoryError) as exc:
        raise _err(exc) from None
    return p, template


@router.post("/api/tickets/{ticket_id}/ad-changes/preview", response_model=ADPlanOut)
def preview_change(ticket_id: int, body: ADChangeIn, user: Agent, session: SessionDep):
    """What would change, nothing done: groups to add (nested ones flagged), already in, to remove, the OU move."""
    p, _ = _plan(session, staff_ticket(session, ticket_id, user), body)
    return ADPlanOut(add=[_g(g) for g in p.add], remove=[_g(g) for g in p.remove], already=[_g(g) for g in p.already], move_to_ou=p.move_to_ou, current_ou=p.current_ou, department=p.department, title=p.title)


@router.post("/api/tickets/{ticket_id}/ad-changes", response_model=list[RunOut], status_code=status.HTTP_201_CREATED)
def apply_change(ticket_id: int, body: ADChangeIn, user: Agent, session: SessionDep):
    """Queue the change on a PowerShell runner (noted on the ticket; its run shows the result)."""
    ticket = staff_ticket(session, ticket_id, user)
    p, template = _plan(session, ticket, body)
    try:
        made = adchanges.request(session, ticket, user, p, template.name if template else None)
    except adchanges.ADChangeError as exc:
        session.rollback()
        raise _err(exc) from None
    session.commit()
    log.info("%s queued an AD change for %s (ticket #%s)", user.display_name, ticket.requester.display_name, ticket.id, extra={"source": "directory"})
    return [_run_out(r, user) for r in made]


# New accounts (onboarding)


def _plan_new(session, body: NewADAccountIn, user):
    if (why := adchanges.not_allowed(session, user)) is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, why)
    template = None
    if body.template_id is not None:
        template = session.get(OnboardingTemplate, body.template_id)
        if template is None or not template.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    fields = body.model_dump(exclude={"template_id"})
    try:
        return adchanges.plan_new(session, **fields, template=template), template
    except adchanges.ADChangeError as exc:
        raise _err(exc) from None


@router.post("/api/tickets/{ticket_id}/ad-account/new/preview", response_model=NewADAccountPlanOut)
def preview_new_account(ticket_id: int, body: NewADAccountIn, user: Agent, session: SessionDep):
    """The account as it would be made: the name from the format (or as given), checked against AD; nothing done."""
    staff_ticket(session, ticket_id, user)
    a, _ = _plan_new(session, body, user)
    return NewADAccountPlanOut(
        **{k: getattr(a, k) for k in NewADAccountPlanOut.model_fields if k not in ("manager", "groups")},
        manager=a.manager.display_name if a.manager else None, groups=[_g(g) for g in a.groups],
    )


@router.post("/api/tickets/{ticket_id}/ad-account/new", response_model=NewADAccountOut, status_code=status.HTTP_201_CREATED)
def create_new_account(ticket_id: int, body: NewADAccountIn, user: Agent, session: SessionDep):
    """Queue the new account on a PowerShell runner (noted on the ticket). The first password is in
    this response only: Layer 8 keeps no copy."""
    ticket = staff_ticket(session, ticket_id, user)
    a, template = _plan_new(session, body, user)
    try:
        made, password = adchanges.create(session, ticket, user, a, template.name if template else None)
    except adchanges.ADChangeError as exc:
        session.rollback()
        raise _err(exc) from None
    session.commit()
    log.info("%s queued a new AD account %s (ticket #%s)", user.display_name, a.sam, ticket.id, extra={"source": "directory"})
    return NewADAccountOut(runs=[_run_out(r, user) for r in made], sam=a.sam, upn=a.upn, password=password)


# Onboarding templates (Settings > Onboarding templates)


@router.get("/api/onboarding-templates", response_model=list[OnboardingTemplateOut])
def list_templates(user: Agent, session: SessionDep):
    stmt = select(OnboardingTemplate).order_by(func.lower(OnboardingTemplate.name))
    if user.role != "admin":
        stmt = stmt.where(OnboardingTemplate.is_active)
    return session.scalars(stmt).all()


def _apply_template(session, t: OnboardingTemplate, body: OnboardingTemplateIn) -> None:
    for g in body.groups:
        if adchanges.group_bases() and not adchanges.under(g.dn, adchanges.group_bases()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{g.dn} isn't under the OUs technicians may change (Settings > Directory)")
    if body.ou_dn and settings.ad_change_ou_bases and not adchanges.under(body.ou_dn, settings.ad_change_ou_bases):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{body.ou_dn} isn't one of the OUs accounts may be moved into (Settings > Directory)")
    other = session.scalar(select(OnboardingTemplate).where(func.lower(OnboardingTemplate.name) == body.name.strip().lower()))
    if other is not None and other.id != t.id:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a template called {other.name!r}")
    t.name, t.description, t.ou_dn = body.name.strip(), body.description, body.ou_dn.strip()
    t.department, t.title, t.is_active = body.department.strip(), body.title.strip(), body.is_active
    t.groups = [{"dn": g.dn.strip(), "name": g.name.strip() or g.dn.split(",", 1)[0].split("=", 1)[-1]} for g in body.groups]


@router.post("/api/onboarding-templates", response_model=OnboardingTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(body: OnboardingTemplateIn, admin: TemplatesManager, session: SessionDep):
    t = OnboardingTemplate()
    _apply_template(session, t, body)
    session.add(t)
    session.commit()
    log.info("Onboarding template %r added by %s", t.name, admin.display_name, extra={"source": "settings"})
    return t


@router.put("/api/onboarding-templates/{template_id}", response_model=OnboardingTemplateOut)
def update_template(template_id: int, body: OnboardingTemplateIn, admin: TemplatesManager, session: SessionDep):
    t = session.get(OnboardingTemplate, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    _apply_template(session, t, body)
    session.commit()
    return t


@router.delete("/api/onboarding-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: int, admin: TemplatesManager, session: SessionDep):
    t = session.get(OnboardingTemplate, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    session.delete(t)
    session.commit()
