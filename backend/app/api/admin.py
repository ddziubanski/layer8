# SPDX-License-Identifier: AGPL-3.0-or-later
"""Setup data: users, groups, queues, SLA policies and routing rules. Agents can read; admins can change.

Queue managers can also edit their own queues and set their owners.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app import auth, permissions, services
from app.auth import Admin, Agent, SessionDep
from app.models import AuthSource, Group, Priority, PriorityMatrixCell, Queue, RoutingRule, SlaPolicy, User, UserRole
from app.priority import matrix as priority_matrix
from app.schemas import (
    GroupCreate,
    GroupOut,
    GroupUpdate,
    MembersIn,
    PasswordSetIn,
    PriorityCell,
    QueuePeopleIn,
    SignInModeIn,
    QueueCreate,
    QueueOut,
    QueueUpdate,
    RoutingRuleIn,
    RoutingRuleOut,
    SlaPolicyIn,
    SlaPolicyOut,
    UserCreate,
    UserOut,
    UserRef,
    UserUpdate,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def get_or_404(session, model, id_):
    obj = session.get(model, id_)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return obj


def check_queue(session, queue_id: int | None) -> None:
    if queue_id is not None:
        get_or_404(session, Queue, queue_id)


def clear_other_defaults(session, keep: Queue) -> None:
    # Only one default queue; setting a new one replaces the old.
    for q in session.scalars(select(Queue).where(Queue.is_default, Queue.id != keep.id)):
        q.is_default = False
    session.flush()


# Users


@router.get("/users", response_model=list[UserOut], tags=["users"])
def list_users(_: Agent, session: SessionDep, q: str | None = None, limit: int = Query(default=0, ge=0, le=200)):
    """Everyone, or with q: active people whose name or email contains it (the requester picker)."""
    stmt = select(User).order_by(User.display_name)
    if q:
        # contains(autoescape) treats % and _ in what was typed as plain characters.
        text = q.strip().lower()
        stmt = stmt.where(
            User.is_active,
            User.email != services.AUTOMATION_EMAIL,
            func.lower(User.display_name).contains(text, autoescape=True) | func.lower(User.email).contains(text, autoescape=True),
        )
    if limit:
        stmt = stmt.limit(limit)
    return session.scalars(stmt).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED, tags=["users"])
def create_user(body: UserCreate, _: Admin, session: SessionDep):
    user = User(**body.model_dump(exclude={"password"}))
    session.add(user)
    session.flush()
    if body.password is not None:
        _set_password(session, user, body.password)
    session.commit()
    return user


def _set_password(session, user: User, password: str) -> None:
    try:
        auth.set_password(session, user, password)
    except auth.AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.put("/users/{user_id}/sign-in", response_model=UserOut, tags=["users"])
def set_sign_in(user_id: int, body: SignInModeIn, _: Admin, session: SessionDep):
    """AD only, AD with a local fallback for when AD is down, or a local account. Signs the user out."""
    user = get_or_404(session, User, user_id)
    try:
        auth.set_sign_in(session, user, body.mode, body.password)
    except auth.AuthError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    session.commit()
    return user


@router.put("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
def set_user_password(user_id: int, body: PasswordSetIn, _: Admin, session: SessionDep):
    """Set or reset a local password. Signs the user out everywhere."""
    _set_password(session, get_or_404(session, User, user_id), body.password)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/users/{user_id}/two-factor", status_code=status.HTTP_204_NO_CONTENT, tags=["users"])
def reset_two_factor(user_id: int, admin: Admin, session: SessionDep):
    """Turn off someone's two-factor sign-in (a lost phone). They set it up again; if Settings >
    Sign-in requires it, at their next sign-in. Signs them out everywhere."""
    from app.api.auth import clear_two_factor

    user = get_or_404(session, User, user_id)
    clear_two_factor(user)
    auth.end_sessions(session, user)
    log.warning("Two-factor sign-in for %s reset by %s", user.email, admin.display_name, extra={"source": "auth"})
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/users/{user_id}", response_model=UserOut, tags=["users"])
def update_user(user_id: int, body: UserUpdate, _: Admin, session: SessionDep):
    user = get_or_404(session, User, user_id)
    changes = body.model_dump(exclude_unset=True)
    if user.auth_source == AuthSource.directory and {"role", "is_active"} & changes.keys():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This account comes from the directory: change its role with role mappings (AD groups), and disable it in AD",
        )
    if "role" in changes:
        user.role_source = "manual"
    for field, value in changes.items():
        setattr(user, field, value)
    session.flush()
    if not session.scalar(select(func.count()).select_from(User).where(User.role == UserRole.admin, User.is_active)):
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Keep at least one active admin: make someone else an admin first")
    if changes.get("is_active") is False:
        auth.end_sessions(session, user)
    session.commit()
    return user


def load_users(session, ids: list[int], *, agents_only: bool = False) -> list[User]:
    users = session.scalars(select(User).where(User.id.in_(ids))).all() if ids else []
    if len(users) != len(set(ids)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown user id")
    if agents_only:
        bad = [u.email for u in users if u.role not in (UserRole.agent, UserRole.admin)]
        if bad:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not agents: {', '.join(bad)}")
    return list(users)


def load_groups(session, ids: list[int]) -> list[Group]:
    groups = session.scalars(select(Group).where(Group.id.in_(ids))).all() if ids else []
    if len(groups) != len(set(ids)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown group id")
    return list(groups)


def load_queues(session, ids: list[int]) -> list[Queue]:
    queues = session.scalars(select(Queue).where(Queue.id.in_(ids))).all() if ids else []
    if len(queues) != len(set(ids)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown queue id")
    return list(queues)


# Groups


@router.get("/groups", response_model=list[GroupOut], tags=["groups"])
def list_groups(_: Agent, session: SessionDep):
    return session.scalars(select(Group).order_by(Group.name)).all()


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED, tags=["groups"])
def create_group(body: GroupCreate, _: Admin, session: SessionDep):
    group = Group(**body.model_dump())
    session.add(group)
    session.commit()
    return group


@router.patch("/groups/{group_id}", response_model=GroupOut, tags=["groups"])
def update_group(group_id: int, body: GroupUpdate, _: Admin, session: SessionDep):
    group = get_or_404(session, Group, group_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    session.commit()
    return group


@router.put("/groups/{group_id}/members", response_model=GroupOut, tags=["groups"])
def set_group_members(group_id: int, body: MembersIn, _: Admin, session: SessionDep):
    group = get_or_404(session, Group, group_id)
    if group.source != AuthSource.local:
        raise HTTPException(status.HTTP_409_CONFLICT, "Members of directory groups come from directory sync")
    group.members = load_users(session, body.user_ids)
    session.commit()
    return group


# Queues


def require_queue_manager(session, user: User, queue: Queue) -> None:
    if user not in queue.managers and not permissions.allowed(session, user, "queues_manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins and this queue's managers only (Settings > Permissions: Queues)")


QueueManager = permissions.require("queues_manage")


@router.get("/queues", response_model=list[QueueOut], tags=["queues"])
def list_queues(_: Agent, session: SessionDep):
    return session.scalars(select(Queue).order_by(Queue.name)).all()


def tidy_addresses(session, addresses: list[str] | None, own_id: int | None = None) -> list[str] | None:
    """Lower-case, no duplicates, and each address claimed by one queue only."""
    if addresses is None:
        return None
    tidy = list(dict.fromkeys(a.strip().lower() for a in addresses if a.strip()))
    for address in tidy:
        other = session.scalar(select(Queue).where(Queue.email_addresses.any(address), Queue.id != (own_id or 0)))
        if other is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{address} already files into {other.name}")
    return tidy


def check_routes(session, routes: dict[str, int] | None, own_id: int | None = None) -> None:
    for option, queue_id in (routes or {}).items():
        target = session.get(Queue, queue_id)
        if target is None or not target.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"\"{option}\" sends to a queue that doesn't exist or is inactive")
        if queue_id == own_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"\"{option}\" already stays in this queue; leave its target blank")


@router.post("/queues", response_model=QueueOut, status_code=status.HTTP_201_CREATED, tags=["queues"])
def create_queue(body: QueueCreate, _: QueueManager, session: SessionDep):
    check_queue(session, body.escalation_queue_id)
    check_routes(session, body.category_routes)
    body.email_addresses = tidy_addresses(session, body.email_addresses)
    queue = Queue(**body.model_dump(exclude={"is_default"}))
    session.add(queue)
    session.flush()
    if body.is_default:
        clear_other_defaults(session, queue)
        queue.is_default = True
    session.commit()
    return queue


@router.patch("/queues/{queue_id}", response_model=QueueOut, tags=["queues"])
def update_queue(queue_id: int, body: QueueUpdate, user: Agent, session: SessionDep):
    queue = get_or_404(session, Queue, queue_id)
    require_queue_manager(session, user, queue)
    changes = body.model_dump(exclude_unset=True)
    if {"is_default", "is_active", "restricted"} & changes.keys() and (reason := permissions.not_allowed(session, user, "queues_manage")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Changing is_default, is_active or restricted: {reason}")
    if changes.get("escalation_queue_id") == queue.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A queue cannot escalate to itself")
    check_queue(session, changes.get("escalation_queue_id"))
    if "category_routes" in changes and "categories" not in changes:
        # Routes only make sense against the option list; re-check them against the current one.
        changes["category_routes"] = {k: v for k, v in (changes["category_routes"] or {}).items() if k in queue.categories}
    if changes.get("categories") is not None and "category_routes" not in changes:
        # Options renamed or removed: drop routes for options that no longer exist.
        changes["category_routes"] = {k: v for k, v in queue.category_routes.items() if k in changes["categories"]}
    check_routes(session, changes.get("category_routes"), queue.id)
    if "email_addresses" in changes:
        changes["email_addresses"] = tidy_addresses(session, changes["email_addresses"], queue.id) or []
    if changes.pop("is_default", None):
        clear_other_defaults(session, queue)
        queue.is_default = True
    for field, value in changes.items():
        setattr(queue, field, value)
    session.commit()
    return queue


@router.put("/queues/{queue_id}/owners", response_model=QueueOut, tags=["queues"])
def set_queue_owners(queue_id: int, body: QueuePeopleIn, user: Agent, session: SessionDep):
    """Owners can be assigned the queue's tickets. Existing assignments aren't changed."""
    queue = get_or_404(session, Queue, queue_id)
    require_queue_manager(session, user, queue)
    queue.owner_users = load_users(session, body.user_ids, agents_only=True)
    queue.owner_groups = load_groups(session, body.group_ids)
    session.commit()
    return queue


@router.put("/queues/{queue_id}/viewers", response_model=QueueOut, tags=["queues"])
def set_queue_viewers(queue_id: int, body: QueuePeopleIn, user: Agent, session: SessionDep):
    """Who else may see a restricted queue's tickets. Owners and managers always can."""
    queue = get_or_404(session, Queue, queue_id)
    require_queue_manager(session, user, queue)
    queue.viewer_users = load_users(session, body.user_ids, agents_only=True)
    queue.viewer_groups = load_groups(session, body.group_ids)
    session.commit()
    return queue


