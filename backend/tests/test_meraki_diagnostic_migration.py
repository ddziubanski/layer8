# SPDX-License-Identifier: AGPL-3.0-or-later
"""Upgrade/downgrade preserves authoritative and discovery data; cache schema matches the model."""
import uuid
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine, make_url, text
from app.models import IpAddress, MerakiClient, MerakiDiagnostic
from app import sla
from tests.conftest import TEST_URL, alembic_config
from tests.test_meraki_diagnostics import observation


def test_diagnostics_migration_preserves_discovery_and_ipam(engine):
    name = "helpdesk_meraki_upgrade_" + uuid.uuid4().hex
    server = create_engine(make_url(TEST_URL).set(database="postgres"), isolation_level="AUTOCOMMIT")
    upgraded = None
    try:
        with server.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        url = make_url(TEST_URL).set(database=name)
        cfg = alembic_config(); cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
        command.upgrade(cfg, "a86d20f49c71")
        upgraded = create_engine(url)
        with upgraded.begin() as conn:
            conn.execute(MerakiClient.__table__.insert().values(**observation()))
            conn.execute(IpAddress.__table__.insert().values(address="10.20.1.43", hostname="ws-0142.corp.example"))
        command.upgrade(cfg, "head")
        with upgraded.begin() as conn:
            metadata = MetaData(); MerakiDiagnostic.__table__.to_metadata(metadata)
            context = MigrationContext.configure(conn, opts={"include_object": lambda obj, name, type_, reflected, compare: name == "meraki_diagnostics" if type_ == "table" else True})
            assert compare_metadata(context, metadata) == []
            conn.execute(MerakiDiagnostic.__table__.insert().values(network_id="N_123", mac="00:11:22:33:44:55", identity_hash="a" * 64, attempted_at=sla.utcnow(), data={}))
        command.downgrade(cfg, "a86d20f49c71")
        with upgraded.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM meraki_clients")) == 1
            assert conn.scalar(text("SELECT count(*) FROM ip_addresses")) == 1
        command.upgrade(cfg, "head")
        with upgraded.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM meraki_diagnostics")) == 0
    finally:
        if upgraded is not None: upgraded.dispose()
        with server.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        server.dispose()
