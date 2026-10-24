# SPDX-License-Identifier: AGPL-3.0-or-later
import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CIDR, INET, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, aliased, column_property, mapped_column, relationship

# Stable constraint names so Alembic migrations are predictable.
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)


def str_enum(cls: type[enum.Enum]) -> Enum:
    # Stored as plain strings with a CHECK constraint; adding a value later is
    # a simple migration instead of an ALTER TYPE. Autogenerate renders each
    # CHECK twice with a clashing name: run scripts/fix_enum_checks.py on it.
    return Enum(
        cls,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class UserRole(enum.StrEnum):
    requester = "requester"
    agent = "agent"
    admin = "admin"


class AuthSource(enum.StrEnum):
    local = "local"  # password stored here; works with no directory at all
    directory = "directory"  # signs in through AD/Entra (not built yet)


class TicketSource(enum.StrEnum):
    email = "email"
    portal = "portal"
    agent = "agent"
    api = "api"


class TicketStatus(enum.StrEnum):
    new = "new"
    open = "open"
    waiting_on_user = "waiting_on_user"
    resolved = "resolved"
    closed = "closed"


class Priority(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Urgency(enum.StrEnum):
    low = "low"  # it can wait
    medium = "medium"  # needed soon
    high = "high"  # blocked now


class Impact(enum.StrEnum):
    single = "single"  # just me
    many = "many"  # several people or a team
    business = "business"  # immediate business impact


class ScanStatus(enum.StrEnum):
    pending = "pending"
    clean = "clean"
    infected = "infected"
    skipped = "skipped"


class EventType(enum.StrEnum):
    created = "created"
    routed = "routed"
    assigned = "assigned"
    escalated = "escalated"
    status_changed = "status_changed"
    priority_changed = "priority_changed"
    updated = "updated"
    comment_added = "comment_added"
    attachment_added = "attachment_added"
    requester_changed = "requester_changed"
    device_linked = "device_linked"
    device_unlinked = "device_unlinked"
    script_run = "script_run"
    remote_session = "remote_session"
    merged = "merged"  # on both tickets: data says which way
    system_linked = "system_linked"
    system_unlinked = "system_unlinked"
    parent_changed = "parent_changed"  # on a child: now part of another ticket (or none)
    child_changed = "child_changed"  # on a parent: a child added or removed
    kb_linked = "kb_linked"  # a knowledge base article used on it
    survey_sent = "survey_sent"  # the requester was asked how it went
    survey_answered = "survey_answered"


class User(Base):
    __tablename__ = "users"

    # Not stored. True for a request made with a portal-only (single sign-on) session,
    # which gets requester access whatever the role. Set by auth.current_user.
    portal_only = False

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole), default=UserRole.requester)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    auth_source: Mapped[AuthSource] = mapped_column(
        str_enum(AuthSource), default=AuthSource.local, server_default=AuthSource.local.value
    )
    # AD objectGUID (or Entra object ID). Stable across renames and OU moves.
    directory_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    directory_dn: Mapped[str | None] = mapped_column(Text)
    # Technicians' own look of the queue: their colour (a stripe on tickets assigned to them,
    # seen by everyone) and personal colour rules (only they see them).
    ticket_color: Mapped[str | None] = mapped_column(String(7))
    color_rules: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Their own screen layouts (app.layouts), page -> layout; missing: the admins' default.
    layouts: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # Microsoft Entra object ID (looked up by UPN or email for Teams presence) and the last Teams
    # status read: available / busy / away / offline; None when unknown.
    entra_id: Mapped[str | None] = mapped_column(String(36))
    presence: Mapped[str | None] = mapped_column(String(16))
    presence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upn: Mapped[str | None] = mapped_column(String(320))
    sam_account_name: Mapped[str | None] = mapped_column(String(256))
    department: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(64))
    # "manual" for local accounts; for directory users, which mapping set the role,
    # e.g. "group:SG-Helpdesk-Agents", or "default" (no mapped group: requester).
    role_source: Mapped[str] = mapped_column(String(300), default="manual", server_default="manual")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Argon2 hash. Null means no local password set: the user can't sign in
    # locally (e.g. a requester created by email intake).
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # Two-factor sign-in (app.totp): the encrypted secret once confirmed (pending until then), the
    # last time step used (a code works once), and hashes of the unused recovery codes.
    totp_secret: Mapped[str | None] = mapped_column(Text)
    totp_pending: Mapped[str | None] = mapped_column(Text)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    totp_recovery: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))

    @property
    def two_factor(self) -> bool:
        return self.totp_enabled_at is not None
    # Directory agents/admins only: a local password that works when the directory can't be
    # reached (never instead of a wrong directory password). Stored in password_hash.
    local_fallback: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Signed in with a password someone else knows (the first-run admin/admin): change it first.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        Index("ix_users_upn_lower", text("lower(upn)")),
        Index("ix_users_sam_lower", text("lower(sam_account_name)")),
    )


class UserEmail(Base):
    """Extra addresses a user receives mail on (AD proxyAddresses). Email intake matches these too."""

    __tablename__ = "user_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))

    __table_args__ = (Index("uq_user_emails_email_lower", text("lower(email)"), unique=True),)


class UserSession(Base):
    """A signed-in browser. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Signed in by single sign-on: the portal only, as a requester. The console needs a password.
    portal_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    user: Mapped[User] = relationship()


def _link(name: str, left: str, right: str) -> Table:
    return Table(
        name,
        Base.metadata,
        Column(f"{left}_id", ForeignKey(f"{left}s.id", ondelete="CASCADE"), primary_key=True),
        Column(f"{right}_id", ForeignKey(f"{right}s.id", ondelete="CASCADE"), primary_key=True, index=True),
    )


group_members = _link("group_members", "group", "user")
# Owners: agents who can be assigned the queue's tickets (KACE-style).
# Managers: accountable for the queue; get escalation/breach notices and can edit it.
# Either can be individual users, groups, or both.
queue_owners = _link("queue_owners", "queue", "user")
queue_owner_groups = _link("queue_owner_groups", "queue", "group")
queue_managers = _link("queue_managers", "queue", "user")
queue_manager_groups = _link("queue_manager_groups", "queue", "group")
# Viewers: who else may see a restricted queue's tickets (owners and managers always can).
queue_viewers = _link("queue_viewers", "queue", "user")
queue_viewer_groups = _link("queue_viewer_groups", "queue", "group")


class Group(Base):
    """A set of users. Local groups are managed here; directory groups mirror an AD
    security group or Entra group, and their members come from directory sync."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[AuthSource] = mapped_column(str_enum(AuthSource), default=AuthSource.local)
    # AD objectGUID or Entra group object ID.
    directory_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Last known DN; refreshed by sync, since groups get renamed and moved.
    directory_dn: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set by sync when the group can't be found in the directory any more.
    sync_error: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    members: Mapped[list[User]] = relationship(secondary=group_members, order_by=User.display_name)


def _effective(users: list[User], groups: list[Group]) -> list[User]:
    found = {u.id: u for u in users if u.is_active}
    for group in groups:
        if group.is_active:
            found.update((u.id, u) for u in group.members if u.is_active)
    return sorted(found.values(), key=lambda u: (u.display_name.casefold(), u.id))