@router.put("/queues/{queue_id}/managers", response_model=QueueOut, tags=["queues"])
def set_queue_managers(queue_id: int, body: QueuePeopleIn, _: QueueManager, session: SessionDep):
    queue = get_or_404(session, Queue, queue_id)
    queue.manager_users = load_users(session, body.user_ids, agents_only=True)
    queue.manager_groups = load_groups(session, body.group_ids)
    session.commit()
    return queue


@router.get("/queues/{queue_id}/assignable", response_model=list[UserRef], tags=["queues"])
def assignable_agents(queue_id: int, _: Agent, session: SessionDep):
    """Who this queue's tickets can be assigned to: its owners, or every active agent if it has none."""
    queue = get_or_404(session, Queue, queue_id)
    if queue.has_owners:
        people = queue.owners
    else:
        people = session.scalars(select(User).where(User.is_active, User.role.in_([UserRole.agent, UserRole.admin]))).all()
    people = [u for u in people if u.role in (UserRole.agent, UserRole.admin) and queue.can_view(u)]
    return sorted(people, key=lambda u: (u.display_name.casefold(), u.id))


# Priority matrix


@router.get("/priority-matrix", response_model=list[PriorityCell], tags=["sla"])
def get_priority_matrix(_: Agent, session: SessionDep):
    """Business impact x urgency -> priority, for portal requests and agents' assessments."""
    return [PriorityCell(impact=i, urgency=u, priority=p) for (i, u), p in priority_matrix(session).items()]


