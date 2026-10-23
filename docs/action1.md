# Action1 (patch status, read only)

Layer 8 reads each machine's patch state from Action1 and shows it where you work: on the device's page and on a ticket's **Requester's computer** and **Devices** cards. Patching itself stays in Action1; The patch integration never changes anything there. Optional software deployment uses a separate opt-in connection, described below.

What's read, per machine: missing critical and other updates, open vulnerabilities (critical and other), its organization, whether a reboot is pending, when Action1 last saw it, its agent status and version, last boot and signed-in user. Layer 8 also notes since when critical updates have been missing and a reboot pending (from the first read that saw it).

## Set it up

1. In Action1: **Configuration > API credentials > Create**. Give it a role that can only read (a custom role with view rights on endpoints is enough; add view rights on automations for patch changes, below). Copy the client ID and the secret (shown once).
2. In Layer 8, switch it on: **Settings > General > Use Action1 (patching)**. The installer asks this too. While it's off, the Action1 tab, patch status on devices and tickets, and Action1 in the device sweeps are hidden, and nothing is read.
3. **Settings > Action1**: pick the region your console is in (`app.action1.com` is North America; `app.eu.action1.com` Europe, and so on), paste the client ID and secret, and save.
4. **Test connection** lists the organizations the credentials can read. Leave **Organizations to read** empty to read all of them (new ones are picked up by themselves), or put organization IDs there, one per line, to read only those.
5. **Read Action1 now**, or wait for the worker (every 60 minutes by default; Settings > Status shows the job "Action1: read patch status").

The same values can come from the installer's answers file (`action1_region`, `action1_client_id`, `action1_client_secret`, `action1_org_ids` (a list), ...): `python -m app.cli settings import answers.json`.

## Matching machines to devices

By name: Action1's machine name against a device's name (`WS-0142`) or host name (`ws-0142.corp.example`). Devices come from AD, so machines Action1 knows but AD doesn't (or that are named differently) are listed under **Settings > Action1** as "without a device here". A re-installed agent (a new Action1 ID for the same name) takes over the device.

## Tickets for overdue patching

Off by default. Set **Open a ticket when critical updates are missing for (days)** and/or **... when a reboot is pending for (days)**. When a machine passes either, the next read opens one ticket, from "Service Desk" with the device linked, through the routing rules like any other (add a rule on the subject "Patching overdue" to send them to a queue). No second ticket is opened while that one is open; once it's resolved or closed and the machine is still overdue, the next read opens another.

## Patch automations on the change calendar

Each enabled Action1 automation that deploys updates, runs an update ring or reboots machines becomes a change in Layer 8 when its next run is near (**Put patch automations on the change calendar (days ahead)**, 7 by default; 0 turns it off). So patch nights show on **Changes > Calendar**, and a firmware upgrade or other change planned over the same window, or on the same machines, gets an overlap warning.

- Each one is a **standard change, pre-approved** (the automation in Action1 is the approved procedure), titled "Patching: " and the automation's name, from "Service Desk", marked "from Action1". It lasts **A patch change lasts (hours)** (4 by default) from the run's start.
- Its devices are the machines the automation runs on: every machine in the organization, the machines in its endpoint groups, or those chosen one by one, matched to devices as above.
- It follows Action1: a run Action1 moves moves the change; disabling or deleting the automation cancels its upcoming change. When the run starts the change is in progress; when it ends, Success or Warning completes it and Error fails it, with Action1's status as the outcome. A run Action1 never made is cancelled a day after its window.
- One change per run: after one ends, the next run gets a new one. A change someone cancels by hand isn't made again for that run.
- Automations that run scripts or install software aren't changes here.
- The credentials need view rights on automations (and on endpoint groups when automations target groups). Without them, patch status is still read and the job's summary says the automations couldn't be.

Times: Action1's `next_run` is read as UTC. An automation set to each machine's local time starts at that local time on each machine; the change shows the UTC time.

## Checked against the live API