class Queue(Base):
    __tablename__ = "queues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # Tickets land here when no routing rule picks a queue.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Where "escalate" moves a ticket. Null means escalation only raises priority.
    escalation_queue_id: Mapped[int | None] = mapped_column(ForeignKey("queues.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Restricted: only owners, managers, viewers and admins see its tickets (e.g. Human Resources).
    restricted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Opening hours (app.hours lines); empty: the desk hours (Settings > Desk hours).
    hours: Mapped[str | None] = mapped_column(Text)

    # Self-service portal: whether requesters can pick this queue, and how it's shown to them.
    show_in_portal: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    portal_name: Mapped[str | None] = mapped_column(String(100))
    portal_description: Mapped[str | None] = mapped_column(Text)
    portal_position: Mapped[int] = mapped_column(Integer, default=100, server_default=text("100"))
    # Optional list offered on the request form. Empty means free text.
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=text("'{}'"))
    # Aliases of the intake mailbox that file new email here (e.g. hr@corp.com). Lower-case.
    email_addresses: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default=text("'{}'"))
    # Problem option -> queue id: a portal request with that option goes to that queue
    # (e.g. Information Technology / "Wi-Fi" -> Network). Options not listed stay in this queue.
    category_routes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))

    escalation_queue: Mapped["Queue | None"] = relationship(remote_side=[id])
    owner_users: Mapped[list[User]] = relationship(secondary=queue_owners, order_by=User.display_name)
    owner_groups: Mapped[list[Group]] = relationship(secondary=queue_owner_groups, order_by=Group.name)
    manager_users: Mapped[list[User]] = relationship(secondary=queue_managers, order_by=User.display_name)
    manager_groups: Mapped[list[Group]] = relationship(secondary=queue_manager_groups, order_by=Group.name)
    viewer_users: Mapped[list[User]] = relationship(secondary=queue_viewers, order_by=User.display_name)
    viewer_groups: Mapped[list[Group]] = relationship(secondary=queue_viewer_groups, order_by=Group.name)

    @property
    def has_owners(self) -> bool:
        return bool(self.owner_users or self.owner_groups)

    @property
    def owners(self) -> list[User]:
        """Active users who own the queue directly or through an active group."""
        return _effective(self.owner_users, self.owner_groups)

    @property
    def managers(self) -> list[User]:
        return _effective(self.manager_users, self.manager_groups)

    @property
    def viewers(self) -> list[User]:
        return _effective(self.viewer_users, self.viewer_groups)

    def can_view(self, user: User) -> bool:
        """Whether this agent may see the queue's tickets as staff."""
        if user.role == UserRole.admin or not self.restricted:
            return True
        return user in self.owners or user in self.managers or user in self.viewers

    def can_be_assigned(self, user: User) -> bool:
        if self.restricted and not self.can_view(user):
            return False
        # A queue with no owners configured accepts any agent, so a fresh install works.
        return not self.has_owners or user in self.owners

    __table_args__ = (
        Index("uq_queues_single_default", "is_default", unique=True, postgresql_where=text("is_default")),
    )


class SlaPolicy(Base):
    __tablename__ = "sla_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    priority: Mapped[Priority] = mapped_column(str_enum(Priority), unique=True)
    first_response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_minutes: Mapped[int] = mapped_column(Integer)
    # A ticket is "at risk" once this share of its resolution window has passed.
    at_risk_percent: Mapped[int] = mapped_column(Integer, default=75, server_default=text("75"))


class RoutingRule(Base):
    """Rules are checked in position order; the first rule whose conditions all match wins.

    A null condition matches anything. Keywords match if any one appears in the
    subject or description (case-insensitive). requester_match is an exact
    email, or a domain when it starts with "@".
    """

    __tablename__ = "routing_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    match_category: Mapped[str | None] = mapped_column(String(100))
    match_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    match_requester: Mapped[str | None] = mapped_column(String(320))
    match_source: Mapped[TicketSource | None] = mapped_column(str_enum(TicketSource))
    match_priority: Mapped[Priority | None] = mapped_column(str_enum(Priority))
    # The queue the requester chose in the portal.
    match_requested_queue_id: Mapped[int | None] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"))
    # A requester's chosen queue is only replaced by rules that match on it or set this.
    override_requested_queue: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    set_queue_id: Mapped[int | None] = mapped_column(ForeignKey("queues.id"))
    set_priority: Mapped[Priority | None] = mapped_column(str_enum(Priority))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    set_queue: Mapped[Queue | None] = relationship(foreign_keys=[set_queue_id])

    def may_replace_requested_queue(self) -> bool:
        return self.match_requested_queue_id is not None or self.override_requested_queue


class FieldList(enum.StrEnum):
    """Dropdown lists admins edit on Settings > Ticket fields."""

    resolution_type = "resolution_type"
    closure_reason = "closure_reason"
    department = "department"
    merge_reason = "merge_reason"