@router.put("/priority-matrix", response_model=list[PriorityCell], tags=["sla"])
def put_priority_matrix(body: list[PriorityCell], _: Admin, session: SessionDep):
    """Replace cells given; others keep their value. Applies to new tickets and new assessments."""
    for cell in body:
        row = session.get(PriorityMatrixCell, (cell.impact, cell.urgency))
        if row is None:
            session.add(PriorityMatrixCell(impact=cell.impact, urgency=cell.urgency, priority=cell.priority))
        else:
            row.priority = cell.priority
    session.commit()
    return get_priority_matrix(_, session)


# SLA policies


@router.get("/sla-policies", response_model=list[SlaPolicyOut], tags=["sla"])
def list_sla_policies(_: Agent, session: SessionDep):
    return session.scalars(select(SlaPolicy)).all()


@router.put("/sla-policies/{priority}", response_model=SlaPolicyOut, tags=["sla"])
def put_sla_policy(priority: Priority, body: SlaPolicyIn, _: Admin, session: SessionDep):
    """Create or replace the policy for a priority. Applies to tickets created or re-prioritised afterwards."""
    policy = session.scalar(select(SlaPolicy).where(SlaPolicy.priority == priority))
    if policy is None:
        policy = SlaPolicy(priority=priority)
        session.add(policy)
    for field, value in body.model_dump().items():
        setattr(policy, field, value)
    session.commit()
    return policy