On 2026-10-20 the API returned the counts nested (`missing_updates: {critical, other}`, `vulnerabilities: {critical, other}`) rather than the flat `missing_critical_updates` / `missing_other_updates` the docs show; both are read.

## Limits

- Action1 asks for fewer than 30 API requests a minute. A read is one request per 50 machines, plus signing in.
- Patch changes cost one request per organization, one per endpoint group an automation targets, and one per run that has started.

## Reviewed software deployment (first slice)

From a ticket's **Requester's computer** card or a device's **Patching (Action1)** section, authorised technicians can open **Install software through Action1**. Search the organisation's available packages, select an exact version and review the computer, Action1 endpoint ID, organisation, platform and reported installed-software matches. Search and review do not start installation. **Deploy reviewed software** is the explicit external action.

### Set up deployment separately

1. Keep the existing patch-reading credentials read-only. Create separate Action1 API credentials in the same region, with `view_endpoints`, `view_software_repository`, `view_automations` and `run_automations`. These names come from the official API documentation; scope that role to the organisations you intend to manage.
2. In **Settings > Action1**, save **Deployment client ID**, **Deployment client secret** and an explicit **Organizations allowed for deployment** list. Empty permits none. Turn on **Allow reviewed software deployments**. Values are importable through the `action1` answers-file section (`action1_deploy_enabled`, `action1_deploy_client_id`, `action1_deploy_client_secret`, `action1_deploy_org_ids`). The secret is stored as a write-only file.
3. In **Settings > Permissions**, grant **Deploy software through Action1** to the appropriate technicians/groups. It defaults to admins only; requesters and portal-only sessions cannot deploy.
4. After upgrading, run **Read Action1 now**. Existing endpoint records need their organisation ID populated before deployment. Layer 8 checks that the endpoint is still in the allowed organisation and that its current name matches the device before reviewing and submitting.
5. Test with a non-production endpoint and a package whose installation/licensing you have already reviewed in Action1. No live deployment has been performed by the automated tests.

### Boundaries and outcomes

- Windows and Mac packages only; one package/version to one endpoint, immediately. Scheduling, groups, bulk installs and onboarding bundles are later work.
- Automatic reboot is disabled. Packages declaring additional actions are rejected in this slice; run those from Action1. A package with an unaccepted EULA must be reviewed there first. Layer 8 does not accept licences on your behalf.
- Action1 retries disconnected endpoints for 30 minutes. **Accepted/pending is not installed**. The worker reads results once a minute, up to ten active runs per pass. Success can also mean the required software was already present; warnings require inspecting Action1. The cached inventory search is informational, not proof of absence or installation.
- A review expires after ten minutes. Package/version settings, target identity and deployment configuration are checked again at submission. A change requires a fresh review.
- Durable intent is saved before the external POST. Repeating that POST uses the same review record and cannot submit it again; another active/uncertain deployment of the same package to the same device is blocked.
- If the connection fails without confirming acceptance, the state is **unknown**. **Refresh deployment result** can look up the uniquely named `Layer8 <reference>` automation and verify its exact package and endpoint without resubmitting. If none can be confirmed, an administrator must inspect Action1 manually; this first slice deliberately provides no force-resend/clear-unknown action. A process interruption while submitting follows the same recovery path.
- On a ticket, submission and status changes add internal notes and history events without requester email. Deployments started directly from Devices are kept in deployment history and the system log. The history shows the current technician's deployments (admins may see all in the selected device/ticket context).
- Disabling deployment stops catalogue/submission/provider refresh calls. Existing local history stays available. Rotating deployment settings requires checking older outstanding runs in Action1; Layer 8 does not query a different credential scope automatically.

The optional feature does not deploy patches, change Action1 schedules or alter the existing patch-status worker. Primary contract: [Action1 REST API](https://app.action1.com/apidocs/), checked 22 October 2026: catalogue/versions, installed-software inventory, `POST /automations/instances/{orgId}` with `deploy_package`, and instance status reads. Implementation and tests are original; no PSAction1 code is copied.
