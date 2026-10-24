# Security

Layer 8 holds tickets, people's details and, for some installs, rights to change Active Directory. Please report security problems privately, not in a public issue.

**How to report:** use GitHub's private vulnerability reporting (the repository's Security tab, then "Report a vulnerability"). Include what you found, how to reproduce it, and which version or commit you tested. You'll get an answer within a few days.

**In scope:** the API and web UI, the relay, the installer and appliance (including the recovery console), and the importers.

**Good to know**
- Secrets (client secrets, keytabs, backup passphrases) are kept as files readable only by the service, and are never shown back in the UI or the API.
- Relays pull work over HTTPS with their own tokens. Values like new-account passwords reach them sealed, for one run.
- Uploads aren't malware-scanned by design. Email is expected to be scanned by Exchange Online before delivery.
