# Deploying relays

The **relay kit** (Settings > Relays > **Download relay kit**) installs relays without hand work. It holds the relay (`helpdesk_runner.py`), a Linux installer, three Windows PowerShell scripts, `relay.json` (this service desk's address) and a README. **It holds no tokens:** add a relay in Settings > Relays for each host and give that host its own token. Nothing about how relays work changes: they still pull work over HTTPS with their own token, and approvals and run permissions apply as before.

## Check a relay without running anything

```
python3 helpdesk_runner.py --url https://helpdesk.corp.example --token-file /path/to/token --check
```

It checks in once and exits: `OK: relay 'RELAY01' checked in from relay01.corp.example`. That confirms the address, the certificate and the token, and shows the host and version in Settings > Relays. It never takes work. Both installers run it before they start the relay, and don't start it if it fails.

## On the appliance (or any Linux server)

```
sh install-relay.sh --url https://helpdesk.corp.example --token-file /root/relay.token            # preview: says what it would do
sh install-relay.sh --url https://helpdesk.corp.example --token-file /root/relay.token --apply    # install, or update in place
sh install-relay.sh --status       # service state, the last log lines and a check-in
sh install-relay.sh --uninstall    # stop and remove the service, its account and files
```

- **Its own account:** the relay runs as the system account `layer8-relay`. It has no login and isn't in the `helpdesk` group.
- **Cut off from the service desk:** systemd hides the program (`/opt/helpdesk`), settings (`/etc/helpdesk`), data, secrets and files (`/var/lib/helpdesk`) and the updater's state from it. The rest of the system is read-only to it, apart from its own work folder `/var/lib/layer8-relay`. It gets no extra capabilities and no privilege escalation. Hide anything else with `--deny /mnt/backups`.
- **The token:** it's read from a file, never typed on the command line, and stored in `/etc/layer8-relay/token`, owned by root and readable only by `layer8-relay`.
- **Certificate:** `--ca-file` points to your CA when the service desk's certificate isn't publicly trusted.
- **Updates:** running `--apply` again updates the program and the token.

This relay suits bash, Python and Arista EOS scripts. PowerShell over WinRM needs a Windows relay.

## Windows hosts, by GPO

Everything is previewed first. Nothing changes an existing GPO, enables WinRM, bypasses the execution policy or grants domain rights.

1. **Prepare:**
   - Python 3.10+ for all users on each relay host.
   - An AD group of the relay computers, for example *Layer 8 relay hosts*.
   - Two shares:
     - the **kit share**, readable by those computers, holding the unzipped kit;
     - the **token share**, where each file will be readable only by its own computer.
   - For scripts that reach other PCs over WinRM, an existing **gMSA** installed on those hosts. Without one, the relay runs as SYSTEM, for scripts that run on the host itself.
2. **Tokens:** in Layer 8, add one relay per host (Settings > Relays). Then, as a domain administrator, run once per host:
   `.\Set-Layer8RelayToken.ps1 -Computer RELAY01 -TokenPath \\fs01\layer8-relay-tokens$`
   It asks for the token, so it's never on the command line or in SYSVOL, and writes `RELAY01.token`, readable only by `CORP\RELAY01$` and Administrators. For a scripted rollout, `-TokenFile <file>` reads the token from a file instead; delete that file afterwards.
3. **The GPO:**
   `.\New-Layer8RelayGpo.ps1 -ComputerGroup 'Layer 8 relay hosts' -KitPath \\fs01\layer8-relay$ -TokenPath \\fs01\layer8-relay-tokens$ [-ServiceAccount 'CORP\gmsa-relay$']`
   It checks everything and shows the plan. Add `-Apply` to create the GPO:
   - It's a **new**, **unlinked** GPO; the script refuses if one with that name exists.
   - It applies only to the group; Authenticated Users can only read it.
   - It has one computer startup script, `Install-Layer8Relay.ps1`, given only the two share paths and the gMSA name.
4. **Activate it** yourself by linking the GPO to the OU holding the relay hosts. The script ends by printing the `New-GPLink` command for each OU the group's computers are in. The hosts install the relay at their next restart.

At startup, `Install-Layer8Relay.ps1`:
1. Copies the relay from the kit share when it changed.
2. Takes this computer's own token.
3. Locks `C:\ProgramData\Layer8Relay` to SYSTEM and Administrators (and the gMSA).
4. Checks in.
5. Only then registers and starts the **Layer 8 relay** scheduled task.

It logs to `C:\ProgramData\Layer8Relay\install.log`. The relay itself logs to `state\relay.log` in the same folder, including why it stopped (for example a revoked token). `state` is the only place the relay's account can write. PowerShell startup scripts follow the computers' execution policy: sign `Install-Layer8Relay.ps1`, or allow it as your policy says.

## Health, revoking and removing

- **Health:** Settings > Relays shows each relay's last check-in, host and version. On a host, `--check` checks in on demand. Logs: `journalctl -u layer8-relay` on Linux; `install.log` and `state\relay.log` on Windows.
- **Revoke:** Settings > Relays > **New token** (or delete the relay). The old token stops working at once. Delete the host's token file on the token share as well.
- **Remove:**
  - Linux: `sh install-relay.sh --uninstall`.
  - Windows: `.\Install-Layer8Relay.ps1 -Uninstall` on the host.
  - The GPO: unlink and delete it (`Remove-GPO`).
- **If something fails:** an installer that can't check in leaves the relay stopped and says why. A host whose token isn't on the share yet installs nothing until it is.

## Tested

The Windows path was tested end to end in the Windows Server 2025 lab (`docs/ad-lab.md`), on a domain with default policies:
- the token file's ACL;
- GPO preview, creation and security filtering, including a workstation in a linked OU that was correctly denied;
- the startup install as a gMSA;
- check-in;
- token revocation: one refused claim, then the relay stops and doesn't retry;
- uninstall.

The PowerShell startup script ran under the default RemoteSigned policy without being signed, because it runs from SYSVOL. If your domain requires signed scripts, sign `Install-Layer8Relay.ps1`.

Not yet tested in a real deployment: the Linux installer's systemd containment, and running jobs that reach other computers through a gMSA relay.