class FieldOption(Base):
    """One choice in a ticket dropdown. Retired (inactive) choices stay on old tickets and in
    reports but aren't offered any more."""

    __tablename__ = "field_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    list: Mapped[FieldList] = mapped_column(str_enum(FieldList), index=True)
    label: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Built-in choices the app uses itself, e.g. "requester_resolved" for a portal self-close.
    key: Mapped[str | None] = mapped_column(String(50), unique=True)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(BigInteger, Identity(start=1000), primary_key=True)
    subject: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[TicketSource] = mapped_column(str_enum(TicketSource))
    status: Mapped[TicketStatus] = mapped_column(str_enum(TicketStatus), default=TicketStatus.new)
    priority: Mapped[Priority] = mapped_column(str_enum(Priority), default=Priority.medium)
    # What the requester said; the priority matrix turns these into priority.
    urgency: Mapped[Urgency | None] = mapped_column(str_enum(Urgency))
    impact: Mapped[Impact | None] = mapped_column(str_enum(Impact))

    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    queue_id: Mapped[int] = mapped_column(ForeignKey("queues.id"))
    # What the requester picked in the portal, kept even if routing moved the ticket.
    requested_queue_id: Mapped[int | None] = mapped_column(ForeignKey("queues.id", ondelete="SET NULL"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Snapshot of the rule name too, so the ticket still explains itself if the
    # rule is later renamed or deleted.
    matched_rule_id: Mapped[int | None] = mapped_column(ForeignKey("routing_rules.id", ondelete="SET NULL"))
    matched_rule_name: Mapped[str | None] = mapped_column(String(200))

    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the ticket turns "at risk"; stored so saved views are a plain query.
    sla_at_risk_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The SLA clock stopped while waiting on the user (app.sla): since when, the business minutes
    # each deadline had left then, and the business minutes of earlier pauses.
    sla_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_left: Mapped[dict | None] = mapped_column(JSONB)
    sla_paused_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # The policy's resolution window (business minutes), for how much of it is left.
    sla_resolution_minutes: Mapped[int | None] = mapped_column(Integer)
    # Nobody assigned since then (None while someone has it), and when that set off an alert.
    unassigned_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    unaccepted_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # For "time in status" rule conditions.
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Asked when resolving or closing (Settings > Ticket fields).
    resolution_type_id: Mapped[int | None] = mapped_column(ForeignKey("field_options.id", ondelete="SET NULL"))
    closure_reason_id: Mapped[int | None] = mapped_column(ForeignKey("field_options.id", ondelete="SET NULL"))
    # Contact details for this ticket; start from the requester's AD record.
    department_id: Mapped[int | None] = mapped_column(ForeignKey("field_options.id", ondelete="SET NULL"))
    # The requester's computer: quick actions (remote session, scripts) act on it.
    computer_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    # Merged into that ticket (its replies and files moved there; this one is closed).
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    # A child ticket is part of this one (a task for another technician, say).
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), index=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64))
    # Fields an import brought from another system that Layer 8 has no field for
    # ({"Building": "A", ...}): shown on that ticket only, never added to forms or lists.
    imported_fields: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])
    queue: Mapped[Queue] = relationship(foreign_keys=[queue_id])

    @property
    def sla_minutes_left(self) -> int | None:
        from app import sla

        left = sla.minutes_left(self, sla.utcnow())
        return None if left is None else round(left)
    resolution_type: Mapped[FieldOption | None] = relationship(foreign_keys=[resolution_type_id])
    closure_reason: Mapped[FieldOption | None] = relationship(foreign_keys=[closure_reason_id])
    department: Mapped[FieldOption | None] = relationship(foreign_keys=[department_id])
    computer: Mapped["Device | None"] = relationship(foreign_keys=[computer_id])
    parent: Mapped["Ticket | None"] = relationship(remote_side="Ticket.id", foreign_keys=[parent_id])
    merged_into: Mapped["Ticket | None"] = relationship(remote_side="Ticket.id", foreign_keys=[merged_into_id])

    __table_args__ = (
        Index("ix_tickets_queue_status", "queue_id", "status"),
        Index("ix_tickets_assignee_status", "assignee_id", "status"),
        Index("ix_tickets_requester", "requester_id"),
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    # Internal notes are never shown to the requester or emailed out.
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    # A staff reply that was also emailed to the requester (not just shown in the portal).
    emailed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    source: Mapped[TicketSource] = mapped_column(str_enum(TicketSource))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    author: Mapped[User] = relationship()


class Attachment(Base):
    """Metadata only. File bytes live in storage_dir under storage_key, never in the database."""

    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    comment_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id", ondelete="SET NULL"))
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    scan_status: Mapped[ScanStatus] = mapped_column(str_enum(ScanStatus), default=ScanStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    uploaded_by: Mapped[User | None] = relationship()


class DeviceSource(enum.StrEnum):
    directory = "directory"  # an AD computer object, kept up to date by directory sync
    manual = "manual"  # added by hand (standalone installs, non-domain kit)


class Device(Base):
    """A computer or other device tickets and scripts can refer to. AD computers arrive with directory
    sync; others are added by hand. Action1 or an agent can fill in more later."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    dns_hostname: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[DeviceSource] = mapped_column(str_enum(DeviceSource), default=DeviceSource.manual)
    # AD objectGUID for directory devices.
    directory_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    directory_dn: Mapped[str | None] = mapped_column(Text)
    os: Mapped[str | None] = mapped_column(String(255))
    os_version: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    # Enabled in AD (manual devices: always).
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Still in the directory's scope (false once it's gone from AD); manual devices until removed.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # AD lastLogonTimestamp: replicated, so up to ~14 days behind the real last logon.
    last_logon_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who it's for: AD managedBy, or set by hand.
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Lifecycle (warranty and replacement): kept by technicians; Action1 fills manufacturer and serial when empty.
    manufacturer: Mapped[str] = mapped_column(String(200), default="", server_default="")
    model: Mapped[str] = mapped_column(String(200), default="", server_default="")
    serial: Mapped[str] = mapped_column(String(200), default="", server_default="")
    purchased_on: Mapped[date | None] = mapped_column(Date)
    warranty_ends: Mapped[date | None] = mapped_column(Date)
    # When it's due for replacement; empty: purchased_on plus Settings > Device sweeps' lifetime.
    replace_by: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    assigned_user: Mapped[User | None] = relationship()


class TicketDevice(Base):
    """A device linked to a ticket (the ticket's history notes who linked it and when)."""

    __tablename__ = "ticket_devices"

    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True, index=True)
    linked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped[Device] = relationship()


class ScriptKind(enum.StrEnum):
    query = "query"  # read-only SQL: a report or a dynamic group of devices/users
    action = "action"  # acts on devices, through a runner


class ScriptLanguage(enum.StrEnum):
    sql = "sql"
    powershell = "powershell"
    bash = "bash"
    python = "python"
    eos = "eos"  # Arista EOS CLI commands, sent through eAPI


class Script(Base):
    """An item in the script library. Its body and parameters live in versions; current_version is the one that runs."""

    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    kind: Mapped[ScriptKind] = mapped_column(str_enum(ScriptKind))
    language: Mapped[ScriptLanguage] = mapped_column(str_enum(ScriptLanguage))
    # High-risk actions need an admin's approval before they run.
    high_risk: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Agents may run it (admins always can). Only admins create and edit scripts.
    agents_can_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    versions: Mapped[list["ScriptVersion"]] = relationship(back_populates="script", order_by="ScriptVersion.number", cascade="all, delete-orphan")


class ScriptVersion(Base):
    """One saved version of a script: never changed once written."""

    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("script_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    # The interpreter is part of the saved executable, not a later mutable setting.
    language: Mapped[ScriptLanguage | None] = mapped_column(str_enum(ScriptLanguage))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # [{name, label, type: text|int|bool|date|choice, required, default, choices}]
    parameters: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    script: Mapped[Script] = relationship(back_populates="versions")
    author: Mapped[User | None] = relationship(foreign_keys=[author_id])
    reviewed_by: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_id])


class Runner(Base):
    """A machine that runs action scripts (e.g. a domain-joined Windows box for PowerShell over WinRM).
    It polls the API with its own token, so it needs no inbound ports."""

    __tablename__ = "runners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Which script languages it takes.
    languages: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Privileged hosts may be reserved for explicitly targeted built-in integrations.
    builtins_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_host: Mapped[str | None] = mapped_column(String(255))
    last_version: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunStatus(enum.StrEnum):
    awaiting_approval = "awaiting_approval"
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    rejected = "rejected"
    cancelled = "cancelled"


class ScriptRun(Base):
    """One action script run on one target (a device, or the relay itself), with its output."""

    __tablename__ = "script_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"), index=True)
    # The exact version that runs (versions are never changed).
    version_id: Mapped[int] = mapped_column(ForeignKey("script_versions.id", ondelete="CASCADE"))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), index=True)
    # The target as it was asked for (the device may change or go later); None: on the runner itself.
    target: Mapped[str | None] = mapped_column(String(255))
    values: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # Values never shown or kept (a new account's first password): encrypted with a key in the
    # secrets folder (app.runseal), given to the runner once when it claims the run, then cleared.
    sealed: Mapped[bytes | None] = mapped_column(LargeBinary)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), index=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus), index=True)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runner_id: Mapped[int | None] = mapped_column(ForeignKey("runners.id", ondelete="SET NULL"))
    # Only this runner may claim it (e.g. a subnet's scan by the runner on that LAN).
    only_runner_id: Mapped[int | None] = mapped_column(ForeignKey("runners.id", ondelete="SET NULL"))
    # Not claimed before this (e.g. enabling a new starter's account on their start date).
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # An email to send if it succeeds: {"to", "subject", "body"} (e.g. telling the manager an account is ready).
    on_success: Mapped[dict | None] = mapped_column(JSONB)
    # Queued by a script schedule rather than a person.
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("script_schedules.id", ondelete="SET NULL"), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    output: Mapped[str] = mapped_column(Text, default="", server_default="")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    script: Mapped[Script] = relationship()
    version: Mapped[ScriptVersion] = relationship()
    device: Mapped[Device | None] = relationship()
    runner: Mapped[Runner | None] = relationship(foreign_keys=[runner_id])
    requested_by: Mapped[User | None] = relationship(foreign_keys=[requested_by_id])
    decided_by: Mapped[User | None] = relationship(foreign_keys=[decided_by_id])


class ScriptSchedule(Base):
    """A script run on a timetable (Scripts > Schedules): an action queued on its devices, a dynamic
    group's devices (worked out at each run) or the relay itself; or a query whose result is emailed
    as CSV. It runs as its owner, with the owner's rights. The timetable is as a report schedule's."""

    __tablename__ = "script_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    values: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # An action's targets: these devices, plus a dynamic group's (a query returning device_id), or the runner itself.
    device_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    group_script_id: Mapped[int | None] = mapped_column(ForeignKey("scripts.id", ondelete="SET NULL"))
    on_runner: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # A query's :start and :end, worked out at each run (reports.delivery.PERIODS); empty: none.
    period: Mapped[str] = mapped_column(String(30), default="", server_default="")
    # A query's result goes to these addresses.
    recipients: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    # once, daily, weekly or monthly; weekdays 0 = Monday; day_of_month 1-28 or -1 (last); server local HH:MM
    frequency: Mapped[str] = mapped_column(String(10))
    weekdays: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    day_of_month: Mapped[int | None] = mapped_column(Integer)
    time_of_day: Mapped[str] = mapped_column(String(5))
    start_date: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What the last run did ("Queued on 12 devices", "Emailed 40 rows to 2"), or why it couldn't.
    last_result: Mapped[str] = mapped_column(Text, default="", server_default="")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    script: Mapped[Script] = relationship(foreign_keys=[script_id])
    group_script: Mapped[Script | None] = relationship(foreign_keys=[group_script_id])
    owner: Mapped[User | None] = relationship()


class RemoteSession(Base):
    """A remote session a technician started from a ticket (Remote Assistance today). The launcher on
    their PC reports when it ends, with a one-time token, so the ticket shows how long it took."""

    __tablename__ = "remote_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    target: Mapped[str] = mapped_column(String(255))
    tool: Mapped[str] = mapped_column(String(30))
    started_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_token_hash: Mapped[str | None] = mapped_column(String(64))

    started_by: Mapped[User | None] = relationship()


class Vendor(Base):
    """A supplier we call for support: their account details, support channels and contacts."""

    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    account_number: Mapped[str] = mapped_column(String(100), default="", server_default="")
    website: Mapped[str] = mapped_column(String(500), default="", server_default="")
    support_phone: Mapped[str] = mapped_column(String(64), default="", server_default="")
    support_email: Mapped[str] = mapped_column(String(320), default="", server_default="")
    support_portal: Mapped[str] = mapped_column(String(500), default="", server_default="")
    support_hours: Mapped[str] = mapped_column(String(200), default="", server_default="")
    contract_level: Mapped[str] = mapped_column(String(200), default="", server_default="")
    contract_ends: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    contacts: Mapped[list["VendorContact"]] = relationship(
        back_populates="vendor", order_by="VendorContact.position, VendorContact.id", cascade="all, delete-orphan"
    )


class VendorContact(Base):
    __tablename__ = "vendor_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # support, sales, account manager, escalation, other
    role: Mapped[str] = mapped_column(String(50), default="support", server_default="support")
    email: Mapped[str] = mapped_column(String(320), default="", server_default="")
    phone: Mapped[str] = mapped_column(String(64), default="", server_default="")
    mobile: Mapped[str] = mapped_column(String(64), default="", server_default="")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    vendor: Mapped[Vendor] = relationship(back_populates="contacts")


