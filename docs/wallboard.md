# Wallboards

A wallboard is a live page for a big screen in the IT office: how many tickets are open, unassigned, past or close to their SLA, waiting on the user, new and resolved today; alerts for what needs someone now; the oldest unassigned tickets; the tickets closest to (or past) their SLA; and per-queue and per-technician bars. It refreshes every 30 seconds and shows a clock.

It opens from a secret link with no sign-in, so a TV, a small PC or a stick computer only needs a browser.

## Make one

**Settings > Wallboards > New wallboard** (admins):

- **Name**: shown on the board, e.g. *Service desk TV*.
- **Queues**: none ticked shows every active queue that isn't restricted. A restricted queue shows only when it is ticked here.
- **Alert when a ticket is unassigned for**: minutes (default 30). Older unassigned tickets raise an alert and are marked in the list.

The link is shown once: `https://<server>/wallboard#l8w_...`. Copy it to the screen's browser and press F11 for full screen. Only a hash of the link is stored.

## What it shows

| Area | Contents |
| --- | --- |
| Alerts | The background worker has stopped; tickets past their SLA; high or critical tickets with nobody assigned; tickets unassigned longer than the board's limit; tickets at risk of missing their SLA. "All clear" when there are none. |
| Counts | Open, unassigned, SLA breached, SLA at risk (not yet breached), waiting on user, new today, resolved today. |
| Unassigned | The eight oldest, with number, subject, priority, queue, requester and age. |
| SLA | The eight due soonest (breached first), with number, subject, priority, technician, requester and time left or over. |
| Queues | Open, unassigned and breached per queue. |
| Technicians | Open tickets and those needing attention (at risk or breached) per technician, the twelve busiest. |

"Today" is the server's local day.

## Who can see it

The board shows ticket subjects and the names of requesters and technicians. Anyone who can see the screen, or who has the link, can read them. Put boards only where that is fine, and choose queues accordingly.

The secret is after the `#`, so browsers never send it in a request line, a Referer header or a server log; the page sends it as `Authorization: Bearer l8w_...` to `/api/public/wallboard`. That endpoint returns the board's data only. It can't change anything or reach other APIs.

**New link** replaces the link (the old one stops at once) and also turns a revoked board back on. **Revoke** switches it off: the screen says so on its next refresh. **Last shown** in the list tells you whether a screen is still using it (updated at most every five minutes).
