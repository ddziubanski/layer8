# Meraki webhook visual review

These captures use the real React pages with synthetic local API fixtures and the
backend's actual Meraki settings field descriptions. No tenant credentials or
live Meraki deliveries were used; this is not provider acceptance testing.

- `layer8-meraki-webhooks-desktop.jpg`: receiver URL, intake status and recent
  created/recovery/stale delivery outcomes with ticket links.
- `layer8-meraki-webhooks-settings.jpg`: opt-in checkbox, organization allowlist,
  fallback queue and blank write-only secret field, alongside existing discovery
  and authentication settings. No credential was entered in the browser.
- `layer8-meraki-webhooks-tablet-failure.jpg`: dark tablet layout with a simulated
  history-read failure. The status says "Status unavailable", rather than
  incorrectly claiming intake is disabled. Page/scroll widths were both 1022 CSS
  pixels; desktop widths were both 1447 CSS pixels (no horizontal overflow).

Backend tests separately cover authentication, source allowlists, bounded streaming,
concurrent deliveries, retries, timestamp ordering, routing/SLA/audit behavior,
internal recovery notes, rollback, retention/capacity and migration compatibility.