system_devices = Table(
    "system_devices",
    Base.metadata,
    Column("system_id", ForeignKey("systems.id", ondelete="CASCADE"), primary_key=True),
    Column("device_id", ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class System(Base):
    """Something a vendor supports (phone system, firewall pair, ERP...): who to call, what it runs on, its addresses."""

    __tablename__ = "systems"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), index=True)
    # The vendor contact to call about it.
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("vendor_contacts.id", ondelete="SET NULL"))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vendor: Mapped[Vendor | None] = relationship()
    contact: Mapped[VendorContact | None] = relationship()
    devices: Mapped[list["Device"]] = relationship(secondary=system_devices, order_by="Device.name")
    addresses: Mapped[list["SystemAddress"]] = relationship(
        back_populates="system", order_by="SystemAddress.position, SystemAddress.id", cascade="all, delete-orphan"
    )


class SystemAddress(Base):
    """An IP address or host name of a system, and what it's for."""

    __tablename__ = "system_addresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    system_id: Mapped[int] = mapped_column(ForeignKey("systems.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(255))
    # management, vip, ilo (out-of-band), service, other
    kind: Mapped[str] = mapped_column(String(30), default="management", server_default="management")
    notes: Mapped[str] = mapped_column(String(500), default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    system: Mapped[System] = relationship(back_populates="addresses")


class TicketSystem(Base):
    __tablename__ = "ticket_systems"

    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    system_id: Mapped[int] = mapped_column(ForeignKey("systems.id", ondelete="CASCADE"), primary_key=True, index=True)
    linked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    system: Mapped[System] = relationship()


class MerakiClient(Base):
    """Replaceable observations, separate from authoritative IPAM records."""

    __tablename__ = "meraki_clients"
    network_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), primary_key=True, index=True)
    network_name: Mapped[str] = mapped_column(String(255))
    client_id: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(45))
    description: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32))
    vlan: Mapped[str] = mapped_column(String(32))
    switch_name: Mapped[str] = mapped_column(String(255))
    switch_serial: Mapped[str] = mapped_column(String(100))
    port: Mapped[str] = mapped_column(String(100))
    connection: Mapped[str] = mapped_column(String(32))
    ssid: Mapped[str] = mapped_column(String(100))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MerakiDiagnostic(Base):
    """Bounded, replaceable diagnostic cache, never authoritative IPAM data."""

    __tablename__ = "meraki_diagnostics"
    network_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), primary_key=True)
    identity_hash: Mapped[str] = mapped_column(String(64))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(255))


class MerakiWebhookReceipt(Base):
    """Replay protection and a bounded administrator delivery history; no raw payloads."""
    __tablename__ = "meraki_webhook_receipts"
    organization_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    network_id: Mapped[str] = mapped_column(String(100))
    serial: Mapped[str] = mapped_column(String(32))
    alert_type: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))


class MerakiWebhookIncident(Base):
    """Latest device reporting state, including recoveries received before the outage."""
    __tablename__ = "meraki_webhook_incidents"
    organization_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    network_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    serial: Mapped[str] = mapped_column(String(32), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_down: Mapped[bool] = mapped_column(Boolean)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))


