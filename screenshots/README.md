# Screenshots

For the refreshed GUI, start with the [Command Center gallery (22 October 2026)](command-center/README.md). The images below are older feature examples and may show the previous layout.

From the dev app, 2026-10-21. Regenerate with `backend/.venv/bin/python dev/screenshots.py`.

## Sign-in page with branding (product name, message)

![Sign-in page with branding (product name, message)](01-sign-in.png)

## Agent queue: my tickets / my groups / all, views, technicians' colour stripes and personal colour rules; only the list scrolls

![Agent queue: my tickets / my groups / all, views, technicians' colour stripes and personal colour rules; only the list scrolls](02-queue.png)

## Agent queue: select tickets to move or assign them together

![Agent queue: select tickets to move or assign them together](02b-queue-bulk.png)

## Ticket: conversation, attachments, assignment, requester, devices, status

![Ticket: conversation, attachments, assignment, requester, devices, status](03-ticket.png)

## Arrange this page: drag panels between columns or across the top or bottom (full width), hide them, save for yourself or as everyone's default

![Arrange this page: drag panels between columns or across the top or bottom (full width), hide them, save for yourself or as everyone's default](03d-ticket-arrange.png)

## Ticket: a New starter checklist started for John Smith: each step a child ticket, due from the start date, with progress

![Ticket: a New starter checklist started for John Smith: each step a child ticket, due from the start date, with progress](03e-ticket-checklist.png)

## Ticket: the requester's AD account: groups, an onboarding template, add a group, move OU, department and title (checked before it runs)

![Ticket: the requester's AD account: groups, an onboarding template, add a group, move OU, department and title (checked before it runs)](03b-ticket-ad.png)

## Onboarding: a new AD account from the ticket, with phone, extension, distribution lists as tick boxes, checked against AD before it's created

![Onboarding: a new AD account from the ticket, with phone, extension, distribution lists as tick boxes, checked against AD before it's created](03c-ticket-new-account.png)

## Devices: AD computers from directory sync and devices added by hand

![Devices: AD computers from directory sync and devices added by hand](04-devices.png)

## Devices > Stale: no sign of life from AD, Action1 or network scans for 90 days

![Devices > Stale: no sign of life from AD, Action1 or network scans for 90 days](04b-devices-stale.png)

## Devices > Unknown on the network: seen by scans or in Action1, but not a device here

![Devices > Unknown on the network: seen by scans or in Action1, but not a device here](04c-devices-unknown.png)

## A device: details, patching from Action1, IP addresses, notes and linked tickets

![A device: details, patching from Action1, IP addresses, notes and linked tickets](05-device.png)

## Scripts: the library; a query run with its parameters

![Scripts: the library; a query run with its parameters](05b-scripts.png)

## Scripts: an action with a parameter, ready to run on devices, a device group or the runner

![Scripts: an action with a parameter, ready to run on devices, a device group or the runner](05c-script-action.png)

## Scripts: all runs, with output from the runner

![Scripts: all runs, with output from the runner](05d-script-runs.png)

## Knowledge base: search, drafts and published articles; who can read each one

![Knowledge base: search, drafts and published articles; who can read each one](05f-kb.png)

## Knowledge base: writing an article in simple Markdown, with a preview

![Knowledge base: writing an article in simple Markdown, with a preview](05g-kb-edit.png)

## Scripts: a schedule pinging a device group twice a week, with its last run

![Scripts: a schedule pinging a device group twice a week, with its last run](05e-script-schedules.png)

## Vendors: account, support line, portal, hours, contract and contacts

![Vendors: account, support line, portal, hours, contract and contacts](05e-vendors.png)

## Systems: what a vendor supports, who to call, its addresses and devices

![Systems: what a vendor supports, who to call, its addresses and devices](05f-systems.png)

## IP addresses: a subnet's map of static, reserved, leased, do-not-use and free addresses, the DHCP pool and gateway

![IP addresses: a subnet's map of static, reserved, leased, do-not-use and free addresses, the DHCP pool and gateway](05k-ipam-subnet.png)

## IP addresses: a scan by a runner on the LAN (ping, reverse DNS, ARP) and what it found to review: apply or dismiss, nothing changes by itself

![IP addresses: a scan by a runner on the LAN (ping, reverse DNS, ARP) and what it found to review: apply or dismiss, nothing changes by itself](05m-ipam-scan.png)

## IP addresses: Windows DHCP leases and reservations and DNS records read for a subnet, with the differences to review

![IP addresses: Windows DHCP leases and reservations and DNS records read for a subnet, with the differences to review](05n-ipam-dhcp-dns.png)

## IP addresses > Network gear: a switch found by SNMP discovery, its LLDP neighbours, and what's plugged into each port (MAC, VLAN, documented address and device)

![IP addresses > Network gear: a switch found by SNMP discovery, its LLDP neighbours, and what's plugged into each port (MAC, VLAN, documented address and device)](05o-network-gear.png)

## IP addresses: find an address by IP, host name, MAC in any format, device or system

![IP addresses: find an address by IP, host name, MAC in any format, device or system](05l-ipam-find.png)

## Changes: risk-scored, with a window and owner; standard (pre-approved), normal and emergency

![Changes: risk-scored, with a window and owner; standard (pre-approved), normal and emergency](05n-changes.png)

## Changes > Calendar: a month of change windows, coloured by risk

![Changes > Calendar: a month of change windows, coloured by risk](05n2-change-calendar.png)

## A change awaiting approval: plans (rollback required), risk, window, linked ticket and device, approve or reject with a note

![A change awaiting approval: plans (rollback required), risk, window, linked ticket and device, approve or reject with a note](05o-change.png)

## Settings > Changes: the risk that needs approval, who approves, and templates (pre-approved ones make standard changes)

![Settings > Changes: the risk that needs approval, who approves, and templates (pre-approved ones make standard changes)](09d-settings-changes.png)

## Purchase orders: search, status, what's in, totals

![Purchase orders: search, status, what's in, totals](05g-purchase-orders.png)

## A purchase order: lines with tax, approval and ordering, receiving, heads-up to receiving, files and history

![A purchase order: lines with tax, approval and ordering, receiving, heads-up to receiving, files and history](05h-purchase-order.png)

## A purchase order, arranged: its panels in a column, across the top or bottom, or hidden; for yourself or everyone

![A purchase order, arranged: its panels in a column, across the top or bottom, or hidden; for yourself or everyone](05h2-purchase-order-arrange.png)

## Receiving sheet: what's expected, a QR code, and the address, PO number and code written out

![Receiving sheet: what's expected, a QR code, and the address, PO number and code written out](05i-receiving-sheet.png)

## The receiving page: no sign-in, PO number and code, then what arrived

![The receiving page: no sign-in, PO number and code, then what arrived](05j-receive-public.png)

## Reports: ready-made, saved and custom reports; PDF/CSV, email and schedules

![Reports: ready-made, saved and custom reports; PDF/CSV, email and schedules](06-reports.png)

## Settings > General: portal address, ticket numbering and label format (with a preview), limits

![Settings > General: portal address, ticket numbering and label format (with a preview), limits](06b-settings-general.png)

## Settings > Branding: name, texts, colours with live preview and contrast check, images

![Settings > Branding: name, texts, colours with live preview and contrast check, images](07-settings-branding.png)

## Settings > Surveys: the share of solved tickets surveyed, how often one person is asked, and the results

![Settings > Surveys: the share of solved tickets surveyed, how often one person is asked, and the results](07c-settings-surveys.png)

## Settings > Permissions: who may delete tickets, publish articles, run ticket rules..., technicians, admins, or admins and chosen groups

![Settings > Permissions: who may delete tickets, publish articles, run ticket rules..., technicians, admins, or admins and chosen groups](07d-settings-permissions.png)

## Settings > Runners: machines that run action scripts, with check-in status

![Settings > Runners: machines that run action scripts, with check-in status](07b-settings-runners.png)

## Settings > Action1: patch status read from Action1 (read only), test connection, machines without a device, overdue-patching tickets

![Settings > Action1: patch status read from Action1 (read only), test connection, machines without a device, overdue-patching tickets](07c-settings-action1.png)

## Settings > Backup: schedule, passphrase, backups with verify

![Settings > Backup: schedule, passphrase, backups with verify](08-settings-backup.png)

## Settings > Onboarding templates: groups (with nesting warnings), OU, department and title

![Settings > Onboarding templates: groups (with nesting warnings), OU, department and title](09b-settings-onboarding.png)

## Settings > Checklists: onboarding and offboarding steps, each due days from the start date or last day, with a queue or technician; a step can disable the AD account on its day

![Settings > Checklists: onboarding and offboarding steps, each due days from the start date or last day, with a queue or technician; a step can disable the AD account on its day](09c-settings-checklists.png)

## Settings > Directory: AD connection, sync, role mappings, computers

![Settings > Directory: AD connection, sync, role mappings, computers](09-settings-directory.png)

## Settings > Status: services and worker jobs

![Settings > Status: services and worker jobs](10-settings-status.png)

## Settings > Log: the system log

![Settings > Log: the system log](11-settings-log.png)

## Settings > Ticket rules (KACE-style, incl. SQL rules)

![Settings > Ticket rules (KACE-style, incl. SQL rules)](12-settings-rules.png)

## My preferences: my colour in the queue and my own colour rules

![My preferences: my colour in the queue and my own colour rules](12b-preferences.png)

## Self-service portal: welcome text and the guided request form

![Self-service portal: welcome text and the guided request form](13-portal.png)

## Portal: my tickets

![Portal: my tickets](14-portal-tickets.png)

## Portal: help articles, searchable

![Portal: help articles, searchable](15-portal-help.png)

## Portal: an article, and whether it helped

![Portal: an article, and whether it helped](15b-portal-article.png)

## Satisfaction survey: one click in the email answers; a comment can follow (no sign-in needed)

![Satisfaction survey: one click in the email answers; a comment can follow (no sign-in needed)](16-survey.png)

# Dark mode

Settings > Branding > Dark mode: off, always, or following each person's device setting.

## Sign-in page with branding (product name, message) (dark)

![Sign-in page with branding (product name, message), dark](dark/01-sign-in.png)

## Agent queue: my tickets / my groups / all, views, technicians' colour stripes and personal colour rules; only the list scrolls (dark)

![Agent queue: my tickets / my groups / all, views, technicians' colour stripes and personal colour rules; only the list scrolls, dark](dark/02-queue.png)

## Ticket: conversation, attachments, assignment, requester, devices, status (dark)

![Ticket: conversation, attachments, assignment, requester, devices, status, dark](dark/03-ticket.png)

## A device: details, patching from Action1, IP addresses, notes and linked tickets (dark)

![A device: details, patching from Action1, IP addresses, notes and linked tickets, dark](dark/05-device.png)

## Knowledge base: search, drafts and published articles; who can read each one (dark)

![Knowledge base: search, drafts and published articles; who can read each one, dark](dark/05f-kb.png)

## IP addresses: a subnet's map of static, reserved, leased, do-not-use and free addresses, the DHCP pool and gateway (dark)

![IP addresses: a subnet's map of static, reserved, leased, do-not-use and free addresses, the DHCP pool and gateway, dark](dark/05k-ipam-subnet.png)

## IP addresses > Network gear: a switch found by SNMP discovery, its LLDP neighbours, and what's plugged into each port (MAC, VLAN, documented address and device) (dark)

![IP addresses > Network gear: a switch found by SNMP discovery, its LLDP neighbours, and what's plugged into each port (MAC, VLAN, documented address and device), dark](dark/05o-network-gear.png)

## Reports: ready-made, saved and custom reports; PDF/CSV, email and schedules (dark)

![Reports: ready-made, saved and custom reports; PDF/CSV, email and schedules, dark](dark/06-reports.png)

## Self-service portal: welcome text and the guided request form (dark)

![Self-service portal: welcome text and the guided request form, dark](dark/13-portal.png)
