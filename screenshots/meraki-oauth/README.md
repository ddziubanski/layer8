# Meraki OAuth review screenshots

Synthetic browser fixtures using the real React settings page. No real organization,
credentials, provider authorization, or Dashboard calls were used. The connection
return is simulated locally; provider interoperability remains an owner acceptance test.

- `layer8-meraki-oauth-connected.jpg`: desktop connection status, redirect URI and controls.
- `layer8-meraki-oauth-disconnected.jpg`: local disconnect with explicit Dashboard revocation warning.
- `layer8-meraki-oauth-tablet.jpg`: tablet layout and authentication selection.
- `layer8-meraki-oauth-failure-dark.jpg`: dark-theme callback failure and reconnect guidance.

Browser checks also saved API-key mode, verified that the OAuth card disappears,
switched back to OAuth, and verified that both saved secret inputs remain empty.
At the tablet viewport the document width and scroll width were both 1022 CSS px.