class DevicePatch(Base):
    """A machine as Action1 (patching, read-only) reports it: its patch state, matched to a device by
    name. Kept current by the "action1" worker job; overdue patching can open a ticket."""

    __tablename__ = "device_patches"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Action1's endpoint id.
    action1_id: Mapped[str] = mapped_column(String(64), unique=True)
    organization_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    name: Mapped[str] = mapped_column(String(255))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), unique=True)
    # Connected / Disconnected, as Action1 says.
    status: Mapped[str] = mapped_column(String(50), default="", server_default="")
    os: Mapped[str] = mapped_column(String(255), default="", server_default="")
    agent_version: Mapped[str] = mapped_column(String(50), default="", server_default="")
    user: Mapped[str] = mapped_column(String(255), default="", server_default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_boot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    missing_critical: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    missing_other: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reboot_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    update_status: Mapped[str] = mapped_column(String(50), default="", server_default="")
    vulnerability_status: Mapped[str] = mapped_column(String(50), default="", server_default="")
    # Open vulnerabilities (CVEs) Action1 found on it.
    vulns_critical: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    vulns_other: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The Action1 organization it's in (its name).
    organization: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # Since when critical updates have been missing / a reboot pending (as first seen by a sync).
    critical_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reboot_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The ticket opened because patching is overdue (another isn't opened while it's open).
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    # False once a sync no longer finds it in Action1.
    in_action1: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped["Device | None"] = relationship()


# IPAM (light): VLANs, subnets and documented IP addresses. Which subnet an address is in comes
# from the address itself (the most specific subnet containing it), so it can't drift.


class SoftwareDeployment(Base):
    """Immutable reviewed target/package and durable intent before an Action1 write."""

    __tablename__ = "software_deployments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), index=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(24), default="review", server_default="review")
    instance_id: Mapped[str | None] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Vlan(Base):
    __tablename__ = "vlans"
    __table_args__ = (UniqueConstraint("site", "number", name="uq_vlans_site_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    site: Mapped[str] = mapped_column(String(100), default="", server_default="")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")


class Subnet(Base):
    __tablename__ = "subnets"

    id: Mapped[int] = mapped_column(primary_key=True)
    network: Mapped[str] = mapped_column(CIDR, unique=True)
    name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    vlan_id: Mapped[int | None] = mapped_column(ForeignKey("vlans.id", ondelete="SET NULL"), index=True)
    gateway: Mapped[str | None] = mapped_column(INET)
    # The DHCP scope's pool, if the subnet has one.
    dhcp_start: Mapped[str | None] = mapped_column(INET)
    dhcp_end: Mapped[str | None] = mapped_column(INET)
    dns_servers: Mapped[str] = mapped_column(String(500), default="", server_default="")
    site: Mapped[str] = mapped_column(String(100), default="", server_default="")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Scheduled scans: every this many hours (0: only on demand), by this runner (None: any that takes Python).
    scan_every_hours: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    scan_runner_id: Mapped[int | None] = mapped_column(ForeignKey("runners.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vlan: Mapped[Vlan | None] = relationship()
    scan_runner: Mapped["Runner | None"] = relationship()


class IpKind(enum.StrEnum):
    static = "static"
    reserved = "reserved"  # a DHCP reservation
    dhcp = "dhcp"  # a dynamic lease
    do_not_use = "do_not_use"  # held back: don't hand it out


class IpSource(enum.StrEnum):
    manual = "manual"
    imported = "imported"
    scan = "scan"


class IpAddress(Base):
    """A documented IP address: what's on it and how it's assigned."""

    __tablename__ = "ip_addresses"
    # For "which addresses are in this subnet" (address <<= network).
    __table_args__ = (Index("ix_ip_addresses_address_gist", "address", postgresql_using="gist", postgresql_ops={"address": "inet_ops"}),)

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(INET, unique=True)
    hostname: Mapped[str] = mapped_column(String(255), default="", server_default="")
    # Lower case, colon separated.
    mac: Mapped[str] = mapped_column(String(17), default="", server_default="")
    kind: Mapped[IpKind] = mapped_column(str_enum(IpKind), default=IpKind.static)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), index=True)
    system_id: Mapped[int | None] = mapped_column(ForeignKey("systems.id", ondelete="SET NULL"), index=True)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    source: Mapped[IpSource] = mapped_column(str_enum(IpSource), default=IpSource.manual)
    # Set by scans.
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    device: Mapped["Device | None"] = relationship()
    system: Mapped[System | None] = relationship()


class ScanKind(enum.StrEnum):
    network = "network"  # ping sweep, reverse DNS and ARP (the built-in "Layer 8: IP scan")
    dhcp_dns = "dhcp_dns"  # Windows DHCP leases and reservations, and DNS A records ("Layer 8: DHCP and DNS read")
    snmp = "snmp"  # network gear over SNMP: identity, LLDP neighbours, MAC tables ("Layer 8: SNMP discovery")


class IpScan(Base):
    """A scan of a subnet, done by a relay with one of the built-in scan scripts."""

    __tablename__ = "ip_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    subnet_id: Mapped[int] = mapped_column(ForeignKey("subnets.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("script_runs.id", ondelete="SET NULL"), unique=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[ScanKind] = mapped_column(str_enum(ScanKind), default=ScanKind.network, server_default="network")
    scheduled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # When its results were read (None: not finished yet, or it failed).
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hosts_seen: Mapped[int | None] = mapped_column(Integer)
    new_findings: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    run: Mapped["ScriptRun | None"] = relationship()
    requested_by: Mapped["User | None"] = relationship()


class FindingKind(enum.StrEnum):
    new_host = "new_host"  # answering, but not documented
    not_seen = "not_seen"  # documented, seen before, not for a while
    hostname_changed = "hostname_changed"
    mac_changed = "mac_changed"
    in_use_do_not_use = "in_use_do_not_use"  # marked "do not use", but something answers
    kind_changed = "kind_changed"  # DHCP says reserved or leased, the documentation says otherwise


class FindingStatus(enum.StrEnum):
    open = "open"
    applied = "applied"
    dismissed = "dismissed"
    cleared = "cleared"  # a later scan found it's no longer so


class IpFinding(Base):
    """A difference a scan found between what's documented and what's on the network. Nothing is
    changed until someone applies it."""

    __tablename__ = "ip_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    subnet_id: Mapped[int] = mapped_column(ForeignKey("subnets.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(INET)
    kind: Mapped[FindingKind] = mapped_column(str_enum(FindingKind))
    # Which kind of scan raised it: only that kind refreshes or clears it.
    scan_kind: Mapped[ScanKind] = mapped_column(str_enum(ScanKind), default=ScanKind.network, server_default="network")
    status: Mapped[FindingStatus] = mapped_column(str_enum(FindingStatus), default=FindingStatus.open, index=True)
    # What was seen and what's documented: hostname, mac, documented_hostname, documented_mac, last_seen_at.
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    ip_address_id: Mapped[int | None] = mapped_column(ForeignKey("ip_addresses.id", ondelete="SET NULL"))
    scan_id: Mapped[int | None] = mapped_column(ForeignKey("ip_scans.id", ondelete="SET NULL"))
    first_found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    resolved_by: Mapped["User | None"] = relationship()


class ImportRef(Base):
    """What an import from another system (TrackIT) made of each of its records, so a later run
    (a cut-over top-up) updates instead of duplicating."""

    __tablename__ = "import_refs"
    __table_args__ = (UniqueConstraint("source", "kind", "source_id", name="uq_import_refs_source_kind_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(30))
    # ticket, comment, user, attachment, article, device
    kind: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str] = mapped_column(String(100))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    """A key another application (e.g. a server-room sign-in app) uses for the integration API
    (/api/integrations). It acts as its own technician account, which is inactive so it can never
    sign in or be assigned work; notes it adds are signed with that account's name."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The first characters, to tell keys apart in lists (the rest is never stored).
    prefix: Mapped[str] = mapped_column(String(12))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    created_by: Mapped["User | None"] = relationship(foreign_keys=[created_by_id])


class DeviceWarranty(Base):
    """What the maker says about a device's warranty (Dell TechDirect, Lenovo's warranty API), as of
    the last lookup. Kept apart from the device's own dates: those are filled when empty, and a
    difference is shown for a technician to take or leave."""

    __tablename__ = "device_warranties"

    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    vendor: Mapped[str] = mapped_column(String(20))
    serial: Mapped[str] = mapped_column(String(200))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Why the last lookup gave nothing (unknown serial, refused...); None when it worked.
    error: Mapped[str | None] = mapped_column(Text)
    product: Mapped[str] = mapped_column(String(255), default="", server_default="")
    shipped_on: Mapped[date | None] = mapped_column(Date)
    purchased_on: Mapped[date | None] = mapped_column(Date)
    # The latest end of any entitlement.
    warranty_ends: Mapped[date | None] = mapped_column(Date)
    # [{name, type, start, end}] as the maker lists them.
    entitlements: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))


class NetworkDevice(Base):
    """Network gear found by SNMP discovery (switches, routers, printers, UPSs...)."""

    __tablename__ = "network_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(INET, unique=True)
    sys_name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    sys_descr: Mapped[str] = mapped_column(Text, default="", server_default="")
    sys_object_id: Mapped[str] = mapped_column(String(255), default="", server_default="")
    location: Mapped[str] = mapped_column(String(255), default="", server_default="")
    contact: Mapped[str] = mapped_column(String(255), default="", server_default="")
    vendor: Mapped[str] = mapped_column(String(100), default="", server_default="")
    model: Mapped[str] = mapped_column(String(255), default="", server_default="")
    serial: Mapped[str] = mapped_column(String(100), default="", server_default="")
    # switch (has a bridge MAC table), printer, or other.
    kind: Mapped[str] = mapped_column(String(20), default="other", server_default="other")
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    # The asset it is, when one matches by serial or name (or a technician links it).
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), index=True)
    subnet_id: Mapped[int | None] = mapped_column(ForeignKey("subnets.id", ondelete="SET NULL"), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped["Device | None"] = relationship()
    neighbors: Mapped[list["NetworkNeighbor"]] = relationship(cascade="all, delete-orphan", order_by="NetworkNeighbor.id")


class NetworkNeighbor(Base):
    """An LLDP neighbour of a network device, as of its last discovery."""

    __tablename__ = "network_neighbors"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_device_id: Mapped[int] = mapped_column(ForeignKey("network_devices.id", ondelete="CASCADE"), index=True)
    local_port: Mapped[str] = mapped_column(String(100), default="", server_default="")
    remote_name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    remote_port: Mapped[str] = mapped_column(String(255), default="", server_default="")
    remote_port_desc: Mapped[str] = mapped_column(String(255), default="", server_default="")
    remote_chassis: Mapped[str] = mapped_column(String(100), default="", server_default="")
    remote_address: Mapped[str | None] = mapped_column(INET)


class SwitchPortMac(Base):
    """A MAC address in a switch's forwarding table, as of its last discovery: where a machine plugs in."""

    __tablename__ = "switch_port_macs"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_device_id: Mapped[int] = mapped_column(ForeignKey("network_devices.id", ondelete="CASCADE"), index=True)
    mac: Mapped[str] = mapped_column(String(17), index=True)
    port: Mapped[str] = mapped_column(String(100), default="", server_default="")
    vlan: Mapped[int | None] = mapped_column(Integer)
    # How many MACs that port had: an edge port has one or a few, an uplink many.
    port_mac_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    network_device: Mapped[NetworkDevice] = relationship()


class POStatus(enum.StrEnum):
    draft = "draft"
    requested = "requested"
    approved = "approved"
    ordered = "ordered"
    partially_received = "partially_received"
    received = "received"
    cancelled = "cancelled"
    closed = "closed"


po_tickets = Table(
    "po_tickets",
    Base.metadata,
    Column("po_id", ForeignKey("purchase_orders.id", ondelete="CASCADE"), primary_key=True),
    Column("ticket_id", ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True, index=True),
)
po_devices = Table(
    "po_devices",
    Base.metadata,
    Column("po_id", ForeignKey("purchase_orders.id", ondelete="CASCADE"), primary_key=True),
    Column("device_id", ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class PurchaseOrder(Base):
    """A purchase order for reference and receiving, not accounting: what was ordered, from whom,
    for which department and tickets, and what has arrived. Its number is its id (labelled like
    tickets, e.g. PO-00012: Settings > General)."""

    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(BigInteger, Identity(start=1), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("field_options.id", ondelete="SET NULL"))
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # The manager who approves it.
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[POStatus] = mapped_column(str_enum(POStatus), default=POStatus.draft, index=True)
    order_date: Mapped[date | None] = mapped_column(Date)
    expected_date: Mapped[date | None] = mapped_column(Date)
    ship_to: Mapped[str] = mapped_column(Text, default="", server_default="")
    budget_code: Mapped[str] = mapped_column(String(100), default="", server_default="")
    # The vendor's quote or order number.
    vendor_reference: Mapped[str] = mapped_column(String(200), default="", server_default="")
    shipping: Mapped[float] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    terms: Mapped[str] = mapped_column(Text, default="", server_default="")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Printed on the receiving sheet: with the PO number it opens the public receiving page.
    receiving_code: Mapped[str | None] = mapped_column(String(16))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    vendor: Mapped["Vendor | None"] = relationship()
    department: Mapped["FieldOption | None"] = relationship()
    requested_by: Mapped[User | None] = relationship(foreign_keys=[requested_by_id])
    approver: Mapped[User | None] = relationship(foreign_keys=[approver_id])
    lines: Mapped[list["POLine"]] = relationship(back_populates="po", order_by="POLine.position, POLine.id", cascade="all, delete-orphan")
    tickets: Mapped[list["Ticket"]] = relationship(secondary=po_tickets, order_by="Ticket.id")
    devices: Mapped[list["Device"]] = relationship(secondary=po_devices, order_by="Device.name")


class POLine(Base):
    __tablename__ = "po_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    item: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    part_number: Mapped[str] = mapped_column(String(100), default="", server_default="")
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0, server_default="0")
    received_quantity: Mapped[float] = mapped_column(Numeric(12, 3), default=0, server_default="0")

    po: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class POAttachment(Base):
    """A quote, invoice, packing slip... kept with the PO (bytes in storage, like ticket files)."""

    __tablename__ = "po_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    uploaded_by: Mapped[User | None] = relationship()


class POEvent(Base):
    """A PO's history: created, edited, status changes, lines received, files added."""

    __tablename__ = "po_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    actor: Mapped[User | None] = relationship()


class TicketExpense(Base):
    """What fixing a ticket cost or needs: a line on the ticket's Expenses tab, maybe on a PO."""

    __tablename__ = "ticket_expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(String(300))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), default=1)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id", ondelete="SET NULL"))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    vendor: Mapped["Vendor | None"] = relationship()
    created_by: Mapped[User | None] = relationship()


class OnboardingTemplate(Base):
    """A starting set for someone's AD account: security groups, an OU and a few attributes.
    Admins keep them in Settings > Onboarding templates; technicians apply one to a ticket's
    requester (a preview first), through the AD change action on a relay."""

    __tablename__ = "onboarding_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # [{"dn": ..., "name": ...}]
    groups: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    ou_dn: Mapped[str] = mapped_column(Text, default="", server_default="")
    department: Mapped[str] = mapped_column(String(200), default="", server_default="")
    title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ChangeKind(enum.StrEnum):
    standard = "standard"  # from a pre-approved template
    normal = "normal"  # approved before it starts, if its risk calls for it
    emergency = "emergency"  # may start at once; approved afterwards


class ChangeStatus(enum.StrEnum):
    draft = "draft"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    rejected = "rejected"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    rolled_back = "rolled_back"
    cancelled = "cancelled"


change_tickets = Table(
    "change_tickets",
    Base.metadata,
    Column("change_id", ForeignKey("changes.id", ondelete="CASCADE"), primary_key=True),
    Column("ticket_id", ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True, index=True),
)
change_devices = Table(
    "change_devices",
    Base.metadata,
    Column("change_id", ForeignKey("changes.id", ondelete="CASCADE"), primary_key=True),
    Column("device_id", ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True, index=True),
)
change_systems = Table(
    "change_systems",
    Base.metadata,
    Column("change_id", ForeignKey("changes.id", ondelete="CASCADE"), primary_key=True),
    Column("system_id", ForeignKey("systems.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class ChangeTemplate(Base):
    """A routine change written once (Settings > Change templates). A standard (pre-approved) one
    needs no approval each time it's used."""

    __tablename__ = "change_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    pre_approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    implementation_plan: Mapped[str] = mapped_column(Text, default="", server_default="")
    rollback_plan: Mapped[str] = mapped_column(Text, default="", server_default="")
    test_plan: Mapped[str] = mapped_column(Text, default="", server_default="")
    impact: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    likelihood: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Change(Base):
    """A planned change to systems (a firmware upgrade, a firewall rule, a patch ring): its risk
    (impact x likelihood), plans (the rollback plan is required), window, approval and outcome."""

    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[ChangeKind] = mapped_column(str_enum(ChangeKind), default=ChangeKind.normal)
    status: Mapped[ChangeStatus] = mapped_column(str_enum(ChangeStatus), default=ChangeStatus.draft, index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("change_templates.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    implementation_plan: Mapped[str] = mapped_column(Text, default="", server_default="")
    rollback_plan: Mapped[str] = mapped_column(Text, default="", server_default="")
    test_plan: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 1 (low) to 3 (high) each; risk = impact x likelihood.
    impact: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    likelihood: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What happened, filled in when it ends.
    outcome: Mapped[str] = mapped_column(Text, default="", server_default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Made by an integration rather than a person: "action1:<automation id>" for an Action1 patch run.
    source: Mapped[str] = mapped_column(String(200), default="", server_default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner: Mapped["User | None"] = relationship(foreign_keys=[owner_id])
    requested_by: Mapped["User | None"] = relationship(foreign_keys=[requested_by_id])
    approved_by: Mapped["User | None"] = relationship(foreign_keys=[approved_by_id])
    tickets: Mapped[list["Ticket"]] = relationship(secondary=change_tickets, order_by="Ticket.id")
    devices: Mapped[list["Device"]] = relationship(secondary=change_devices, order_by="Device.name")
    systems: Mapped[list["System"]] = relationship(secondary=change_systems, order_by="System.name")
    events: Mapped[list["ChangeEvent"]] = relationship(order_by="ChangeEvent.id", cascade="all, delete-orphan")


class ChangeEvent(Base):
    __tablename__ = "change_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey("changes.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # created, edited, submitted, approved, rejected, started, completed, failed, rolled_back, cancelled, reopened
    type: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")

    actor: Mapped["User | None"] = relationship()


class ChecklistKind(enum.StrEnum):
    onboarding = "onboarding"  # the date is the start date
    offboarding = "offboarding"  # the date is the last day
    other = "other"


class ChecklistTemplate(Base):
    """A named list of steps (Settings > Checklists): started on a ticket, each step becomes a child
    ticket, due a number of days from the checklist's date. A step can also act: "disable_ad_account"
    queues the account's disabling on a relay for that day."""

    __tablename__ = "checklist_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[ChecklistKind] = mapped_column(str_enum(ChecklistKind), default=ChecklistKind.other)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # [{"title", "details", "queue_id", "assignee_id", "offset_days", "action"}]
    items: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TicketChecklist(Base):
    """A checklist started on a ticket: who it's for, its date, and its steps' child tickets."""

    __tablename__ = "ticket_checklists"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("checklist_templates.id", ondelete="SET NULL"))
    # As it was when started (the template may change later).
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[ChecklistKind] = mapped_column(str_enum(ChecklistKind))
    for_name: Mapped[str] = mapped_column(String(200))
    for_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # The start date (onboarding) or last day (offboarding) that steps are due from.
    key_date: Mapped[date | None] = mapped_column(Date)
    started_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["ChecklistItem"]] = relationship(back_populates="checklist", order_by="ChecklistItem.position", cascade="all, delete-orphan")
    for_user: Mapped["User | None"] = relationship(foreign_keys=[for_user_id])


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    checklist_id: Mapped[int] = mapped_column(ForeignKey("ticket_checklists.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(300))
    # Days from the checklist's date (kept so the date can be set or moved later).
    offset_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    due_on: Mapped[date | None] = mapped_column(Date)
    child_ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    action: Mapped[str | None] = mapped_column(String(50))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("script_runs.id", ondelete="SET NULL"))

    checklist: Mapped[TicketChecklist] = relationship(back_populates="items")
    child_ticket: Mapped["Ticket | None"] = relationship(foreign_keys=[child_ticket_id])


class CopilotConversation(Base):
    """Authorization metadata only; questions and answers stay out of this table."""

    __tablename__ = "copilot_conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(200))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    account_key: Mapped[str] = mapped_column(String(64))
    shared_ticket: Mapped[bool] = mapped_column(Boolean)
    shared_internal: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "conversation_id", name="uq_copilot_user_conversation"),)


class TeamsDelivery(Base):
    """Durable send intent; an uncertain external side effect must not be replayed."""

    __tablename__ = "teams_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketEvent(Base):
    """Append-only history: routing, assignment, escalation, status and every action on the ticket."""

    __tablename__ = "ticket_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    # Null actor means the system (routing engine, mailbox worker, SLA timer).
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    type: Mapped[EventType] = mapped_column(str_enum(EventType))
    data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    actor: Mapped[User | None] = relationship()


class InboundStatus(enum.StrEnum):
    processed = "processed"
    rejected = "rejected"  # sender not allowed, inactive, or automated mail
    failed = "failed"  # error while processing; retried if moved back to the inbox


class OutboundStatus(enum.StrEnum):
    queued = "queued"
    sent = "sent"
    failed = "failed"  # gave up after repeated errors


class InboundEmail(Base):
    """Every message the worker has taken from the mailbox, keyed by Message-ID so nothing is processed twice."""

    __tablename__ = "inbound_emails"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    internet_message_id: Mapped[str] = mapped_column(String(998), unique=True)
    from_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(998), default="")
    status: Mapped[InboundStatus] = mapped_column(str_enum(InboundStatus))
    reason: Mapped[str | None] = mapped_column(Text)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"), index=True)
    comment_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id", ondelete="SET NULL"))
    # The original .eml, kept for audit.
    raw_storage_key: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboundEmail(Base):
    """Outbox. Rows are written in the same transaction as the change they report; the worker sends them."""

    __tablename__ = "outbound_emails"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # None for mail that isn't about a ticket, e.g. an emailed report.
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    comment_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id", ondelete="SET NULL"))
    to_address: Mapped[str] = mapped_column(String(320))
    # Files sent with it: [{"filename", "content_type", "storage_key"}] (attachment storage).
    attachments: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Notices to staff (e.g. escalation to a queue manager), not the requester.
    # Replies to these are filed as internal notes.
    is_staff_notice: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    subject: Mapped[str] = mapped_column(String(998))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[OutboundStatus] = mapped_column(str_enum(OutboundStatus), default=OutboundStatus.queued)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Set once sent; replies quote it in In-Reply-To, which is how they thread.
    internet_message_id: Mapped[str | None] = mapped_column(String(998), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_outbound_emails_due", "status", "next_attempt_at"),)


# Counts only attachments the requester can see: not ones on internal notes.
_child = aliased(Ticket)
Ticket.child_count = column_property(select(func.count(_child.id)).where(_child.parent_id == Ticket.id).correlate_except(_child).scalar_subquery())
Ticket.open_child_count = column_property(
    select(func.count(_child.id))
    .where(_child.parent_id == Ticket.id, _child.status.not_in(["resolved", "closed"]))
    .correlate_except(_child)
    .scalar_subquery()
)
Ticket.attachment_count = column_property(
    select(func.count(Attachment.id))
    .outerjoin(Comment, Attachment.comment_id == Comment.id)
    .where(Attachment.ticket_id == Ticket.id, or_(Comment.id.is_(None), Comment.is_internal.is_(False)))
    .correlate_except(Attachment, Comment)
    .scalar_subquery()
)


class RuleTrigger(enum.StrEnum):
    on_create = "on_create"  # after routing, once the new ticket is saved
    on_update = "on_update"  # after a change is saved; never for changes rules make
    schedule = "schedule"  # every schedule_minutes; once per ticket until it stops matching


class RuleMode(enum.StrEnum):
    builder = "builder"  # conditions and actions from the form builder
    sql = "sql"  # KACE-style select SQL, optional update SQL (applied by the app, see app/rules)


ticket_rule_queues = _link("ticket_rule_queues", "ticket_rule", "queue")


class TicketRule(Base):
    """KACE-style custom ticket rule. Builder rules hold JSON conditions/actions; SQL rules hold SQL."""

    __tablename__ = "ticket_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    position: Mapped[int] = mapped_column(Integer, default=100, server_default=text("100"))
    trigger: Mapped[RuleTrigger] = mapped_column(str_enum(RuleTrigger))
    schedule_minutes: Mapped[int | None] = mapped_column(Integer)
    mode: Mapped[RuleMode] = mapped_column(str_enum(RuleMode), default=RuleMode.builder)
    # [{"field": "status", "op": "in", "value": ["open"]}, ...], all must match.
    conditions: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    select_sql: Mapped[str | None] = mapped_column(Text)
    # [{"type": "set_status", "value": "closed"}, ...], applied in order. SQL rules may have them too.
    actions: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    update_sql: Mapped[str | None] = mapped_column(Text)
    # Safety cap: a run that would change more tickets than this changes none.
    max_rows: Mapped[int] = mapped_column(Integer, default=100, server_default=text("100"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Empty means every queue.
    queues: Mapped[list[Queue]] = relationship(secondary=ticket_rule_queues, order_by=Queue.name)


class RuleRun(Base):
    """One execution of a rule: what matched, what changed, what was emailed, and any error."""

    __tablename__ = "rule_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("ticket_rules.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(32))  # on_create, on_update, schedule, manual
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matched: Mapped[int] = mapped_column(Integer, default=0)
    changed: Mapped[int] = mapped_column(Integer, default=0)
    emails: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    # Per-ticket detail, trimmed: [{"ticket_id": 1, "changes": [...], "notes": n, "emails": n}].
    details: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))


class RuleFire(Base):
    """A scheduled rule already acted on this ticket; cleared once the ticket stops matching."""

    __tablename__ = "rule_fires"

    rule_id: Mapped[int] = mapped_column(ForeignKey("ticket_rules.id", ondelete="CASCADE"), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True, index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoleMapping(Base):
    """Directory group -> app role. A user gets the highest role of any group they're in (nested)."""

    __tablename__ = "role_mappings"

    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole))

    group: Mapped[Group] = relationship()


class DirectorySyncRun(Base):
    __tablename__ = "directory_sync_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    users_seen: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    linked: Mapped[int] = mapped_column(Integer, default=0)
    deactivated: Mapped[int] = mapped_column(Integer, default=0)
    role_changes: Mapped[int] = mapped_column(Integer, default=0)
    groups_synced: Mapped[int] = mapped_column(Integer, default=0)
    devices_seen: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    # Trimmed list of what changed, for the admin screen.
    details: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))


class PriorityMatrixCell(Base):
    """Impact x urgency -> priority, editable by admins. One row per combination."""

    __tablename__ = "priority_matrix"

    impact: Mapped[Impact] = mapped_column(str_enum(Impact), primary_key=True)
    urgency: Mapped[Urgency] = mapped_column(str_enum(Urgency), primary_key=True)
    priority: Mapped[Priority] = mapped_column(str_enum(Priority))


class AppSetting(Base):
    """Settings admins change in the app (Settings screen), one JSON document per area.
    Secrets never go here: files like the Windows sign-in keytab live in HELPDESK_SECRETS_DIR."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class SystemEvent(Base):
    """The Log tab: warnings, errors and notable events from the API and the worker
    (directory, email, sign-in, settings...). Pruned after LOG_DAYS by the worker."""

    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    level: Mapped[str] = mapped_column(String(10))  # info, warning, error
    source: Mapped[str] = mapped_column(String(30), index=True)
    message: Mapped[str] = mapped_column(Text)
    # Traceback for errors.
    details: Mapped[str | None] = mapped_column(Text)
    # api or worker
    process: Mapped[str] = mapped_column(String(20))


class Wallboard(Base):
    """A read-only wall display (Settings > Remote displays): live counts, alerts and ticket lists for a
    big screen. Its secret link is the key; only a hash of it is kept."""

    __tablename__ = "wallboards"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # The first characters, to tell links apart in lists (the rest is never stored).
    prefix: Mapped[str] = mapped_column(String(12))
    # The queues it shows; empty: every active queue that isn't restricted.
    queue_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, server_default=text("'{}'"))
    # Unassigned longer than this raises an alert on the board.
    unassigned_alert_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default=text("30"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped["User | None"] = relationship()


class NetworkObservation(Base):
    """A client a vendor integration saw on the network (UniFi now, Juniper next): cached and read
    only. It is shown beside documented IP records and never changes them."""

    __tablename__ = "network_observations"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    # The site, console or switch it was read from.
    source_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), primary_key=True, index=True)
    source_name: Mapped[str] = mapped_column(String(255), default="")
    address: Mapped[str] = mapped_column(String(45), default="")
    hostname: Mapped[str] = mapped_column(String(255), default="")
    network: Mapped[str] = mapped_column(String(100), default="")
    uplink_name: Mapped[str] = mapped_column(String(255), default="")
    port: Mapped[str] = mapped_column(String(100), default="")
    ssid: Mapped[str] = mapped_column(String(100), default="")
    connection: Mapped[str] = mapped_column(String(16), default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NetworkInventory(Base):
    """Network equipment a vendor integration reported (gateways, switches, access points)."""

    __tablename__ = "network_inventory"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    device_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), default="")
    name: Mapped[str] = mapped_column(String(255), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    # gateway / switch / ap / other
    kind: Mapped[str] = mapped_column(String(16), default="other")
    mac: Mapped[str] = mapped_column(String(17), default="")
    address: Mapped[str] = mapped_column(String(45), default="")
    # online / offline / other
    state: Mapped[str] = mapped_column(String(16), default="other")
    firmware: Mapped[str] = mapped_column(String(100), default="")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NetworkOutage(Base):
    """A vendor-reported device's offline state across reads, and the ticket opened for it."""

    __tablename__ = "network_outages"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    device_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    is_down: Mapped[bool] = mapped_column(Boolean, default=False)
    down_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NetworkRead(Base):
    """A runner-driven vendor read (Juniper): the run it asked for and what came of it."""

    __tablename__ = "network_reads"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("script_runs.id", ondelete="CASCADE"), unique=True)
    scheduled: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")


