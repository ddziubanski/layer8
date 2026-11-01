# Device timeline

A device's page (People & devices > Devices > *device*) has a **Timeline**: one history merged from everything Layer 8 records about that device, newest first, grouped by day.

| Kind | From |
|---|---|
| Tickets | Tickets linked to the device, or naming it as their computer: opened, resolved or closed |
| Changes | Changes listing the device, with their outcome |
| Scripts | Script runs on it: which script and version, the result and who asked |
| Remote | Remote Assistance, Remote Desktop and Action1 sessions started from a ticket |
| Software | Action1 software deployments and their outcome |
| Network | When each of its IP addresses was first seen (IPAM scans and DHCP) |
| Purchases | Purchase orders that list it |
| Lifecycle | Added to Layer 8, purchased, last AD sign-in |

Above the history, **right now** shows what's only kept as current state (Action1 status, a pending reboot, missing critical updates, last boot, current IP addresses), and **coming up** shows the warranty end and replace-by date. The card refreshes every minute.

Each entry links to its source. Filters hide or show kinds. Visibility follows the source: an entry that belongs to a ticket in a queue you can't see is counted ("2 more entries belong to tickets in queues you can't see") and never named. Script output and deployment details stay on their own pages.

It only reads what Layer 8 already has; opening it never calls Action1, UniFi or any other system.

API: `GET /api/devices/{id}/timeline` (`?kinds=ticket,change` to limit it).

Next (issue #156): record changes Layer 8 sees in its regular syncs (online and offline, switch port and VLAN moves, IP changes, patch and reboot changes, AD changes) so they join the timeline as they happen.