# Routing rules


@router.get("/routing-rules", response_model=list[RoutingRuleOut], tags=["routing"])
def list_rules(_: Agent, session: SessionDep):
    return session.scalars(select(RoutingRule).order_by(RoutingRule.position, RoutingRule.id)).all()


@router.post("/routing-rules", response_model=RoutingRuleOut, status_code=status.HTTP_201_CREATED, tags=["routing"])
def create_rule(body: RoutingRuleIn, _: Admin, session: SessionDep):
    check_queue(session, body.set_queue_id)
    rule = RoutingRule(**body.model_dump())
    session.add(rule)
    session.commit()
    return rule


@router.put("/routing-rules/{rule_id}", response_model=RoutingRuleOut, tags=["routing"])
def replace_rule(rule_id: int, body: RoutingRuleIn, _: Admin, session: SessionDep):
    rule = get_or_404(session, RoutingRule, rule_id)
    check_queue(session, body.set_queue_id)
    for field, value in body.model_dump().items():
        setattr(rule, field, value)
    session.commit()
    return rule


@router.delete("/routing-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["routing"])
def delete_rule(rule_id: int, _: Admin, session: SessionDep):
    # Tickets keep matched_rule_name, so they still show which rule routed them.
    session.delete(get_or_404(session, RoutingRule, rule_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