class ServiceHeartbeat(Base):
    """A background service saying it's alive, and what its last pass did."""

    __tablename__ = "service_heartbeats"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    info: Mapped[dict] = mapped_column(JSONB, default=dict)


class WorkerJob(Base):
    """One of the background worker's jobs (email, directory sync...): its last run, and
    what admins asked for on Settings > Status (run now, pause)."""

    __tablename__ = "worker_jobs"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    run_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_summary: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceRequest(Base):
    """An admin asking a service to do something it does itself, e.g. the worker to restart."""

    __tablename__ = "service_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(30))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    requested_by: Mapped[str | None] = mapped_column(String(200))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SavedReport(Base):
    """A report someone built (Reports > Customize) and saved, for themselves or shared with
    every agent. spec is a reports.engine.ReportSpec."""

    __tablename__ = "saved_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    spec: Mapped[dict] = mapped_column(JSONB)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    shared: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner: Mapped[User | None] = relationship()


class ReportSchedule(Base):
    """A report emailed on a schedule (Reports > Email or schedule). It runs as its owner, so it
    shows only what they may see."""

    __tablename__ = "report_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # One of: a ready-made report, or a saved report.
    preset_key: Mapped[str | None] = mapped_column(String(100))
    saved_report_id: Mapped[int | None] = mapped_column(ForeignKey("saved_reports.id", ondelete="CASCADE"))
    # Relative period worked out at each run, e.g. previous_month (reports.delivery.PERIODS).
    period: Mapped[str] = mapped_column(String(30))
    recipients: Mapped[list] = mapped_column(JSONB, default=list)
    # pdf, csv or both
    format: Mapped[str] = mapped_column(String(10), default="pdf")
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    # once, daily, weekly or monthly
    frequency: Mapped[str] = mapped_column(String(10))
    # weekly: 0 = Monday ... 6 = Sunday
    weekdays: Mapped[list] = mapped_column(JSONB, default=list)
    # monthly: 1-28, or -1 for the last day
    day_of_month: Mapped[int | None] = mapped_column(Integer)
    # Server's local time, HH:MM
    time_of_day: Mapped[str] = mapped_column(String(5))
    start_date: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped[User | None] = relationship()


