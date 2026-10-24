# Setting up a relay

Relays were called *runners* before version 1.2. Only the name on screen changed: the program (`runner/helpdesk_runner.py`), its tokens, settings names and API paths are the same, so relays already installed keep working.

A relay runs action scripts from the script library. It polls the service desk over HTTPS 443 with its own token, so it needs no inbound ports. The relay is one file, `runner/helpdesk_runner.py` (Python 3.10+, standard library only).

## 1. Add it in the app

Settings > Relays > **Add a relay**: a name and the languages it takes. Copy the token it shows; it isn't shown again (**New token** replaces it).

## 2. A Windows relay for PowerShell (WinRM)

On a domain-joined server, e.g. `RUNNER01`:

1. Install Python 3.10+ for all users. PowerShell 7 (`pwsh`) is used if present, else Windows PowerShell.
2. Create a service account, e.g. `CORP\svc-hd-runner` (a gMSA is better). It needs WinRM access to the target PCs: membership of their local *Remote Management Users* (or Administrators, for scripts that change services), typically by GPO. Grant only what your scripts need.
3. Make `C:\ProgramData\HelpdeskRunner\` and copy `helpdesk_runner.py` there. Save the token in `C:\ProgramData\HelpdeskRunner\token` and limit that file to the service account and Administrators.
4. Run it as a service as that account, e.g. with NSSM or Task Scheduler ("at startup", "run whether user is logged on or not"):

   ```
   python C:\ProgramData\HelpdeskRunner\helpdesk_runner.py --url https://helpdesk.corp.example --token-file C:\ProgramData\HelpdeskRunner\token
   ```

   Add `--ca-file` if the service desk's certificate comes from a CA the machine doesn't trust.

A PowerShell script with a target device runs there through `Invoke-Command -ComputerName <device's DNS name>`. Its parameters arrive as variables (`$days`). Without a target, it runs on the relay itself; use this for tasks like AD account changes with the ActiveDirectory module.

## 3. Linux relay (bash, Python, Arista EOS)

```
python3 helpdesk_runner.py --url https://helpdesk.corp.example --token-file /etc/helpdesk-runner/token \
  --eos-user hd-runner --eos-password-file /etc/helpdesk-runner/eos-password
```

Run it as a systemd service under its own user. bash and Python scripts get their parameters as `HD_<NAME>` environment variables, plus `HD_TARGET` and `HD_TARGET_OS`. EOS scripts are CLI commands, one per line, with `$name` filled in. They're sent to `https://<switch>/command-api` (eAPI must be enabled on the switch: `management api http-commands`). Use `--eos-ca-file` for the switches' CA; `--eos-insecure` skips the check and is for labs only.

## Behaviour

- One run at a time. A run is stopped after 10 minutes; output streams back every 2 seconds.
- If the relay disappears mid-run, housekeeping marks the run failed about 15 minutes later.
- A high-risk script's runs wait until an admin other than the requester approves them.
- Runs started from a ticket are noted on it, with the end of their output.

## Schedules

A script can run by itself on a timetable: open it under **Scripts** and choose **Schedules > New schedule…**.

