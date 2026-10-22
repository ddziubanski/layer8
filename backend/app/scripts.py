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


# Examples an admin can add from the Scripts page (rewritten for this schema, after the KACE community's).
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
]
