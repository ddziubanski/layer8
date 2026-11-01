# Problem management

Problems are staff-only investigation records for recurring or not-yet-understood incidents. They complement, rather than replace, tickets and the Library.

A problem groups the tickets that report the same underlying issue. Its status tracks the investigation: Open, Investigating, Known error, Resolved or Closed. Technicians can record a description, suspected or confirmed root cause, workaround and resolution, assign an owner, and add internal updates to its chronological thread.

Ticket links are explicit; the app does not infer that two tickets share a cause. Staff must be able to see every linked ticket to read or change the problem. Requesters never see problem records or investigation notes. If a linked ticket is in a restricted queue, technicians without access to that queue cannot see the problem.

Use a problem record to coordinate investigation and preserve operational context. Publish reviewed, reusable instructions as a Library article when appropriate. Similar-ticket and article suggestions in the ticket workspace are a separate follow-on workflow.

## From the ticket

A ticket's **Work** tab has a **Problems** panel. It shows the problems the ticket belongs to, with each one's status, workaround and root cause, so the next technician sees what to do without leaving the ticket. **Link to a problem…** searches problems by title, cause or workaround. **Create problem from this ticket** opens a new problem with the ticket's subject and description, already linked. Linking and unlinking from either side is written to the ticket's history by problem number only, because someone who can see the ticket may not be allowed to open the problem. A problem linked to a ticket you can't see is counted ("also part of 1 problem you can't open"), never named.

## Deleting a problem

**Delete problem…** on the problem page needs the *Delete problems* permission (admins by default; Settings > Permissions). Its linked tickets stay, and each one's history says the problem was deleted. To keep the record, set the status to Closed instead.

API: `GET/POST /api/tickets/{id}/problems`, `DELETE /api/tickets/{id}/problems/{problem_id}`, `DELETE /api/problems/{id}`.

## Outages: one problem, one notice, one update

When two or more tracked devices (UniFi, Meraki or SNMP gear, Meraki webhook alerts) go down at the same **switch**, **subnet** or **site** within 30 minutes, the Problems page shows the outage under **Outages without a problem**. **Open a problem…** creates one in the Investigating state:
- **Tickets:** it links the outage tickets you can see.
- **Scope:** it records the outage scope, for example "Site Branch". You can edit the scope on the problem page.
- **Draft notice (optional):** it can draft a service notice. The notice stays off until someone turns it on in Settings > Notices. Drafting one needs the *Manage notices* permission.

While the problem is open:
- **New tickets:** tickets from people at that site, on that switch or in that subnet get a **Part of this outage?** suggestion on their Problems card.
- **Problem page:** the page lists the same tickets so you can link them in one click.
- **Outage panel:** this panel shows what is still down and the state of the notice. Recoveries from before the problem don't count.

When everything is back up, **Update everyone affected…** sends one reviewed reply to every open linked ticket. It works like the parent-ticket update: you preview it first, and each person is emailed once. You can choose to resolve all the tickets with it. When the last linked ticket is resolved, the problem becomes Resolved and its service notice ends.

API: `GET /api/problems/outage-clusters`, `POST /api/problems/from-outage`, `POST /api/problems/{id}/broadcast/preview`, `POST /api/problems/{id}/broadcast`. `GET /api/tickets/{id}/problems` also returns `suggested`.

## Suggested from patterns

The Problems page shows **These look like one problem** when enough open tickets arrive close together and share something:
- a device model;
- a system;
- a switch, subnet or site;
- a distinctive word in the subject.

The thresholds are in Settings > Problems: how many tickets (default 3; 0 turns suggestions off) within how many hours (default 48).

Rules:
- Only tickets you can see count, and tickets already in a problem are left out.
- **Create problem** opens one in the Investigating state with those tickets linked.
- **Dismiss** hides that exact group of tickets until another ticket joins it.

API: `GET /api/problems/suggestions`, `POST /api/problems/suggestions/dismiss`.
