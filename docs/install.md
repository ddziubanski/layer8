# Installing Layer 8

There are three ways to install. All of them end the same way: a Debian 13 server running the service desk behind nginx on HTTPS (443, with 80 only redirecting), PostgreSQL on the same machine, and two systemd services, `helpdesk-api` and `helpdesk-worker`.

| Way | For | Needs |
|---|---|---|
| **Appliance ISO** (recommended) | A new VM or server that runs only the service desk | A VM (2 vCPU, 4 GB RAM, 40 GB disk) or a PC. No network needed to install. |
| **Installer on a Debian 13 server** | A server you already run | Debian 13, root, internet access (or the ISO's offline bundle) |
| **Offline demo** | Trying it out | Either of the above; one choice in the wizard, or `--demo` |

Every option the setup asks for can be changed later in the app (Settings), except the host name and the HTTPS certificate, which are server settings (see [Changing the host name or certificate](#changing-the-host-name-or-certificate)).

## 1. Appliance ISO

The ISO is Debian 13 plus the service desk and everything it needs, so the OS and every dependency are the versions it was tested with.

1. **Create the VM**: 2 vCPU, 4 GB RAM, 40 GB disk, one network card. On Hyper-V use a Generation 2 VM; disable Secure Boot, or set its template to "Microsoft UEFI Certificate Authority". BIOS and UEFI both work.
2. **Boot the ISO.** It has one entry, *Install Layer 8 (erases the disk)*, which starts after 5 seconds. It installs a minimal Debian server (no desktop) and Layer 8 without asking anything, and **erases the first disk**. There are no other install options: for a hand-made Debian, use Debian's own ISO and `install/install.sh`.
3. **First boot**: the setup wizard opens on the console. It asks for the following, and anything marked *optional* can be skipped and done later in Settings:
   - **Install type**: real install, offline demo, or an existing answers file.
   - **Time zone**: backup times, scheduled reports and business hours follow it.
   - **Address**: the service desk's DNS name, e.g. `helpdesk.corp.example`. Create the DNS record pointing to the VM's address. The console shows the address, or use `ip addr`.
   - **OS administrator**: a password for `sdadmin`. It's set at once, so you can copy files in with `scp` as `sdadmin` from here on. Until then the account has no password and can't be logged into.
   - **HTTPS**: a certificate and key from your CA (the wizard waits while you `scp` them in as `sdadmin`), or a self-signed certificate for now.
   - **First admin**: a local account that always works, even when AD is down. Keep it as the break-glass account.
   - **Ticket numbers**: the first number and the label, e.g. `HD-{n}` or `INC{n:06}`.
   - **Microsoft Entra / Exchange Online** (optional): tenant ID, application (client) ID, client secret, the service desk mailbox and the email domains to accept. See [email-setup.md](email-setup.md) for the app registration. The wizard tests the connection when it finishes.
   - **Active Directory** (optional): domain controllers (LDAPS), a read-only service account, the search base and the CA certificate. The connection is tested too.
   - **Windows sign-in for the portal** (optional): the keytab for `HTTP/<name>`. See [windows-sign-in.md](windows-sign-in.md).
   - **Backups** (optional): an SMB or NFS share (mounted at `/mnt/helpdesk-backup`), the daily time and the backup passphrase. Keep a copy of the passphrase away from the server.
4. Open `https://<name>/` and sign in as the admin.

**The OS accounts.** `layer8` is the recovery console: log in on the screen or over SSH for a menu of recovery tasks (network, DNS, break-glass admin passwords, services, certificate, backups, power) with no shell. The wizard asks for its password; without one the account stays locked. See [recovery-console.md](recovery-console.md). `sdadmin` is the OS administrator for anything the menu doesn't cover. There's no default password: the wizard asks for one (or `install.os_password` in an answers file), and until then the account is locked. Root login is off; use `sudo`.

**Unattended ISO.** `iso/build.sh --answers answers.json` builds an ISO that installs with those answers and no wizard, for repeat installs or a lab. The answers file holds passwords, so keep that ISO private. The copy on the installed machine is deleted once setup has used it.

## 2. Installer on an existing Debian 13 server

From a release bundle or a checkout of this repository, with the web UI built (`cd frontend && npm ci && npm run build`):

```sh
sudo ./install/wizard.sh                      # asks everything, then installs
sudo ./install/install.sh --answers my.json   # or: straight from an answers file
```

The installer:

- **Packages:** installs `postgresql`, `nginx`, `python3-venv`, the Kerberos libraries, `cifs-utils` and `nfs-common`. With no `debs/` folder these come from the internet, and so do the Python packages; `gssapi` is compiled, so `gcc` and `libkrb5-dev` are added too. An offline bundle carries pre-built wheels instead.
- **Account and folders:** creates the `helpdesk` service account and:
  - `/opt/helpdesk`: the app, its Python environment and the web UI, owned by root.
  - `/var/lib/helpdesk`: attachments in `storage/`, and `secrets/` (0700).
  - `/etc/helpdesk`: `helpdesk.env` with the generated database passwords, and `tls/`.
- **Database:** creates the database, the `helpdesk` login and the restricted `helpdesk_rules` login (used by SQL rules, SQL reports and script-library queries), then runs the migrations.
- **Settings:** applies the answers with `python -m app.cli setup`: settings, the first admin and, for a demo, sample data.
- **Web server and services:** writes the nginx site, the systemd units and a sudoers rule that lets the app restart its worker (Settings > Status).

Running it again **updates the app** and keeps the database, settings, files, secrets and passwords. It also runs any new migrations. Take a backup first (Settings > Backup > Back up now).

Other distributions aren't tested. The installer warns and carries on, but expect to adjust package names.

## 3. The offline demo

Choose *Offline demo* in the wizard, or run `sudo ./install/install.sh --demo`, or build `iso/build.sh --demo`. The demo includes:

- Sample departments, queues and routing rules.
- Seven people with local accounts, including an admin and two technicians.
- Six devices, eight tickets and the example queries in the script library.
- A "This is a demo" notice.

Every demo account's password is `Demo-helpdesk-2026`, and the sign-in page says so. Nothing connects to email or AD.

To go from a demo to a real install, reinstall: either the ISO, or drop the database and re-run the installer with real answers.

## The answers file

The wizard writes it, and you can write one yourself for unattended installs. Settings sections are the same as in `python -m app.cli settings import` (see Settings for every field). The `install` section is for the installer only:

```json
{
  "install": {
    "hostname": "helpdesk.corp.example",
    "tls": {"cert": "/root/helpdesk.pem", "key": "/root/helpdesk.key"},
    "admin": {"email": "it@example.com", "name": "IT Admin", "password": "use-a-long-one"},
    "os_password": "for sdadmin, the OS administrator",
    "recovery": {"password": "for layer8, the recovery console"},
    "backup": {"kind": "smb", "share": "//fs1/backups/helpdesk", "user": "CORP\\svc-backup", "password": "..."},
    "demo": false
  },
  "general": {"portal_url": "https://helpdesk.corp.example", "ticket_first_number": 10000, "ticket_label_format": "HD-{n}", "action1_enabled": true},
  "action1": {"action1_region": "NorthAmerica", "action1_client_id": "<client ID>", "action1_client_secret": {"file": "action1-secret.txt"}},
  "email": {
    "graph_tenant_id": "<tenant ID>", "graph_client_id": "<client ID>", "graph_client_secret": {"file": "graph-secret.txt"},
    "mailbox": "helpdesk@example.com", "mail_allowed_domains": ["example.com"]
  },
  "directory": {
    "ldap_url": "ldaps://dc1.corp.example,ldaps://dc2.corp.example",
    "ldap_bind_dn": "CN=svc-helpdesk,OU=Service Accounts,DC=corp,DC=example",
    "ldap_bind_password": {"file": "ldap-password.txt"},
    "ldap_base_dn": "DC=corp,DC=example",
    "ldap_ca_file": {"file": "corp-root-ca.pem"},
    "ad_group_lists": ["Distribution lists: OU=Distribution Lists,DC=corp,DC=example"]
  },
  "windows_sign_in": {"keytab_file": "http.keytab", "enabled": true, "silent": false},
  "backup": {"backup_dir": "/mnt/helpdesk-backup", "backup_time": "02:00", "backup_passphrase": {"file": "backup-passphrase.txt"}}
}
```

- **Files instead of values:** any value can be `{"file": "path"}` (the file's text). Relative paths are from the answers file's folder. This keeps passwords out of the JSON.
- **Plain text:** the file holds passwords, so keep it root-only and delete it after the install. The wizard does this for its own copy.
- **`tls: "self-signed"`:** makes a certificate for the host name.

## After installing

- **Status:** Settings > Status shows each part (database, worker, email, directory, disk space).
- **Logs:**
  - `journalctl -u helpdesk-api -u helpdesk-worker`
  - nginx: `/var/log/nginx/`
  - the app's own log: Settings > Log
- **Connection checks** on the server:
  ```sh
  sudo -u helpdesk sh -c 'set -a; . /etc/helpdesk/helpdesk.env; cd /opt/helpdesk/app && /opt/helpdesk/venv/bin/python -m app.cli check email'
  ```
  Use `check directory` for the AD connection.
- **Backups and moving servers:** see [the backend README](../backend/README.md#backups). Restore with `python -m app.cli backup restore` on a fresh install.

## Changing the host name or certificate

Re-run the installer with the new `install.hostname` (and `tls`). Self-signed certificates are remade for a new name. To swap in a certificate from your CA later, replace `/etc/helpdesk/tls/cert.pem` and `key.pem` (key mode 0600), then run `sudo systemctl reload nginx`. Update `portal_url` in Settings > General to match the new address.

## Building the ISO

On a Linux machine with docker, npm and internet access:

```sh
iso/build.sh                 # wizard on first boot
iso/build.sh --demo          # unattended offline demo
iso/build.sh --answers a.json
```

It downloads Debian's current 13.x netinst ISO (checking its SHA-256), builds the web UI, and builds the offline bundle in a Debian 13 container. The bundle holds Python wheels for Debian's Python (with `gssapi` compiled) and every Debian package the installer adds. It then repacks the ISO with the bundle and the automated install entry, keeping Debian's BIOS and UEFI boot. Output: `iso/build/layer8-<version>.iso`.
