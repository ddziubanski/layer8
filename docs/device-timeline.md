# Device timeline

A device's page (People & devices > Devices > *device*) has a **Timeline**: one history merged from everything Layer 8 records about that device, newest first, grouped by day.

| Kind | From |
|---|---|
| Tickets | Tickets linked to the device, or naming it as their computer: opened, resolved or closed |
| Changes | Changes listing the device, with their outcome |
| Scripts | Script runs on it: which script and version, the result and who asked |
| Remote | Remote Assistance, Remote Desktop and Action1 sessions started from a ticket |
| Software | Action1 software deployments and their outcome |
| Network | When each of its IP addresses was first seen (IPAM scans and DHCP); moving to another switch, port, VLAN or SSID (UniFi, Meraki); going offline and coming back online (network equipment linked to the device) |
| Purchases | Purchase orders that list it |
| Updates | What Action1 reports changing: agent online or offline, restarts, a restart needed, critical updates missing or installed |
| Directory | What directory sync sees changing in AD: operating system upgrades, enabled or disabled, renamed, moved to another OU, assigned to someone, gone from or back in AD |
| Lifecycle | Added to Layer 8, purchased, last AD sign-in |

Above the history, **right now** shows what's only kept as current state (Action1 status, a pending reboot, missing critical updates, last boot, current IP addresses), and **coming up** shows the warranty end and replace-by date. The card refreshes every minute.

Each entry links to its source. Filters hide or show kinds. Visibility follows the source: an entry that belongs to a ticket in a queue you can't see is counted ("2 more entries belong to tickets in queues you can't see") and never named. Script output and deployment details stay on their own pages.

It only reads what Layer 8 already has; opening it never calls Action1, UniFi or any other system.

API: `GET /api/devices/{id}/timeline` (`?kinds=ticket,change` to limit it).

## Changes as they happen

The Updates, Directory and network entries are recorded by the regular syncs when something **changes**: Layer 8 compares each read with the last one. A device's first sighting is never an event, so connecting an integration doesn't flood timelines. A network event needs the device's MAC address (an IP record linked to the device), and only a reported "online" ends an outage, never an unknown state. Events are kept for two years.