class ServiceNotice(Base):
    """An outage or known problem admins tell people about (Settings > Notices). Shown after
    sign-in and as a banner in the console and portal while it's current."""

    __tablename__ = "service_notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    # info, warning or outage
    severity: Mapped[str] = mapped_column(String(10), default="info")
    # everyone, staff (agents and admins) or requesters
    audience: Mapped[str] = mapped_column(String(12), default="everyone")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # None: until taken down.
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    created_by: Mapped[User | None] = relationship()


class KbAudience(enum.StrEnum):
    everyone = "everyone"  # requesters see it in the portal, and it's suggested as they type a request
    staff = "staff"  # technicians and admins only


class KbStatus(enum.StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class KbArticle(Base):
    """A knowledge base article: a fix or how-to in simple Markdown. Found by full-text search
    (title and keywords weigh most), suggested in the portal and on tickets."""

    __tablename__ = "kb_articles"
    __table_args__ = (Index("ix_kb_articles_search", "search", postgresql_using="gin"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Other words people use for it ("vpn anyconnect remote access"), searched like the title.
    keywords: Mapped[str] = mapped_column(String(500), default="", server_default="")
    # A ticket category (as queues name them), to suggest it for requests in that category.
    category: Mapped[str | None] = mapped_column(String(100))
    audience: Mapped[KbAudience] = mapped_column(str_enum(KbAudience), default=KbAudience.staff)
    status: Mapped[KbStatus] = mapped_column(str_enum(KbStatus), default=KbStatus.draft, index=True)
    # Drafted from this ticket.
    source_ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id", ondelete="SET NULL"))
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    views: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    helpful_yes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    helpful_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(keywords, '')), 'A') || setweight(to_tsvector('english', coalesce(body, '')), 'B')",
            persisted=True,
        ),
    )

    author: Mapped["User | None"] = relationship(foreign_keys=[author_id])
    updated_by: Mapped["User | None"] = relationship(foreign_keys=[updated_by_id])


