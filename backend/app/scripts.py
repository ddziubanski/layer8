# SPDX-License-Identifier: AGPL-3.0-or-later
"""The script library: parameter definitions and values, versions and diffs, and running queries.

A query is read-only SQL on the restricted rules login (as SQL reports are), with its parameters
bound by name (:days). A query that returns a device_id or user_id column can serve as a dynamic
group. Actions (PowerShell, bash, Python, Arista EOS) run on a runner; see runs in a later slice.
"""

import difflib
import re
from datetime import date
from typing import Any

from app.models import ScriptKind, ScriptLanguage

PARAM_TYPES = ("text", "int", "bool", "date", "choice")
NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
# Bound by the app itself in queries.
RESERVED = {"start", "end"}
LANGUAGES = {
    ScriptKind.query: {ScriptLanguage.sql},
    ScriptKind.action: {ScriptLanguage.powershell, ScriptLanguage.bash, ScriptLanguage.python, ScriptLanguage.eos},
}


# Built-in scripts (IP scans, AD changes...) are made by the app under names starting with this.
# They run only from their own feature, with its checks; nobody may name a script like them.
BUILTIN_PREFIX = "Layer 8:"


def is_builtin(name: str) -> bool:
    return name.strip().lower().startswith(BUILTIN_PREFIX.lower())


class ScriptError(ValueError):
    """The message is safe to show."""


def check_definition(kind: ScriptKind, language: ScriptLanguage, parameters: list[dict]) -> list[dict]:
    """Clean parameter definitions, or ScriptError naming what's wrong."""
    if language not in LANGUAGES[kind]:
        allowed = ", ".join(sorted(LANGUAGES[kind]))
        raise ScriptError(f"{'An' if kind == ScriptKind.action else 'A'} {kind} script is written in {allowed}")
    out, seen = [], set()
    for p in parameters:
        name = str(p.get("name", "")).strip()
        if not NAME.fullmatch(name):
            raise ScriptError(f"Parameter name {name!r}: lowercase letters, digits and _ (starting with a letter)")
        if name in seen or name in RESERVED:
            raise ScriptError(f"Parameter name {name!r} is {'used twice' if name in seen else 'reserved'}")
        seen.add(name)
        kind_ = p.get("type", "text")
        if kind_ not in PARAM_TYPES:
            raise ScriptError(f"{name}: type must be one of {', '.join(PARAM_TYPES)}")
        choices = [str(c).strip() for c in p.get("choices") or [] if str(c).strip()]
        if kind_ == "choice" and not choices:
            raise ScriptError(f"{name}: list the choices")
        clean = {
            "name": name,
            "label": str(p.get("label") or name).strip()[:100],
            "type": kind_,
            "required": bool(p.get("required", False)),
            "default": p.get("default"),
            "choices": choices if kind_ == "choice" else [],
        }
        if clean["default"] not in (None, ""):
            clean["default"] = coerce(clean, clean["default"])
        else:
            clean["default"] = None
        out.append(clean)
    return out


def coerce(param: dict, value: Any) -> Any:
    """A value typed for this parameter, or ScriptError."""
    label = param["label"]
    if value in (None, ""):
        if param["required"]:
            raise ScriptError(f"{label} is required")
        return None
    t = param["type"]
    try:
        if t == "int":
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if t == "bool":
            if isinstance(value, bool):
                return value
            if str(value).lower() in ("true", "1", "yes"):
                return True
            if str(value).lower() in ("false", "0", "no"):
                return False
            raise ValueError
        if t == "date":
            return value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        raise ScriptError(f"{label}: expected {'a whole number' if t == 'int' else 'yes or no' if t == 'bool' else 'a date (YYYY-MM-DD)'}") from None
    value = str(value)
    if t == "choice" and value not in param["choices"]:
        raise ScriptError(f"{label}: pick one of {', '.join(param['choices'])}")
    if len(value) > 2000:
        raise ScriptError(f"{label}: too long")
    return value


def values_for(parameters: list[dict], given: dict[str, Any]) -> dict[str, Any]:
    """Every parameter's value (given, else its default), typed. Unknown names are refused."""
    known = {p["name"] for p in parameters}
    if unknown := set(given) - known:
        raise ScriptError(f"Unknown parameter(s): {', '.join(sorted(unknown))}")
    return {p["name"]: coerce(p, given.get(p["name"], p["default"])) for p in parameters}


