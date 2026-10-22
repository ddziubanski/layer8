"""The appliance's recovery console: the privileged helper's checks and the files it writes, and the CLI commands it uses."""

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

from app import auth, cli
from app.models import UserRole, UserSession
from tests.conftest import make_user

HELPER = Path(__file__).resolve().parents[2] / "install" / "files" / "layer8-recovery"


@pytest.fixture
def helper(tmp_path, monkeypatch):
    monkeypatch.setenv("LAYER8_RECOVERY_ROOT", str(tmp_path))
    loader = importlib.machinery.SourceFileLoader("layer8_recovery", str(HELPER))
    spec = importlib.util.spec_from_loader("layer8_recovery", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    ran = []
    monkeypatch.setattr(mod, "run", lambda *cmd, **kw: ran.append(cmd) or 0)
    mod.ran = ran
    return mod


def test_values_are_checked(helper):
    for bad in ("10.1.10.0/24", "10.1.10.20", "10.1.10.20/32", "999.1.1.1/24", "x"):
        with pytest.raises(helper.Refused):
            helper.address_with_prefix(bad)
    a = helper.address_with_prefix("10.1.10.20/24")
    assert str(helper.gateway_for("10.1.10.1", a)) == "10.1.10.1"
    for bad in ("10.2.0.1", "10.1.10.20", "gateway"):
        with pytest.raises(helper.Refused):
            helper.gateway_for(bad, a)
    assert helper.nameservers("10.1.10.10, 10.1.10.11") == ["10.1.10.10", "10.1.10.11"]
    for bad in ("", "1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4", "dns.corp.example"):
        with pytest.raises(helper.Refused):
            helper.nameservers(bad)
    assert helper.domain("CORP.example.") == "corp.example"
    for bad in ("corp example", "-bad.lan", "a;rm -rf /", "$(reboot)"):
        with pytest.raises(helper.Refused):
            helper.domain(bad)
    for bad in ("lo", "eth0; reboot", "a" * 20):
        with pytest.raises(helper.Refused):
            helper.iface(bad)
    assert helper.target("10.1.10.1") == "10.1.10.1" and helper.target("dc1.corp.example") == "dc1.corp.example"


def test_static_address_writes_interfaces_and_arms_the_revert(helper, tmp_path):
    (tmp_path / "etc/network").mkdir(parents=True)
    (tmp_path / "etc/network/interfaces").write_text("auto ens18\niface ens18 inet dhcp\n")
    assert helper.main(["net-static", "ens18", "10.1.10.20/24", "10.1.10.1"]) == 0
    text = (tmp_path / "etc/network/interfaces").read_text()
    assert "iface ens18 inet static" in text and "address 10.1.10.20/24" in text and "gateway 10.1.10.1" in text
    assert (tmp_path / "var/lib/layer8-recovery/interfaces.prev").read_text() == "auto ens18\niface ens18 inet dhcp\n"
    assert ("systemd-run", "--unit", "layer8-net-revert", "--on-active", "120s", "--property", "KillMode=process", "/usr/local/sbin/layer8-recovery", "net-revert") in helper.ran
    # Down with the old settings (and its addresses flushed) before the new file goes in, then up.
    downs = [c for c in helper.ran if c[0] in ("ifdown", "ifup") or c[:2] == ("ip", "address")]
    assert [c[0] for c in downs[:3]] == ["ifdown", "ip", "ifup"] and downs[1] == ("ip", "address", "flush", "dev", "ens18", "scope", "global")
    assert ("ip", "link", "set", "dev", "ens18", "up") in helper.ran
    # Not confirmed: back to what it was.
    assert helper.main(["net-revert"]) == 0
    assert (tmp_path / "etc/network/interfaces").read_text() == "auto ens18\niface ens18 inet dhcp\n"
    # Confirmed: the backup goes, so there's nothing to revert.
    helper.main(["net-dhcp", "ens18"])
    helper.main(["net-confirm"])
    assert not (tmp_path / "var/lib/layer8-recovery/interfaces.prev").exists()


def test_dns_and_hostname_files(helper, tmp_path):
    (tmp_path / "etc/dhcp").mkdir(parents=True)
    (tmp_path / "etc/dhcp/dhclient.conf").write_text("request subnet-mask, routers;\n")
    (tmp_path / "etc/dhcpcd.conf").write_text("option rapid_commit\n")
    assert helper.main(["dns-set", "10.1.10.10 10.1.10.11", "corp.example"]) == 0
    assert (tmp_path / "etc/resolv.conf").read_text().splitlines()[1:] == ["search corp.example", "nameserver 10.1.10.10", "nameserver 10.1.10.11"]
    helper.main(["dns-set", "10.1.10.12"])
    dh = (tmp_path / "etc/dhcp/dhclient.conf").read_text()
    # The managed block is replaced, not added twice; the rest is kept.
    assert dh.count("BEGIN") == 1 and "supersede domain-name-servers 10.1.10.12;" in dh and dh.startswith("request subnet-mask")
    # dhcpcd (Debian 13) is told to leave resolv.conf alone, once.
    dc = (tmp_path / "etc/dhcpcd.conf").read_text()
    assert dc.startswith("option rapid_commit") and dc.count("nohook resolv.conf") == 1 and ("dhcpcd", "--rebind") in helper.ran
    hosts = helper.hosts_text("127.0.0.1\tlocalhost\n127.0.1.1\thelpdesk\n::1\tlocalhost\n", "helpdesk.corp.example")
    assert hosts.splitlines() == ["127.0.0.1\tlocalhost", "127.0.1.1\thelpdesk.corp.example helpdesk", "::1\tlocalhost"]


def test_unknown_commands_and_values_are_refused(helper, capsys):
    assert helper.main(["bash"]) == 2
    assert helper.main(["svc-restart", "sshd"]) == 1 and "Not one of" in capsys.readouterr().err
    assert helper.main(["ping"]) == 2
    assert helper.main(["ping", "1.1.1.1; reboot"]) == 1
    assert helper.ran == []


def test_cli_admins_and_sign_out_everyone(db, monkeypatch, capsys):
    monkeypatch.setattr(cli, "SessionLocal", lambda: db)
    glass = make_user(db, "breakglass", UserRole.admin, "Break glass")
    auth.set_password(db, glass, "A-long-admin-pass-1")
    sync = make_user(db, "mosei@corp.local", UserRole.admin, "M. Osei")
    sync.directory_id = "guid"
    db.commit()
    cli.list_admins()
    out = capsys.readouterr().out
    assert "breakglass\tBreak glass\tlocal, has a password\tactive" in out and "mosei@corp.local\tM. Osei\tdirectory" in out
    from fastapi import Response

    from app import sla

    auth.start_session(db, glass, Response(), sla.utcnow())
    auth.start_session(db, glass, Response(), sla.utcnow())
    db.commit()
    assert db.query(UserSession).count() == 2
    cli.sign_out_everyone()
    assert "Signed out 2 session(s)" in capsys.readouterr().out and db.query(UserSession).count() == 0
