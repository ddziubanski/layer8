# Meraki diagnostics visual review

The real React pages use synthetic local API fixtures. No credentials or live
Meraki tenant were used. These images do not establish provider interoperability.

- `layer8-meraki-diagnostics-desktop.jpg`: explicit wired and wireless reads,
  cooldowns, shared-port warnings, source event/read/attempt times and truncation.
- `layer8-meraki-diagnostics-tablet.jpg`: two responsive cards at tablet width;
  document and scroll widths both measured 1022 CSS pixels (no horizontal overflow).
- `layer8-meraki-diagnostics-failure-dark.jpg`: a simulated provider failure keeps
  the earlier successful port result and its original read timestamp visible.

Opening either panel first showed only an empty local cache. A separate Read
button was required to produce the fixtures; the resulting cooldown disabled the
button. RSSI is displayed as reported, without inventing units or a health claim.
Backend tests separately validate provider GET contracts and database rate limits.