def diff(old: str, new: str, old_label: str, new_label: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), old_label, new_label))


# Examples an admin can add from the Scripts page: original queries for this schema. Some answer the
# same questions as the KACE community's K1000 queries (which have no licence, so none is copied).
EXAMPLES: list[dict] = [
    {
        "name": "Tickets untouched for a while, by technician",
        "description": "Open tickets nobody has updated for the given number of hours, grouped by who has them.",
        "parameters": [{"name": "hours", "label": "Untouched for (hours)", "type": "int", "required": True, "default": 4}],
        "body": """SELECT coalesce(u.display_name, '(unassigned)') AS technician, count(*) AS tickets,
       min(t.updated_at) AS oldest_update
FROM tickets t
LEFT JOIN users u ON u.id = t.assignee_id
WHERE t.status IN ('new', 'open')
  AND t.updated_at < now() - make_interval(hours => :hours)
GROUP BY 1
ORDER BY tickets DESC""",
    },
    {
        "name": "Average days to close, by category",
        "description": "For tickets resolved in the chosen period.",
        "parameters": [],
        "body": """SELECT coalesce(category, '(none)') AS category, count(*) AS tickets,
       round(avg(extract(epoch FROM resolved_at - created_at) / 86400)::numeric, 1) AS avg_days
FROM tickets
WHERE resolved_at >= :start AND resolved_at < :end
GROUP BY 1
ORDER BY avg_days DESC""",
    },
    {
        "name": "Devices not seen for a while",
        "description": "AD computers whose last logon is older than the given number of days (AD's lastLogonTimestamp lags by up to two weeks). A dynamic group: it returns device_id.",
        "parameters": [{"name": "days", "label": "Not seen for (days)", "type": "int", "required": True, "default": 30}],
        "body": """SELECT d.id AS device_id, d.name, d.os, d.last_logon_at
FROM devices d
WHERE d.is_active AND d.enabled
  AND (d.last_logon_at IS NULL OR d.last_logon_at < now() - make_interval(days => :days))
ORDER BY d.last_logon_at NULLS FIRST""",
    },
    {
        "name": "Devices with no assigned person",
        "description": "A dynamic group: it returns device_id.",
        "parameters": [],
        "body": """SELECT d.id AS device_id, d.name, d.os, d.directory_dn
FROM devices d
WHERE d.is_active AND d.assigned_user_id IS NULL
ORDER BY d.name""",
    },
    {
        "name": "Devices assigned to someone who has left",
        "description": "The assigned person's account is disabled or gone from AD. A dynamic group: it returns device_id.",
        "parameters": [],
        "body": """SELECT d.id AS device_id, d.name, u.display_name AS assigned_to, u.email
FROM devices d
JOIN users u ON u.id = d.assigned_user_id
WHERE d.is_active AND NOT u.is_active
ORDER BY u.display_name, d.name""",
    },
    {
        "name": "People added recently",
        "description": "Accounts created in the chosen period. A dynamic group: it returns user_id.",
        "parameters": [],
        "body": """SELECT u.id AS user_id, u.display_name, u.email, u.created_at
FROM users u
WHERE u.created_at >= :start AND u.created_at < :end
ORDER BY u.created_at DESC""",
    },
    # Tickets: throughput, ageing and follow-through.
    {
        "name": "Tickets opened and resolved, per month",
        "description": "Monthly counts for the chosen period (no period: from the first ticket to now).",
        "parameters": [],
        "body": """WITH oldest AS (
  SELECT min(created_at) AS at FROM tickets
), span AS (
  -- Never from -infinity: start at the period's start or the first ticket, whichever is later.
  SELECT date_trunc('month', greatest(CAST(:start AS timestamptz), at)) AS first,
         least(CAST(:end AS timestamptz), now()) AS last
  FROM oldest WHERE at IS NOT NULL
), months AS (
  SELECT generate_series(first, last, interval '1 month') AS month FROM span
)
SELECT to_char(m.month, 'YYYY-MM') AS month,
       (SELECT count(*) FROM tickets t WHERE t.created_at >= m.month AND t.created_at < m.month + interval '1 month') AS opened,
       (SELECT count(*) FROM tickets t WHERE t.resolved_at >= m.month AND t.resolved_at < m.month + interval '1 month') AS resolved
FROM months m
ORDER BY m.month""",
    },
    {
        "name": "Open tickets by queue and technician",
        "description": "Who is holding what right now, with the oldest ticket in each pile.",
        "parameters": [],
        "body": """SELECT q.name AS queue, coalesce(u.display_name, '(unassigned)') AS technician,
       count(*) AS open_tickets, min(t.created_at) AS oldest
FROM tickets t
JOIN queues q ON q.id = t.queue_id
LEFT JOIN users u ON u.id = t.assignee_id
WHERE t.status IN ('new', 'open', 'waiting_on_user')
GROUP BY q.name, u.display_name
ORDER BY q.name, open_tickets DESC""",
    },
    {
        "name": "Open tickets by age, per queue",
        "description": "How long open tickets have been waiting: under a day, 1-3 days, 3-7 days, over a week.",
        "parameters": [],
        "body": """SELECT q.name AS queue,
       count(*) FILTER (WHERE t.created_at > now() - interval '1 day') AS under_1_day,
       count(*) FILTER (WHERE t.created_at <= now() - interval '1 day' AND t.created_at > now() - interval '3 days') AS days_1_to_3,
       count(*) FILTER (WHERE t.created_at <= now() - interval '3 days' AND t.created_at > now() - interval '7 days') AS days_3_to_7,
       count(*) FILTER (WHERE t.created_at <= now() - interval '7 days') AS over_a_week,
       count(*) AS total
FROM tickets t
JOIN queues q ON q.id = t.queue_id
WHERE t.status IN ('new', 'open', 'waiting_on_user')
GROUP BY q.name
ORDER BY over_a_week DESC, total DESC""",
    },
    {
        "name": "Tickets reopened in the period",
        "description": "Tickets that went from resolved or closed back to open, with who reopened them.",
        "parameters": [],
        "body": """SELECT t.id AS ticket, t.subject, e.created_at AS reopened_at,
       e.data->>'before' AS was, coalesce(u.display_name, 'automation') AS reopened_by
FROM ticket_events e
JOIN tickets t ON t.id = e.ticket_id
LEFT JOIN users u ON u.id = e.actor_id
WHERE e.type = 'status_changed'
  AND e.data->>'before' IN ('resolved', 'closed')
  AND e.data->>'after' NOT IN ('resolved', 'closed')
  AND e.created_at >= :start AND e.created_at < :end
ORDER BY e.created_at DESC""",
    },
    {
        "name": "Tickets reopened more than once",
        "description": "A sign the fix didn't hold: two or more reopenings over the ticket's life.",
        "parameters": [],
        "body": """SELECT t.id AS ticket, t.subject, t.status, count(*) AS times_reopened, max(e.created_at) AS last_reopened
FROM ticket_events e
JOIN tickets t ON t.id = e.ticket_id
WHERE e.type = 'status_changed'
  AND e.data->>'before' IN ('resolved', 'closed')
  AND e.data->>'after' NOT IN ('resolved', 'closed')
GROUP BY t.id, t.subject, t.status
HAVING count(*) > 1
ORDER BY times_reopened DESC, last_reopened DESC""",
    },
    {
        "name": "Unassigned for too long",
        "description": "Open tickets with no technician after the given number of hours.",
        "parameters": [{"name": "hours", "label": "Unassigned for (hours)", "type": "int", "required": True, "default": 24}],
        "body": """SELECT t.id AS ticket, t.subject, q.name AS queue, t.priority, t.source, t.created_at
FROM tickets t
JOIN queues q ON q.id = t.queue_id
WHERE t.status IN ('new', 'open', 'waiting_on_user') AND t.assignee_id IS NULL
  AND t.created_at < now() - make_interval(hours => :hours)
ORDER BY t.created_at""",
    },
    {
        "name": "Email tickets nobody has picked up",
        "description": "Tickets that came in by email and still have no technician after the given number of hours.",
        "parameters": [{"name": "hours", "label": "Waiting for (hours)", "type": "int", "required": True, "default": 2}],
        "body": """SELECT t.id AS ticket, t.subject, q.name AS queue, t.created_at
FROM tickets t
JOIN queues q ON q.id = t.queue_id
WHERE t.source = 'email' AND t.assignee_id IS NULL AND t.status IN ('new', 'open')
  AND t.created_at < now() - make_interval(hours => :hours)
ORDER BY t.created_at""",
    },
    {
        "name": "Open tickets with their latest reply or note",
        "description": "Each open ticket's most recent comment: who wrote it, when, and the start of what they said.",
        "parameters": [],
        "body": """SELECT t.id AS ticket, t.subject, coalesce(a.display_name, '(unassigned)') AS technician,
       c.created_at AS last_comment_at, w.display_name AS by, c.is_internal AS internal,
       left(regexp_replace(c.body, '[[:space:]]+', ' ', 'g'), 120) AS comment
FROM tickets t
LEFT JOIN users a ON a.id = t.assignee_id
LEFT JOIN LATERAL (
  SELECT * FROM comments x WHERE x.ticket_id = t.id ORDER BY x.created_at DESC, x.id DESC LIMIT 1
) c ON true
LEFT JOIN users w ON w.id = c.author_id
WHERE t.status IN ('new', 'open', 'waiting_on_user')
ORDER BY c.created_at NULLS FIRST""",
    },
    {
        "name": "First response time, by priority",
        "description": "For tickets opened in the period: average hours to the first response, and how many were answered within their SLA's first-response time.",
        "parameters": [],
        "body": """SELECT t.priority, count(*) AS tickets, count(t.first_responded_at) AS answered,
       round(avg(extract(epoch FROM t.first_responded_at - t.created_at) / 3600)::numeric, 1) AS avg_hours_to_answer,
       count(*) FILTER (WHERE t.first_responded_at <= t.first_response_due_at) AS answered_in_time
FROM tickets t
WHERE t.created_at >= :start AND t.created_at < :end
GROUP BY t.priority
ORDER BY array_position(ARRAY['critical', 'high', 'medium', 'low'], t.priority::text)""",
    },
    {
        "name": "Resolved within a number of hours, by priority",
        "description": "For tickets resolved in the period: how many were resolved within the given number of hours of being opened.",
        "parameters": [{"name": "hours", "label": "Within (hours)", "type": "int", "required": True, "default": 48}],
        "body": """SELECT t.priority, count(*) AS resolved,
       count(*) FILTER (WHERE t.resolved_at <= t.created_at + make_interval(hours => :hours)) AS within_limit,
       round(100.0 * count(*) FILTER (WHERE t.resolved_at <= t.created_at + make_interval(hours => :hours)) / count(*), 1) AS percent
FROM tickets t
WHERE t.resolved_at >= :start AND t.resolved_at < :end
GROUP BY t.priority
ORDER BY array_position(ARRAY['critical', 'high', 'medium', 'low'], t.priority::text)""",
    },
    {
        "name": "Tickets opened by hour of day",
        "description": "When the work arrives, for staffing desk hours (server time).",
        "parameters": [],
        "body": """SELECT extract(hour FROM t.created_at)::int AS hour, count(*) AS tickets,
       count(*) FILTER (WHERE t.source = 'email') AS by_email, count(*) FILTER (WHERE t.source = 'portal') AS by_portal
FROM tickets t
WHERE t.created_at >= :start AND t.created_at < :end
GROUP BY 1
ORDER BY 1""",
    },
    {
        "name": "Tickets moved to another queue",
        "description": "Moves between queues in the period, by hand or by a rule: from, to and who moved it.",
        "parameters": [],
        "body": """SELECT t.id AS ticket, t.subject, e.created_at AS moved_at, f.name AS from_queue, q.name AS to_queue,
       coalesce(u.display_name, 'automation') AS moved_by
FROM ticket_events e
JOIN tickets t ON t.id = e.ticket_id
LEFT JOIN queues f ON f.id = (e.data->'before'->>'queue_id')::int
LEFT JOIN queues q ON q.id = (e.data->'after'->>'queue_id')::int
LEFT JOIN users u ON u.id = e.actor_id
WHERE e.type = 'assigned'
  AND e.data->'before'->>'queue_id' IS DISTINCT FROM e.data->'after'->>'queue_id'
  AND e.created_at >= :start AND e.created_at < :end
ORDER BY e.created_at DESC""",
    },
    {
        "name": "Busiest requesters",
        "description": "Who opened the most tickets in the period, and how many are still open.",
        "parameters": [],
        "body": """SELECT u.display_name AS requester, u.email, count(*) AS tickets,
       count(*) FILTER (WHERE t.status IN ('new', 'open', 'waiting_on_user')) AS still_open
FROM tickets t
JOIN users u ON u.id = t.requester_id
WHERE t.created_at >= :start AND t.created_at < :end
GROUP BY u.id, u.display_name, u.email
ORDER BY tickets DESC
LIMIT 50""",
    },
    # Devices (each returns device_id or user_id, so it can be a dynamic group).
    {
        "name": "Warranty ending soon",
        "description": "Active devices whose warranty ends within the given number of days (or has ended). A dynamic group: it returns device_id.",
        "parameters": [{"name": "days", "label": "Ending within (days)", "type": "int", "required": True, "default": 60}],
        "body": """SELECT d.id AS device_id, d.name, d.manufacturer, d.model, d.serial, d.warranty_ends
FROM devices d
WHERE d.is_active AND d.warranty_ends IS NOT NULL
  AND d.warranty_ends < current_date + :days
ORDER BY d.warranty_ends""",
    },
    {
        "name": "Due for replacement",
        "description": "Active devices whose replace-by date falls within the given number of days. A dynamic group: it returns device_id.",
        "parameters": [{"name": "days", "label": "Due within (days)", "type": "int", "required": True, "default": 90}],
        "body": """SELECT d.id AS device_id, d.name, d.model, d.purchased_on, d.replace_by,
       u.display_name AS assigned_to
FROM devices d
LEFT JOIN users u ON u.id = d.assigned_user_id
WHERE d.is_active AND d.replace_by IS NOT NULL
  AND d.replace_by < current_date + :days
ORDER BY d.replace_by""",
    },
    {
        "name": "Devices without a purchase date",
        "description": "Gaps in the lifecycle records. A dynamic group: it returns device_id.",
        "parameters": [],
        "body": """SELECT d.id AS device_id, d.name, d.manufacturer, d.model, d.serial
FROM devices d
WHERE d.is_active AND d.purchased_on IS NULL
ORDER BY d.name""",
    },
    {
        "name": "Devices by operating system",
        "description": "How many active devices run each OS and version.",
        "parameters": [],
        "body": """SELECT coalesce(d.os, '(unknown)') AS os, coalesce(d.os_version, '') AS version, count(*) AS devices
FROM devices d
WHERE d.is_active
GROUP BY 1, 2
ORDER BY devices DESC""",
    },
    {
        "name": "People with more than one computer",
        "description": "Active people with two or more active devices assigned. A dynamic group: it returns user_id.",
        "parameters": [],
        "body": """SELECT u.id AS user_id, u.display_name, u.email, count(*) AS devices,
       string_agg(d.name, ', ' ORDER BY d.name) AS names
FROM devices d
JOIN users u ON u.id = d.assigned_user_id
WHERE d.is_active AND u.is_active
GROUP BY u.id, u.display_name, u.email
HAVING count(*) > 1
ORDER BY devices DESC""",
    },
    {
        "name": "Same serial number on more than one device",
        "description": "Duplicates that usually mean a renamed or re-imaged machine left behind. A dynamic group: it returns device_id.",
        "parameters": [],
        "body": """SELECT d.id AS device_id, d.name, d.serial, d.source, d.last_logon_at
FROM devices d
WHERE d.is_active AND d.serial IS NOT NULL AND d.serial <> ''
  AND d.serial IN (SELECT serial FROM devices WHERE is_active AND serial IS NOT NULL AND serial <> '' GROUP BY serial HAVING count(*) > 1)
ORDER BY d.serial, d.name""",
    },
    # Scripts and remote work.
    {
        "name": "Latest run of each script",
        "description": "When each script last ran, how it went and where.",
        "parameters": [],
        "body": """SELECT s.name AS script, r.status, coalesce(r.finished_at, r.created_at) AS at, r.target,
       u.display_name AS requested_by
FROM scripts s
JOIN LATERAL (
  SELECT * FROM script_runs x WHERE x.script_id = s.id ORDER BY x.created_at DESC, x.id DESC LIMIT 1
) r ON true
LEFT JOIN users u ON u.id = r.requested_by_id
ORDER BY at DESC""",
    },
    {
        "name": "Remote sessions per technician",
        "description": "Remote sessions started from tickets in the period, with their total length where they were ended.",
        "parameters": [],
        "body": """SELECT u.display_name AS technician, count(*) AS sessions, count(DISTINCT r.ticket_id) AS tickets,
       round(sum(extract(epoch FROM r.ended_at - r.started_at)) / 3600, 1) AS hours_recorded
FROM remote_sessions r
JOIN users u ON u.id = r.started_by_id
WHERE r.started_at >= :start AND r.started_at < :end
GROUP BY u.display_name
ORDER BY sessions DESC""",
    },
]
