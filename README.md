# Layer 8

*The service desk for the layer every network has: its people.*

Layer 8 is a self-hosted IT service desk for small and mid-sized IT teams: one queue for tickets from email and a self-service portal, with the tools a desk actually uses day to day, all in one install you control. It borrows the best ideas from ManageEngine, Track-It!, KACE and Spiceworks, runs on a single Linux box (an appliance ISO installs it offline), and works standalone or with Active Directory and Microsoft 365.

> **Version 1.0.** Free software under the [GNU AGPL-3.0](LICENSE) (or later). In use on real desks; back up before upgrading.

![The technician's queue](screenshots/02-queue.png)

**At a glance**

- **One queue** for tickets from email and the self-service portal, with queues, SLAs, routing rules, checklists and an arrangeable ticket page.
- **The work around tickets:** devices from AD, IP address management with network scans, purchase orders, vendors, changes, a knowledge base, reports and a script library with runners.
- **Microsoft 365 when you want it:** email through Graph, Teams messages and status, and Microsoft 365 Copilot on tickets, each optional. Step by step: [docs/microsoft-365.md](docs/microsoft-365.md).
- **Your network and tools:** Cisco Meraki discovery and outage tickets, Action1 patch status and reviewed software deployment, Dell and Lenovo warranties, all opt-in and read-only unless you say otherwise.
- **For the room:** a wallboard for a big screen, desk hours on the portal, a Ctrl+K palette, dark mode and a Midnight theme, and a sidebar each technician can trim.
- **Runs on one box you control:** an offline appliance ISO (Debian 13) with a setup wizard and a recovery console, encrypted backups, and no cloud required.

<p>
  <img src="screenshots/03-ticket.png" width="49%" alt="A ticket: conversation, assignment, requester and devices">
  <img src="screenshots/17-wallboard.png" width="49%" alt="The wallboard for a big screen">
  <img src="screenshots/13-portal.png" width="49%" alt="The self-service portal with each team's hours">
  <img src="screenshots/dark/02-queue.png" width="49%" alt="The queue in dark mode">
</p>

All screenshots, from the demo data: [screenshots/](screenshots/README.md). A gallery of the refreshed interface: [screenshots/command-center/](screenshots/command-center/README.md).

## What it does

**Tickets**
- Email (Microsoft 365 / Exchange Online through Graph) and a self-service portal feed one queue and one routing engine.
- Queues with owners, managers and restricted visibility; SLAs; priorities from urgency × impact.
- KACE-style ticket rules: on save, on a schedule, or in SQL.
- Merge, parent and child tickets, bulk changes, and a page layout each technician can arrange.
- "Tickets Anywhere": technicians act on tickets by replying to email (`#close`, `#assign`, `#priority`).
- Checklists for onboarding and offboarding, where each step becomes a child ticket due on its day.
- A wallboard for a big screen in the IT office: live counts, alerts (SLA breaches, unassigned tickets) and the tickets that need someone, opened by a secret link.
- Desk hours per queue on the portal, with an after-hours message saying when someone is next in, and technicians' Teams status (status only).

**People and access**
- Local accounts that need nothing else, or AD/LDAP sign-in with roles from AD groups.
- Silent Windows sign-in (Kerberos) to the portal.
- Creating AD accounts from a ticket: onboarding templates, group lists, a start date, and the password shown once.
- Finer permissions: who may delete, merge, publish or manage each area, by group.
- A personal workspace: hide optional sidebar modules individually or choose a minimal preset in My preferences. Tickets, My preferences and sign-out stay available; hidden modules remain reachable through Ctrl+K and direct links with existing permissions. [Workspace preferences](docs/workspace-preferences.md).

**Devices and network**
- Devices from AD computer objects, with tickets, IP addresses, switch port and lifecycle on each.
- Patch status read from Action1, and opt-in software deployment through Action1: one reviewed package to one ticket-linked computer, logged on the ticket ([docs/action1.md](docs/action1.md)).
- Cisco Meraki, read-only and opt-in: client discovery (switch port, VLAN, SSID) on IP records, devices and tickets; port and wireless diagnostics; and device outage tickets from Meraki alerts.
- Dell and Lenovo warranty lookups through their official APIs.
- IP address management:
  - subnets and VLANs, with a used/free map
  - scans by a runner on the LAN (ping, reverse DNS, ARP)
  - Windows DHCP leases and DNS records
  - SNMP discovery with LLDP neighbours and switch MAC tables

  Scans flag differences; they never change your records by themselves.
- Stale and unknown device sweeps, and warranty and replacement forecasting.

**Automation**
- A script library: PowerShell (locally or over WinRM), bash, Python and Arista EOS.
- 27 ready-made SQL queries to add from the Scripts page: ticket throughput and ageing, reopened and moved tickets, first-response and resolution times, busiest requesters and hours, warranties, replacements, duplicate serials and more. Device and people queries double as dynamic groups.
- Scripts run on "runners" that pull work over HTTPS, so no inbound ports are needed.
- Versions with diffs, approval for high-risk scripts, schedules, and every run logged on its ticket.
- A Remote Assistance button on tickets, logged as well.
- Optional reviewed Action1 software deployments: search available packages from a ticket-linked computer or device, choose an exact version, review the endpoint and explicitly deploy. Separate credentials and permissions; results and ticket notes track the outcome. [Setup and first-slice limits](docs/action1.md#reviewed-software-deployment-first-slice).

**Microsoft 365 and Teams**
- Optional Teams Workflows summaries with queue counts; restricted queues are excluded.
- Technicians connect their own Microsoft work accounts in My preferences and send a requester a message in an existing one-to-one Teams chat from a ticket. The message is recorded as a public ticket reply.
- Optional **Microsoft 365 Copilot on tickets (preview)**: ask as the connected technician, after reviewing exactly which ticket text is shared (internal notes only by a separate choice); follow up, see sources, and save an edited answer as an internal note or send it to the requester as a reviewed reply. Each technician needs a Microsoft 365 Copilot licence; Microsoft's Chat API is a preview. See the [Microsoft 365 setup guide](docs/microsoft-365.md).

**Everything else a desk needs**
- Knowledge base with versions and images, suggested in the portal as people type.
- Satisfaction surveys on a random share of solved tickets.
- Change management: approvals, a calendar, overlap warnings, and Action1 patch nights as standard changes.
- Purchase orders, with receiving by the clerk, vendors and systems.
- Reports with charts, PDF/CSV export and scheduled email delivery; a system log and status page.
- Encrypted backups to SMB/NFS with restore; a recovery console on the appliance; import from Track-It!.

Current GUI screenshots, including dark mode, Settings and a tablet view: [command-center gallery](screenshots/command-center/README.md). Older feature examples remain in [screenshots/](screenshots/README.md). The full requirements and decisions are in [SPEC.md](SPEC.md).

## Try it

- **Appliance ISO (recommended):** Debian 13 plus Layer 8. It installs with no internet access and asks its questions in a setup wizard on first boot. Build it with `iso/build.sh` (needs Docker and npm), or `iso/build.sh --demo` for a one-click offline demo with sample data. The branded BIOS/UEFI boot menu defaults to choosing the disk and partitioning; erasing the entire first disk is a separate explicit choice. The setup wizard and recovery console also use the refreshed styling. See [docs/install.md](docs/install.md).
- **On an existing Debian 13 server:** `install/install.sh`, same doc.
- **For development:** [backend/README.md](backend/README.md) covers the dev database (Docker Compose), the API, the web UI, tests and migrations.

Every setting can be changed in the app (Settings), and also imported from an answers file, so installs can be scripted end to end.

## Planned

- **Appliance updates:** an optional online release check alongside offline installation, followed by signed update bundles, backup and rollback support. The current ISO installs its bundled application; it does not automatically pull a newer version from GitHub. Rebuild the ISO to include newer merged application changes.
- **Action1 deployment, later slices ([#33](https://github.com/ddziubanski/layer8/issues/33)):** bulk targets and onboarding software bundles. One reviewed package to one ticket-linked computer is already available (opt-in, Settings > Action1).

## Integrations (all optional)

| | Guide |
|---|---|
| Email intake and notifications (Microsoft Entra app, Graph) | [docs/email-setup.md](docs/email-setup.md) |
| Technician Teams messages and queue summaries | [docs/teams.md](docs/teams.md) |
| Windows sign-in (Kerberos SSO) | [docs/windows-sign-in.md](docs/windows-sign-in.md) |
| Runners: scripts, AD changes, IP scans, DHCP/DNS, SNMP | [docs/runner.md](docs/runner.md) |
| Remote Assistance from tickets | [docs/remote-assistance.md](docs/remote-assistance.md) |
| Action1 patch status and patch automation calendar (read-only) | [docs/action1.md](docs/action1.md) |
| Dell and Lenovo warranty lookups | [docs/warranty.md](docs/warranty.md) |
| Moving from Track-It! | [docs/trackit-import.md](docs/trackit-import.md) |
| Recovery console | [docs/recovery-console.md](docs/recovery-console.md) |
| Other apps (API keys: look up tickets, add notes) | [docs/integrations.md](docs/integrations.md) |
| Wallboards (live board for a big screen, secret link) | [docs/wallboard.md](docs/wallboard.md) |
| Desk hours and technicians' Teams status on the portal | [docs/desk-hours.md](docs/desk-hours.md) |
| **Microsoft 365, step by step** (email, Teams, technician connections, Copilot) | [docs/microsoft-365.md](docs/microsoft-365.md) |

## Built with

Python and FastAPI, PostgreSQL, React and TypeScript. The runner is a single Python file using only the standard library. The appliance is Debian 13 with nginx.

## Contributing and security

Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). Please report security problems privately, as described in [SECURITY.md](SECURITY.md).

## License

Layer 8 is free software: you can redistribute it and/or modify it under the terms of the
[GNU Affero General Public License](LICENSE) as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version (SPDX:
`AGPL-3.0-or-later`). It is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; see the license for details.

Copyright (C) 2026 Dan Dziubanski and contributors.

**What this means in practice.** You can run it, change it and share it. If you run a
modified copy for other people over a network, offer them its source too. Layer 8 does this for
you: the console, the portal and the sign-in page link to **Source code**, which downloads the
exact source of the running installation (packed by the ISO build), and to the repository
(Settings > General > Source code address; point it at your own fork if you change Layer 8).

**Earlier versions.** Releases before 1.0 (the 0.1 alphas) were published under the MIT
license, and copies of those releases remain available under MIT. Version 1.0 and everything
after it is AGPL-3.0-or-later.

## Credits

Built by Dan Dziubanski, with Claude (Anthropic): aka Claude The Homie.
