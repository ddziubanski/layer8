"""Screen layouts: which panels a page shows, where and in which order: across the top, in the main
or side column, across the bottom, or hidden.

An admin sets the default for everyone (app_settings "layouts"); each technician may keep their
own (users.layouts), or go back to the default. Panels the app adds later appear in their usual
place, so a saved layout never hides something new.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AppSetting, User

# page -> column -> panels in their usual order. Required panels (a ticket's conversation, a PO's
# order form) can be moved but not hidden.
PANELS: dict[str, dict[str, list[str]]] = {
    "ticket": {
        "main": ["conversation", "attachments", "children", "checklists", "expenses", "ad_account", "scripts", "history"],
        "side": ["assignment", "requester", "computer", "devices", "systems", "changes", "knowledge", "status", "actions"],
    },
    # A purchase order: the order itself (its fields, lines and links), and what happens to it.
    "po": {
        "main": ["order"],
        "side": ["receive", "heads_up", "files", "history"],
    },
}
REQUIRED = {"ticket": {"conversation"}, "po": {"order"}}
# page -> field group -> fields in their usual order. Within a group fields can be reordered, made
# full width or hidden (a hidden field keeps its value; it just isn't shown).
FIELDS: dict[str, dict[str, list[str]]] = {
    "po": {
        "order_top": ["vendor", "vendor_reference", "department", "requested_by", "approver", "budget_code", "order_date", "expected_date", "currency"],
        "order_bottom": ["ship_to", "terms", "notes"],
    },
}
# Usually full width.
WIDE = {"po": {"ship_to", "terms", "notes"}}
KEY = "layouts"
# Full width above the two columns, the columns, full width below, and hidden.
ZONES = ("top", "main", "side", "bottom", "hidden")


class LayoutError(ValueError):
    pass


def usual(page: str) -> dict[str, Any]:
    cols = PANELS[page]
    return {"top": [], "main": list(cols["main"]), "side": list(cols["side"]), "bottom": [], "hidden": [], "fields": _fields(page, None)}


def _fields(page: str, given: dict | None) -> dict[str, dict[str, list[str]]]:
    """Each field group: every known field once in order (new ones at the end), and which are hidden or wide."""
    out = {}
    for group, known in FIELDS.get(page, {}).items():
        g = (given or {}).get(group)
        if not isinstance(g, dict):
            out[group] = {"order": list(known), "hidden": [], "wide": [f for f in known if f in WIDE.get(page, set())]}
            continue
        order = list(dict.fromkeys(f for f in g.get("order") or [] if f in known))
        order += [f for f in known if f not in order]
        out[group] = {"order": order, "hidden": [f for f in order if f in set(g.get("hidden") or [])], "wide": [f for f in order if f in set(g.get("wide") or [])]}
    return out


def normalise(page: str, layout: dict[str, Any] | None) -> dict[str, Any]:
    """Every known panel exactly once; unknown names dropped; new panels in their usual column."""
    if page not in PANELS:
        raise LayoutError(f"No page called {page!r}")
    if not layout:
        return usual(page)
    known = {p: col for col, panels in PANELS[page].items() for p in panels}
    seen: set[str] = set()
    out: dict[str, Any] = {z: [] for z in ZONES}
    for col in ZONES:
        for p in layout.get(col) or []:
            if p in known and p not in seen:
                seen.add(p)
                out["main" if col == "hidden" and p in REQUIRED.get(page, set()) else col].append(p)
    for p, col in known.items():
        if p not in seen:
            out[col].append(p)
    out["fields"] = _fields(page, layout.get("fields"))
    return out


def _stored(session: Session) -> dict:
    row = session.get(AppSetting, KEY)
    return dict(row.value) if row else {}


def default(session: Session, page: str) -> dict[str, Any]:
    return normalise(page, _stored(session).get(page))


def mine(user: User, page: str) -> dict[str, Any] | None:
    own = (user.layouts or {}).get(page)
    return normalise(page, own) if own else None


def effective(session: Session, user: User, page: str) -> dict[str, Any]:
    return mine(user, page) or default(session, page)


def set_default(session: Session, page: str, layout: dict | None, by: User | None) -> None:
    """None: back to the app's usual layout."""
    value = _stored(session)
    if layout is None:
        value.pop(page, None)
    else:
        value[page] = normalise(page, layout)
    row = session.get(AppSetting, KEY)
    if row is None:
        row = AppSetting(key=KEY, value={})
        session.add(row)
    row.value, row.updated_by_id = value, by.id if by else None


def set_mine(user: User, page: str, layout: dict | None) -> None:
    value = dict(user.layouts or {})
    if layout is None:
        value.pop(page, None)
    else:
        value[page] = normalise(page, layout)
    user.layouts = value
