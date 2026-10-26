#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Install an isolated appliance relay. Preview by default; --apply changes this host."""
import argparse
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ACCOUNT = 'layer8-relay'
LIB = Path('/usr/local/lib/layer8-relay')
CONFIG = Path('/etc/layer8-relay')
STATE = Path('/var/lib/layer8-relay')
UNIT = Path('/etc/systemd/system/layer8-relay.service')
SERVICE = '''[Unit]
Description=Layer 8 isolated appliance relay
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5
[Service]
User=layer8-relay
Group=layer8-relay
ExecStart=/usr/bin/python3 -I /usr/local/lib/layer8-relay/launch.py
WorkingDirectory=/var/lib/layer8-relay
Restart=on-failure
RestartSec=10
TimeoutStopSec=30
KillMode=control-group
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
ReadWritePaths=/var/lib/layer8-relay
InaccessiblePaths=-/etc/helpdesk -/var/lib/helpdesk -/var/lib/layer8-updater -/opt/helpdesk -/run/postgresql -/var/lib/postgresql -/etc/postgresql
[Install]
WantedBy=multi-user.target
'''
LAUNCH = '''# SPDX-License-Identifier: AGPL-3.0-or-later
import json, os, sys
from pathlib import Path
config = json.loads(Path('/etc/layer8-relay/config.json').read_text())
args = [sys.executable, '-I', '/usr/local/lib/layer8-relay/helpdesk_runner.py', '--url', config['url'], '--token-file', '/etc/layer8-relay/token']
if config['ca_file']: args += ['--ca-file', '/etc/layer8-relay/ca.pem']
os.execv(sys.executable, args)
'''


def url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or re.search(r'[\s\\]', value)):
        raise ValueError('Use the HTTPS root address of Layer 8; no credentials, query or path.')
    return value.rstrip('/')


def secure_parents(path):
    for parent in reversed(path.parents):
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Relay installation parents must be root-owned directories without writable aliases.')
    if path.is_symlink():
        raise ValueError('Relay installation targets cannot be symbolic links.')


def write(path, body, mode, uid=0, gid=0):
    secure_parents(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.relay-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(body)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fchown(output.fileno(), uid, gid)
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--ca-file', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    address = url(args.url)
    source = Path(__file__).resolve().parent / 'helpdesk_runner.py'
    # In a release checkout the runner lives separately; downloaded appliance kits are flat.
    if not source.is_file(): source = Path(__file__).resolve().parents[2] / 'runner/helpdesk_runner.py'
    if not source.is_file() or source.is_symlink(): raise ValueError('Matching relay program is missing.')
    info = args.token_file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077: raise ValueError('The token source must be a private regular file (mode 0600).')
    token = args.token_file.read_text().strip()
    if not re.fullmatch(r'hdr_[A-Za-z0-9_-]{32,128}', token): raise ValueError('Invalid relay token file.')
    if args.ca_file: ssl.create_default_context(cafile=str(args.ca_file))
    print('Plan: separate layer8-relay account; protected /etc/layer8-relay; root-owned program; restricted systemd service; outbound HTTPS to ' + address)
    if not args.apply: return
    if sys.platform != 'linux' or os.geteuid() != 0: raise ValueError('Apply only as root on the Linux appliance.')
    import pwd
    run = lambda command: subprocess.run(command, check=True, capture_output=True, timeout=60)
    for path in (LIB, CONFIG, STATE, UNIT): secure_parents(path)
    if UNIT.exists() and 'Layer 8 isolated appliance relay' not in UNIT.read_text(): raise ValueError('A different relay service already exists; inspect it locally.')
    if any(path.exists() for path in (LIB, CONFIG, STATE)) and not UNIT.exists(): raise ValueError('Relay files exist without this managed service; inspect them locally first.')
    try: account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        run(['/usr/sbin/useradd', '--system', '--user-group', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', ACCOUNT])
        account = pwd.getpwnam(ACCOUNT)
    if account.pw_uid == 0 or account.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin', '/bin/false') or set(os.getgrouplist(ACCOUNT, account.pw_gid)) != {account.pw_gid}:
        raise ValueError('Relay account must be non-root, non-interactive and have no supplementary groups.')
    # Never replace files beneath an active relay/job. Explicit maintenance is required.
    if subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', 'layer8-relay.service']).returncode == 0:
        raise ValueError('Stop layer8-relay.service during a maintenance window before changing its deployment.')
    for directory in (LIB, CONFIG):
        directory.mkdir(mode=0o750, exist_ok=True)
        os.chown(directory, 0, account.pw_gid);directory.chmod(0o750)
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chown(STATE, account.pw_uid, account.pw_gid);STATE.chmod(0o700)
    write(LIB / 'helpdesk_runner.py', source.read_bytes(), 0o640, gid=account.pw_gid)
    write(LIB / 'launch.py', LAUNCH.encode(), 0o640, gid=account.pw_gid)
    write(CONFIG / 'token', token.encode(), 0o400, uid=account.pw_uid, gid=account.pw_gid)
    config = {'url': address, 'ca_file': bool(args.ca_file)}
    if args.ca_file: write(CONFIG / 'ca.pem', args.ca_file.read_bytes(), 0o640, gid=account.pw_gid)
    write(CONFIG / 'config.json', json.dumps(config).encode(), 0o640, gid=account.pw_gid)
    command = ['/usr/sbin/runuser', '-u', ACCOUNT, '--', '/usr/bin/python3', '-I', str(LIB / 'helpdesk_runner.py'), '--url', address, '--token-file', str(CONFIG / 'token'), '--check']
    if args.ca_file:command += ['--ca-file', str(CONFIG / 'ca.pem')]
    run(command)  # No queued job is claimed during this deployment check.
    write(UNIT, SERVICE.encode(), 0o644)
    run(['/usr/bin/systemctl', 'daemon-reload'])
    run(['/usr/bin/systemctl', 'enable', '--now', 'layer8-relay.service'])
    run(['/usr/bin/systemctl', 'is-active', '--quiet', 'layer8-relay.service'])
    print('Relay started. Verify Settings > Relays; inspect logs with journalctl -u layer8-relay.service.')


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('Relay deployment failed: ' + (str(exc) if isinstance(exc, ValueError) else 'inspect local permissions, prerequisites and check-in configuration'), file=sys.stderr)
        raise SystemExit(1)