class KbArticleVersion(Base):
    """A saved version of an article's text (written when its title, text or keywords change)."""

    __tablename__ = "kb_article_versions"
    __table_args__ = (UniqueConstraint("article_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("kb_articles.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    keywords: Mapped[str] = mapped_column(String(500), default="", server_default="")
    note: Mapped[str] = mapped_column(String(200), default="", server_default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    author: Mapped["User | None"] = relationship()


class KbFile(Base):
    """An image in a knowledge base article, shown inline (![alt](/api/kb/files/<id>)). Bytes live in
    storage; who may see it is who may read the article."""

    __tablename__ = "kb_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id: Mapped[int] = mapped_column(ForeignKey("kb_articles.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KbArticleTicket(Base):
    """An article used on a ticket (sent to the requester or followed to fix it)."""

    __tablename__ = "kb_article_tickets"

    article_id: Mapped[int] = mapped_column(ForeignKey("kb_articles.id", ondelete="CASCADE"), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True, index=True)
    linked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    article: Mapped[KbArticle] = relationship()


class TicketSurvey(Base):
    """A satisfaction survey sent when a ticket was solved (a random share of them, Settings >
    Surveys). The emailed link carries a token; only its hash is kept."""

    __tablename__ = "ticket_surveys"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), unique=True)
    requester_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # Who handled it, as it was when the survey went out (for reports by technician).
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    queue_id: Mapped[int | None] = mapped_column(ForeignKey("queues.id", ondelete="SET NULL"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 1 (very unhappy) to 5 (very happy); None until answered.
    rating: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="", server_default="")
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ticket: Mapped["Ticket"] = relationship()
    requester: Mapped["User | None"] = relationship(foreign_keys=[requester_id])
    assignee: Mapped["User | None"] = relationship(foreign_keys=[assignee_id])


class NutanixVm(Base):
    """A VM as Prism Central last reported it. Kept when it disappears (present=False), so ticket links
    and action history stay readable."""

    __tablename__ = "nutanix_vms"

    ext_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    power_state: Mapped[str] = mapped_column(String(20), default="", server_default="")
    cluster_name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    host_name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    vcpus: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    memory_mb: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ips: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    macs: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    guest_tools: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    guest_tools_reachable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # The device it was matched to by name (AD computer or one added by hand), if any.
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), index=True)
    present: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped[Device | None] = relationship()


class TicketNutanixVm(Base):
    """A VM linked to a ticket (the ticket's history notes who linked it and when)."""

    __tablename__ = "ticket_nutanix_vms"

    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    vm_id: Mapped[str] = mapped_column(ForeignKey("nutanix_vms.ext_id", ondelete="CASCADE"), primary_key=True, index=True)
    linked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    vm: Mapped[NutanixVm] = relationship()


class NutanixAction(Base):
    """A power action asked for from a ticket, and the Prism Central task that carries it out."""

    __tablename__ = "nutanix_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    vm_id: Mapped[str] = mapped_column(ForeignKey("nutanix_vms.ext_id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(20))
    task_id: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # submitted, running, succeeded, failed or unknown (Prism Central stopped answering about it).
    status: Mapped[str] = mapped_column(String(20), default="submitted", server_default="submitted", index=True)
    detail: Mapped[str] = mapped_column(Text, default="", server_default="")
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vm: Mapped[NutanixVm] = relationship()
    requested_by: Mapped[User | None] = relationship()


class CannedReply(Base):
    """A saved reply technicians insert into the composer. Shared (owner_id None, managed with the
    canned_replies_manage permission, for every queue or one) or one technician's own."""

    __tablename__ = "canned_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    # Plain text with {first_name}, {name}, {ticket}, {subject}, {technician}, {queue}, {portal}.
    body: Mapped[str] = mapped_column(Text)
    # Inserting it switches the composer to a reply to the requester (else an internal note).
    to_requester: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    queue_id: Mapped[int | None] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    queue: Mapped[Queue | None] = relationship()


class TimeEntry(Base):
    """Time a technician spent on a ticket (its Time panel; the Time spent report)."""

    __tablename__ = "time_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    minutes: Mapped[int] = mapped_column(Integer)
    worked_on: Mapped[date] = mapped_column(Date, index=True)
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    # Logged with this reply or note, when it was.
    comment_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User | None] = relationship()

