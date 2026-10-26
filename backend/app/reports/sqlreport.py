# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQL reports: an admin's own SELECT, run on the restricted rules login (like SQL ticket rules).

The query is wrapped as ``SELECT * FROM (<query>) AS report LIMIT n``, so it must be one
SELECT (or WITH ... SELECT), and runs in a read-only transaction that is always rolled
back, under the SQL rule time limit. ``:start`` and ``:end`` are the report's period as
timestamps (start inclusive, end exclusive; open-ended when no period is chosen), e.g.
``WHERE created_at >= :start AND created_at < :end``, so saved and scheduled SQL reports
follow the period like any other report.
"""

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.reports.engine import ReportColumn, ReportError, ReportResult
from app.rules import sqlmode

PARAMS = re.compile(r"(?<!:):(start|end)\b")


def _bound(d: date | None, default: str, extra_days: int = 0) -> str:
    if d is None:
        return default
    return datetime.combine(d + timedelta(days=extra_days), time.min).astimezone().isoformat()


def _cell(v: Any) -> Any:
    if v is None or isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, datetime):
        return v.astimezone().strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def run(sql: str, date_from: date | None, date_to: date | None, limit: int, extra: dict[str, Any] | None = None) -> ReportResult:
    """extra: more named parameters (a library query's), bound where the SQL uses them."""
    try:
        body = sqlmode._clean(sql, ("SELECT", "WITH"))
        conn_engine = sqlmode.engine()
    except sqlmode.SqlRuleError as exc:
        raise ReportError(str(exc).replace("SQL rules need", "SQL reports need")) from None
    # Untyped parameters: cast them, so they compare with timestamps in any expression.
    wrapped = f"SELECT * FROM (\n{PARAMS.sub(lambda m: f'CAST(:{m[1]} AS timestamptz)', body)}\n) AS report WHERE {sqlmode.GUARD} LIMIT {limit + 1}"
    params = {**(extra or {}), **sqlmode.GUARD_PARAMS, "start": _bound(date_from, "-infinity"), "end": _bound(date_to, "infinity", 1)}
    with conn_engine.connect() as conn:
        try:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            result = conn.execute(text(wrapped), {k: v for k, v in params.items() if re.search(rf"(?<!:):{k}\b", wrapped)})
            names = list(result.keys())
            raw = [[_cell(v) for v in row] for row in result]
        except DBAPIError as exc:
            raise ReportError(str(sqlmode._db_error(exc))) from None
        finally:
            conn.rollback()
    truncated = len(raw) > limit
    raw = raw[:limit]
    columns = []
    for i, name in enumerate(names):
        values = [r[i] for r in raw if r[i] is not None]
        # IDs are numbers but not amounts: no bar, no total.
        is_id = name == "id" or name.endswith("_id")
        numeric = not is_id and bool(values) and all(isinstance(v, (int, float)) for v in values)
        columns.append(ReportColumn(key=f"c{i}", label=name, kind="number" if numeric else "text"))
    return ReportResult(columns=columns, rows=raw, totals={}, truncated=truncated)
