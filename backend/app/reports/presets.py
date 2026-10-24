# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ready-made reports: named ReportSpecs. The Reports screen lists them; each can be run
for any period and opened in the builder to change."""

from dataclasses import dataclass

from app.reports.engine import ReportSpec


@dataclass(frozen=True)
class Preset:
    key: str
    title: str
    description: str
    group: str
    spec: ReportSpec


def _p(key, title, description, group, **spec) -> Preset:
    return Preset(key, title, description, group, ReportSpec(**spec))


PRESETS = [
    _p("total-by-status", "Total tickets by status", "How many tickets were opened in the period, and where they stand now.",
       "Tickets", group_by=["status"], measures=["count"]),
    _p("opened-over-time", "Tickets opened over time", "New tickets per week, with how many are still open.",
       "Tickets", group_by=["created_week"], measures=["count", "open"]),
    _p("by-queue", "Tickets by queue", "Volume, backlog and time to resolve for each queue.",
       "Tickets", group_by=["queue"], measures=["count", "open", "avg_hours_to_resolve"]),
    _p("by-category", "Tickets by category", "What people ask for most.",
       "Tickets", group_by=["category"], measures=["count", "open"]),
    _p("by-source", "Tickets by source", "Email, portal, agent or API.",
       "Tickets", group_by=["source"], measures=["count"]),
    _p("by-priority", "Tickets by priority", "Volume and SLA results for each priority.",
       "Tickets", group_by=["priority"], measures=["count", "open", "response_breached", "resolution_breached"]),
    _p("per-technician", "Tickets per technician", "Assigned, still open, resolved, average time to resolve and SLA misses for each technician.",
       "Technicians", group_by=["assignee"], measures=["count", "open", "resolved", "avg_hours_to_resolve", "resolution_breached"]),
    _p("technician-backlog", "Open tickets per technician", "Each technician's current open tickets, by priority.",
       "Technicians", group_by=["assignee", "priority"], measures=["open"], filters={"status": ["new", "open", "waiting_on_user"]}),
    _p("by-requester", "Tickets opened per user", "Who opens the most tickets.",
       "Requesters", group_by=["requester"], measures=["count", "open"], limit=100),
    _p("by-department", "Tickets per department", "Tickets by department (the ticket's, which starts from the requester's AD department).",
       "Requesters", group_by=["department"], measures=["count", "open", "avg_hours_to_resolve"]),
    _p("by-resolution-type", "Tickets by resolution type", "How tickets were resolved.",
       "Resolutions", group_by=["resolution_type"], measures=["count", "avg_hours_to_resolve"], date_field="resolved"),
    _p("resolution-per-technician", "Resolution types per technician", "How each technician's tickets were resolved.",
       "Resolutions", group_by=["assignee", "resolution_type"], measures=["count"], date_field="resolved"),
    _p("by-closure-reason", "Tickets by closure reason", "Why tickets were closed: resolved, cancelled, no response...",
       "Resolutions", group_by=["closure_reason"], measures=["count"], date_field="resolved"),
    _p("activity-per-technician", "Replies and notes per technician", "Work done on tickets: replies, internal notes and tickets touched.",
       "Technicians", dataset="activity", group_by=["author"], measures=["count", "tickets", "notes", "emailed"],
       filters={}, date_field="created"),
    _p("activity-over-time", "Ticket activity over time", "Replies and notes per week, by kind.",
       "Technicians", dataset="activity", group_by=["week", "kind"], measures=["count"], date_field="created"),
    _p("users-by-department", "People by department", "Everyone the service desk knows, by AD department.",
       "People", dataset="users", group_by=["department"], measures=["count", "active", "staff"], date_field="created"),
    _p("users-by-role", "People by role and sign-in", "Requesters, agents and admins; AD or local accounts.",
       "People", dataset="users", group_by=["role", "auth_source"], measures=["count", "active"], date_field="created"),
    _p("email-received", "Email received", "What happened to email sent to the service desk (admins).",
       "Email", dataset="email_in", group_by=["status"], measures=["count", "tickets", "replies"], date_field="processed"),
    _p("email-rejected-by-domain", "Rejected email by sender domain", "Who's emailing the service desk without being accepted (admins).",
       "Email", dataset="email_in", group_by=["domain"], measures=["count"], filters={"status": ["rejected"]}, date_field="processed"),
    _p("email-sent", "Email sent", "Replies, notices and reports sent, and failures (admins).",
       "Email", dataset="email_out", group_by=["kind", "status"], measures=["count"], date_field="created"),
    _p("sla-by-priority", "SLA compliance by priority", "First response and resolution SLA results.",
       "SLA", group_by=["priority"], measures=["count", "response_breached", "resolution_breached", "resolution_met_pct"]),
    _p("sla-by-month", "SLA compliance by month", "Resolution SLA met, month by month.",
       "SLA", group_by=["created_month"], measures=["count", "resolution_breached", "resolution_met_pct", "avg_hours_first_response"]),
    _p("satisfaction-by-technician", "Satisfaction by technician", "Surveys sent and answered, average rating and unhappy answers for each technician.",
       "Satisfaction", dataset="surveys", group_by=["technician"], measures=["count", "answered", "avg_rating", "happy_pct", "unhappy"], date_field="sent"),
    _p("satisfaction-by-queue", "Satisfaction by queue", "How each queue's requesters rate the service.",
       "Satisfaction", dataset="surveys", group_by=["queue"], measures=["count", "response_pct", "avg_rating", "happy_pct"], date_field="sent"),
    _p("satisfaction-by-month", "Satisfaction by month", "Average rating and response rate, month by month.",
       "Satisfaction", dataset="surveys", group_by=["sent_month"], measures=["count", "response_pct", "avg_rating", "unhappy"], date_field="sent"),
    _p("po-spend-by-vendor", "Purchase order spend by vendor", "What was ordered from whom in the period (cancelled POs left out).",
       "Purchasing", dataset="purchase_orders", group_by=["vendor"], measures=["count", "value", "open"], date_field="ordered"),
    _p("po-spend-by-department", "Purchase order spend by department", "Who's buying what, by department.",
       "Purchasing", dataset="purchase_orders", group_by=["department"], measures=["count", "value"], date_field="ordered"),
    _p("po-by-month", "Purchase orders by month", "How much was ordered each month.",
       "Purchasing", dataset="purchase_orders", group_by=["order_month"], measures=["count", "value"], date_field="ordered"),
    _p("po-open", "Open purchase orders", "Ordered but not all in yet, and which are late.",
       "Purchasing", dataset="purchase_orders", group_by=["vendor", "status"], measures=["open", "late", "value"],
       filters={"status": ["requested", "approved", "ordered", "partially_received"]}, date_field="created"),
]
BY_KEY = {p.key: p for p in PRESETS}
