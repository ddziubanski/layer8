# Service notices

Tell people about an outage or a known problem: Settings > Helpdesk > Notices. A notice has a title, a message, a severity (outage, warning or information), an audience (everyone, staff or requesters), a start and an optional end. While it's current it shows once as a pop-up right after sign-in, and as a banner at the top of the console and the portal.

## Turning a notice on and off

- **In Settings > Notices**, each notice has an **On / Off** switch. Off hides it for everyone straight away. Its dates are kept, so switching it back on shows it again for the rest of its time.
- **From the notice itself:** technicians allowed to manage notices see **Turn off for everyone** on the pop-up and the banner. It's the same switch, so the notice can be turned back on in Settings > Notices.
- **Hide**, on the banner, hides it only for you, in this browser tab.

Who may manage notices is set in Settings > Permissions (Notices; admins by default). Every switch is written to the system log with who did it.

API: `PUT /api/notices/{id}/enabled` with `{"enabled": false}` or `{"enabled": true}`.
