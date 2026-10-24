# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every example SQL query runs as the restricted rules login, open-ended and over a period."""
import pytest
from sqlalchemy import create_engine, text

from app import scripts
from tests.conftest import RULES_URL

PERIODS = [("-infinity", "infinity"), ("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")]


@pytest.mark.parametrize("example", scripts.EXAMPLES, ids=[e["name"] for e in scripts.EXAMPLES])
def test_example_query_runs_read_only_as_the_rules_login(engine, example):
    scripts.check_definition(scripts.ScriptKind.query, scripts.ScriptLanguage.sql, example["parameters"])
    engine = create_engine(RULES_URL)
    try:
        for start, end in PERIODS:
            params = {"start": start, "end": end, **{p["name"]: p["default"] for p in example["parameters"]}}
            with engine.connect() as conn:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text(example["body"]), {k: v for k, v in params.items() if f":{k}" in example["body"]}).fetchall()
    finally:
        engine.dispose()


def test_example_names_are_unique():
    names = [e["name"] for e in scripts.EXAMPLES]
    assert len(names) == len(set(names))
