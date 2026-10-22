"""SQL ticket rules, KACE-style, on their own restricted database login.

The login (HELPDESK_RULES_DATABASE_URL) can read ticket data, not credentials or
sessions, and update a few ticket columns. The rule's SQL is always wrapped so it
is exactly one statement:

- select SQL becomes ``SELECT DISTINCT id FROM (<select>) AS rule_select``, so it
  must return a column named ``id`` (the ticket id);
- update SQL becomes ``WITH rule_update AS (<update> RETURNING 1) SELECT count(*)``,
  so it must be a plain UPDATE of ``tickets`` without its own RETURNING.

Both may use ``:ticket_ids`` (the matched ticket ids, an int array), e.g.
``UPDATE tickets SET priority = 'high' WHERE id = ANY(:ticket_ids)``.

An update never commits. It runs in a transaction that is always rolled back;
we read back which ticket rows it changed (they carry this transaction's id in
xmin) and their new values, and the app applies those changes itself.
"""

import re
from functools import lru_cache

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.types import Integer

from app.config import settings

# Must match the grants in the ticket rules migration.
UPDATABLE = ["subject", "description", "category", "status", "priority", "queue_id", "assignee_id", "requester_id"]


class SqlRuleError(ValueError):
    pass


@lru_cache(maxsize=4)
def _engine(url: str, timeout_ms: int) -> Engine:
    return create_engine(url, pool_pre_ping=True, connect_args={"options": f"-c statement_timeout={timeout_ms}"})


def engine() -> Engine:
    if not settings.rules_database_url:
        raise SqlRuleError("SQL rules need HELPDESK_RULES_DATABASE_URL (the restricted rules login)")
    return _engine(settings.rules_database_url, settings.rules_statement_timeout_ms)


def _clean(sql: str, first_words: tuple[str, ...]) -> str:
    body = sql.strip().rstrip(";").strip()
    # Ignore leading comments when checking what kind of statement it is.
    head = re.sub(r"^(\s*(--[^\n]*\n|/\*.*?\*/))*", "", body, flags=re.S).lstrip().upper()
    if not head.startswith(first_words):
        raise SqlRuleError(f"Must start with {' or '.join(first_words)}")
    return body


def _params(ticket_ids: list[int], sql: str):
    stmt = text(sql)
    if ":ticket_ids" in sql:
        stmt = stmt.bindparams(bindparam("ticket_ids", type_=ARRAY(Integer)))
    return stmt


def _db_error(exc: DBAPIError) -> SqlRuleError:
    message = str(getattr(exc.orig, "args", [exc])[0]).strip().splitlines()[0]
    return SqlRuleError(f"SQL error: {message}")


def select_ids(select_sql: str, ticket_ids: list[int]) -> list[int]:
    body = _clean(select_sql, ("SELECT", "WITH"))
    wrapped = f"SELECT DISTINCT rule_select.id FROM ({body}) AS rule_select ORDER BY 1"
    with engine().connect() as conn:
        try:
            rows = conn.execute(_params(ticket_ids, wrapped), {"ticket_ids": ticket_ids} if ":ticket_ids" in wrapped else {})
            return [int(r[0]) for r in rows if r[0] is not None]
        except DBAPIError as exc:
            raise _db_error(exc) from None
        finally:
            conn.rollback()


def propose_update(update_sql: str, ticket_ids: list[int], cap: int) -> dict[int, dict]:
    """What the update would set, per changed ticket id. Nothing is committed."""
    body = _clean(update_sql, ("UPDATE",))
    if re.search(r"\bRETURNING\b", body, re.I):
        raise SqlRuleError("Leave out RETURNING; the app reads the changes itself")
    wrapped = f"WITH rule_update AS ({body} RETURNING 1) SELECT count(*) FROM rule_update"
    cols = ", ".join(["id", *UPDATABLE])
    with engine().connect() as conn:
        try:
            count = conn.execute(_params(ticket_ids, wrapped), {"ticket_ids": ticket_ids} if ":ticket_ids" in wrapped else {}).scalar()
            if count > cap:
                raise SqlRuleError(f"The update would change {count} tickets, more than this rule's limit of {cap}; nothing was changed")
            rows = conn.execute(
                text(
                    f"SELECT {cols} FROM tickets "
                    # Rows written in this transaction carry its id in xmin.
                    "WHERE xmin::text::bigint = pg_current_xact_id()::text::bigint % 4294967296"
                )
            ).mappings()
            return {r["id"]: {c: r[c] for c in UPDATABLE} for r in rows}
        except DBAPIError as exc:
            raise _db_error(exc) from None
        finally:
            # Always: the app applies the changes, not this connection.
            conn.rollback()
