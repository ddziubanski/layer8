# Meraki device availability alerts

This opt-in receiver creates normal, audited tickets for `stopped_reporting` alerts and records `started_reporting` recovery as an internal note. It never changes Meraki configuration or automatically closes or reopens tickets. Other alert types are acknowledged without creating tickets. Discovery and OAuth/API credentials are independent of incoming webhooks.

## Setup

1. Set the public HTTPS portal address in Settings > General. Publish the receiver through the supported HTTPS reverse proxy with a publicly trusted certificate. Do not expose the backend directly.
2. In Settings > Meraki, save a separate randomly generated shared secret of at least 32 characters, explicit webhook organization IDs and selected network IDs. Empty allowlists accept nothing. Optionally set an active fallback queue ID; 0 uses the default queue. Normal routing rules can choose another queue and priority.
3. Enable **Create tickets from Meraki device outages**. This is off by default, independently of discovery.
4. In Meraki Dashboard > Network-wide > Alerts, add an HTTP server using the receiver URL displayed in Layer 8 (`https://helpdesk.corp.example/api/meraki/webhooks`), the same shared secret and the **default Meraki JSON payload**. Subscribe only to device stopped/started reporting alerts for the selected networks. No custom Liquid template or HMAC signature is required or supported.
5. Validate with a controlled test device and review the accepted delivery history in Settings > Meraki. Provider test messages without a supported availability type do not create tickets. The last 20 accepted supported alerts show their outcome, source time, receipt time and ticket link. This history is admin-only.

Cisco documents the shared-secret payload and HTTPS requirements in [Webhook introduction](https://developer.cisco.com/meraki/webhooks/introduction/), and the exact `stopped_reporting` / `started_reporting` IDs in [sample alerts](https://developer.cisco.com/meraki/webhooks/webhook-sample-alerts/). This receiver uses those exact IDs rather than guessing recovery from a display name or arbitrary alert data.

## Ticket behavior

- A device is identified by organization ID, network ID and serial. An outage opens a high-priority ticket using the normal routing, SLA and event audit services. Its requester is the inactive Service Desk automation account; it does not send an acknowledgement to a real user. Existing administrator-defined on-create/on-update ticket rules still run and can send their configured notifications.
- Further outage alerts reuse an open ticket. A recovery transition adds one internal note. A later outage transition adds an internal note to that same open ticket. Recovery requires technician review; it does not change ticket status. After manual resolution/closure, a newer outage can open a new ticket.
- Each organization/alert ID is accepted once. Retries with a new send time remain duplicates. Reusing that ID with different selected event fields returns 409. A single transaction saves the receipt, device state and ticket or note; rolled-back attempts can be retried.
- Older or equal source timestamps are recorded as stale and cannot change device state. Recovery received before its outage establishes a watermark, preventing the late outage from opening a ticket. Equal timestamps deliberately preserve the first accepted state.
- No provider URL is fetched. Provider display names, URLs, arbitrary alert data, shared secrets and complete payloads are never saved in receipt history or tickets. Ticket text uses bounded IDs and source timestamps only.

## Limits and delivery responses

Only uncompressed JSON, at most 64 KiB (including streamed requests), is accepted. Supported alerts require default payload version 0.1, organization/network/alert IDs, a nonempty device serial and timezone-aware occurrence/send timestamps. Events or deliveries older than seven days are acknowledged without processing; timestamps more than five minutes ahead are refused. Keep server time synchronized.

Authenticated accepted events are limited across the server to 60 new receipts/minute. Duplicate retries succeed even at that limit. Transaction locking serializes intake across API processes; a busy receiver returns 503 with `Retry-After: 5`, and rate limits return 429 with `Retry-After: 60`. Successful/ignored deliveries return an empty 204, never ticket identifiers. Authentication failures return 401, disallowed sources 403, malformed JSON 400, invalid supported payloads 422 and unsupported encodings/content types 415. A disabled or incompletely configured receiver returns 503.

Receipt history is retained for eight days, device incident state for 90 days, with pruning on new accepted deliveries. Hard caps are 100,000 receipts and 10,000 device states; capacity exhaustion refuses new events with 503 rather than discarding replay protection. Recovery after state expiry may be unmatched. This slice does not include an alert inbox for every Meraki alert type, automatic receiver registration, delivery retry scheduling or automatic ticket closure. Review Dashboard receiver health after extended outages; provider retries are controlled by Meraki.

## API, CLI and deployment

- Receiver: `POST /api/meraki/webhooks`, authenticated by the required body `sharedSecret`; user cookies/API keys do not authorize it.
- Admin history/setup status: `GET /api/meraki/webhooks/status`.
- Admin settings: `GET/PUT /api/settings/meraki`. The webhook secret is write-only and stored through the existing 0600 secret-file mechanism.
- Installer/import: `python -m app.cli settings import answers.json`. All four new settings are supported, with secret-file references recommended:

```json
{
  "meraki": {
    "meraki_webhook_enabled": false,
    "meraki_webhook_secret": {"file": "/secure/meraki-webhook-secret"},
    "meraki_webhook_organization_ids": ["123456"],
    "meraki_webhook_queue_id": 0,
    "meraki_network_ids": ["N_123456"]
  }
}
```

Migration `c08f42b61e93` follows the diagnostics migration `b97e31a50d82`, adding receipt and incident tables. Downgrade removes this integration history; existing tickets and their audit events remain. Apply migrations before enabling intake. This draft stacks on the discovery, OAuth and diagnostics drafts; it does not validate compatibility with every other pending feature branch.