- **An action** runs on picked devices, a device group (a query returning `device_id`, worked out at each run, so a device that joins the group later is included) or the relay itself, with the parameter values saved in the schedule.
- **A query** runs and its result is emailed as CSV to the recipients, through the service desk mailbox. If it uses `:start`/`:end`, pick the period (yesterday, previous week, ...).
- Repeat once, every day, every week on chosen days, or every month on a day (or the last day), at a time (the server's local time).
- It runs as whoever made it, with their rights: if they can no longer run the script (or their account is disabled), the run doesn't happen and the schedule says why; the timetable carries on. A high-risk script's runs still wait for another admin's approval each time.
- Its runs are ordinary runs (Runs tab, with output) marked with the schedule; the schedule shows its next and last run, what the last run did and its latest runs. **Run now** runs it without changing the timetable. Deleting a schedule doesn't stop runs it already queued.
- The worker checks every minute (job "Scheduled scripts" under Settings > Status).

## AD changes and new accounts

The ticket's **AD account** card (group and OU changes, onboarding templates, new accounts) runs two built-in scripts, "Layer 8: change an AD account" and "Layer 8: create an AD account", on a PowerShell relay:

- The relay's host needs the ActiveDirectory module (RSAT), and the relay's service account needs rights in AD: change membership of the groups under Settings > Directory's group OUs, move users into the allowed OUs, and create users in the new-account OU.
- A new account's first password comes from Layer 8 as a **secret value**: encrypted in the database until the relay claims the run, then cleared. The relay passes it to the script as a variable only (never the environment, the log or the output). This needs relay **1.1** or later; an older relay fails the run with "No password reached this relay".
- To require a second admin's approval for these changes, mark the script high risk on the Scripts page.

### Start dates

A new account with a start date in the future is created disabled, and a second built-in run, "Layer 8: enable an AD account", is queued with a "not before" time of the start of that day (server time). Relays don't claim it until then, so the relay that takes PowerShell must be running on that morning. It shows as "Waits until …" on the ticket and in Scripts > All runs, and can be cancelled there like any queued run.

## IP address scans

IP addresses > a subnet > **Scan now** (or a subnet's schedule) queues the built-in "Layer 8: IP scan" script. It's Python using only the standard library, so the relay needs Python in its languages (Settings > Relays). It pings every address (64 at a time, up to a /20), looks up reverse DNS and reads the relay host's ARP / neighbour table (`ip neigh`, or `arp -a` on Windows). Put a relay on the subnet's LAN, and choose it on the subnet (Edit subnet > Scanned by): it then also finds devices that drop ping, with their MAC addresses. A scan of a routed subnet still works, without MACs.

### DHCP and DNS (Windows Server)

Set the DHCP and DNS servers under Settings > IP addresses (for example `dc01.corp.example` for both). Subnets then get a **Read DHCP and DNS** button, and scheduled scans also do this read. It queues the built-in "Layer 8: DHCP and DNS read" script (PowerShell, read-only), which, for the one subnet:

- lists the DHCP scopes that overlap it, with their reservations and leases (`Get-DhcpServerv4Scope`, `Get-DhcpServerv4Reservation`, `Get-DhcpServerv4Lease`);
- reads A records from the forward zones you name, or by default from every primary forward zone except `_msdcs` (`Get-DnsServerZone`, `Get-DnsServerResourceRecord -RRType A`).

The relay must be a Windows machine with PowerShell in its languages and the DhcpServer and DnsServer modules: on a server, `Install-WindowsFeature RSAT-DHCP, RSAT-DNS-Server`; on Windows 10/11, `Add-WindowsCapability -Online -Name Rsat.DHCP.Tools~~~~0.0.1.0` and `Rsat.Dns.Tools~~~~0.0.1.0`. Its service account needs to be in the domain's **DHCP Users** group (read-only) and to be able to read the DNS zones. Reading zones over the DNS management interface usually needs **DnsAdmins**. If you'd rather not grant that, leave the DNS server empty and read DHCP only. Name the relay under "Relay for DHCP and DNS reads" if more than one relay takes PowerShell.

What it does with the results:

- An address with an active lease counts as seen. A reservation or DNS record on its own doesn't, because those outlive the device, and nothing is flagged as "not seen" from this read.
- Empty host names and MACs are filled in. If the subnet has no DHCP pool yet, it's taken from the scope.
- These are findings to apply or dismiss:
  - a reservation, or a DNS name outside the pool, that isn't documented
  - a host name or MAC that differs from what's documented
  - a reservation documented as static, or a dynamic lease on an address documented as static
  - an address marked "do not use" that DHCP or DNS still has
- Dynamic leases aren't flagged as new hosts unless "Flag unknown hosts in DHCP pools too" is on.
- Findings from this read and from network scans are kept apart: a ping scan that misses a host doesn't clear what DHCP said, and the other way round.

### Network gear over SNMP

Enter one or more read-only SNMP v2c communities under Settings > IP addresses, separated by commas. Each is tried in turn. Subnets (up to a /22) then get **Discover (SNMP)**, and scheduled scans also discover. It queues the built-in "Layer 8: SNMP discovery" script, which is Python using only the standard library, on the subnet's relay. That relay needs Python and relay version **1.2** or later. The communities reach the script sealed, on its standard input, for that one run. They never appear in the run's values, the environment or the output. An older relay fails the run with "No SNMP community reached this script".

The script asks every address in the subnet for its system name, description, location and uptime (SNMPv2-MIB) and then reads:

- model and serial: ENTITY-MIB, or Printer-MIB for printers
- interface names: IF-MIB
- LLDP neighbours: LLDP-MIB, with their management addresses
- on switches, the MAC forwarding table: Q-BRIDGE-MIB, or BRIDGE-MIB on older switches; only learned entries

The relay's address must be allowed to poll the gear. On Cisco Meraki, turn on SNMP for the network in the Dashboard so the switches answer on their own management addresses.

What you get:

- **IP addresses > Network gear**: each device found, with vendor and model, serial, location and uptime, its LLDP neighbours (click through to neighbours that were discovered too) and what's on each port. That includes the MAC, VLAN and what's documented for it in IP addresses. Uplinks and ports with many MACs are folded up. Gear is linked to the matching asset under Devices by serial, or by name when exactly one matches. You can also link it by hand.
- **Switch ports on addresses**: a documented address with a MAC shows the switch and port it's on, on the address, the device's page and the ticket's device cards. When a MAC shows up on several switches, the port with the fewest MACs wins: the edge port, not the uplinks. "Shared" means other MACs were on that port too, such as a phone with a PC behind it, a small switch or an access point. `GET /api/network/where?mac=...` answers the same question.
- Each discovery replaces that device's neighbours and MAC table. Nothing in IP addresses is changed apart from last-seen on the gear's own address, and its host name if it had none. Gear that's gone can be forgotten by those who may delete subnets.

Not yet: SNMP v3 (it needs encryption that Python's standard library doesn't have), and a drawn topology map.
