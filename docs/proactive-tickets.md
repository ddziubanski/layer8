# Proactive tickets

Settings > Proactive tickets (off by default). When it's on, the worker checks every hour and opens **one ticket per condition** before something lapses:

| Condition | From | Lead time setting |
|---|---|---|
| A device warranty ending | Devices (warranty lookups or entered by hand) | Device warranties ending within (days) |
| A vendor contract ending | Vendors (contract end date) | Vendor contracts ending within (days) |
| A service account expiring | Directory accounts with an expiry date whose account name starts with a configured prefix (e.g. `svc-`) | Service accounts expiring within (days) |
| A runner that stopped checking in | Runners | Runner silent for (hours) |
| Backups failing | The backup job, when backups are set up | No successful backup for (days) |

A lead time of 0 turns that condition off.

How the tickets behave:
- **Routing:** tickets go through the routing rules like any other automated ticket. The requester is the automation account.
- **Linked device:** a warranty ticket links its device.
- **One per condition:** while the ticket is open, the condition doesn't open another.
- **Watching:** each ticket watches its own condition (see [ticket-checks.md](ticket-checks.md)). When the warranty or contract is renewed, the account is extended, the runner checks in, or a backup succeeds, the ticket shows **Verify**. A date that simply passes without a renewal still counts as not fixed.
- **No auto-close:** nothing closes by itself.

Not covered yet: certificate expiry, because Layer 8 doesn't record certificate dates.
