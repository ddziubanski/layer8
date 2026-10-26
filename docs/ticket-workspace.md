# Ticket Focus workspace

Ticket Focus is the standard technician design. Helpdesk, People & devices,
Operations, Library and Setup are workspaces across the top. Each contains only
its available navigation items. Existing global module switches and personal
navigation choices continue to apply; a workspace with no visible destinations
is omitted. Tickets and administrator recovery access remain available.

Tickets use **Conversation**, **Details**, **Tools**, **Work** and **History**.
Conversation contains replies, internal notes, attachments on messages and Teams
reply controls when configured. Details contains status, assignment, requester
and ticket actions. Tools groups Devices, Directory, Scripts, Network, Copilot,
Library, Files and Guest Wi-Fi when available. Work contains child/related tickets,
checklists, expenses, time and changes. History retains the audit trail.

A compact **At a glance** panel summarizes the requester, assignment, status and
priority, with shortcuts into the same existing ticket controls. On narrow
screens it moves below the main content. Header SLA and ticket metadata remain.

Activated tab and tool contents remain mounted while hidden. Unsent replies,
selected attachments and in-progress forms survive switching sections. Drafts
remain in memory, scoped to the account and ticket; leaving the ticket can reset
unsaved work. Primary tabs support Left/Right/Home/End keyboard navigation.
Tool choices save automatically with errors and Retry. A selected device's script
shortcut opens Scripts. Merely opening a tool never launches a job or scan.

The old widget canvas, Arrange editor and drawer layout choices are no longer
shown on tickets. Existing saved layouts/modes remain stored and accepted by the
API for compatibility; they do not restore an old ticket layout or conceal a
Ticket Focus tool. Arrangements on other pages are unchanged. All ticket actions,
integration enablement, module controls and permission checks retain their
existing behavior.

## Personal themes

**My preferences > Appearance** offers Tidepool, Amethyst, Blue Steel, Terracotta
and Graphite Lime. Each supports Light, Dark and Follow my device. Both theme and
appearance can independently follow the organization. Changes are saved to the
technician account and apply immediately after Save across console routes.
They do not modify another technician's preferences or public branding.

**Settings > Branding > Colours** sets the organization palette and dark-mode
default. Tidepool/light is the initial default. Restore default light theme saves
Tidepool/light atomically and keeps uploaded names, logos and background files.
Server-locked settings remain locked. Legacy colour/preset fields remain accepted
by the API/CLI for older clients and existing public branding, but are not
appearance choices in the new technician console.

`PUT /api/auth/me/preferences` accepts a `console_appearance` object:

```json
{"console_appearance": {"palette": "tidepool", "mode": "dark"}}
```

The palette may be one of the five IDs (`tidepool`, `amethyst`, `blue-steel`,
`terracotta`, `graphite-lime`) or null to follow the organization. Mode is
`organization`, `light`, `dark` or `system`. Omitting the field preserves it; null
resets both choices. Requesters cannot change console presentation. Damaged
stored preferences safely fall back to the organization default. No database
migration is needed: these are per-account AppSetting records.

Organization settings are also available through the admin settings API and
installer/CLI settings import:

```json
{"theme": {"console_theme": "tidepool", "dark_mode": "off"}}
```

## Update everyone affected

When several reports are one problem, link them under one ticket as its child tickets (from **Related open tickets**, or **Add a child ticket**). Then, from that ticket's **Child tickets** panel, **Update everyone affected**:

1. **Write the update.** Only this text is sent; internal notes and other tickets' contents never are.
2. **Optionally tick Resolve them all,** choosing a resolution type and closure reason if your setup requires them.
3. **Preview.** It lists every ticket included (this one and its open child tickets you may see), each requester, whether they're emailed, and, when resolving, which tickets are resolved. The preview really runs the update and then undoes it, so anything that would block it (a missing resolution type, for example) shows here. Nothing is sent.
4. **Send.** Each ticket gets the update as its own public reply, in its history. A requester with several of these tickets gets one email. If resolved emails are on, they also get the usual resolved email.

Child tickets in queues you can't see are left out, and the preview doesn't count or name them. If one of them is still open, this ticket stays open when you resolve the rest. Changing the message or the ticket list after a preview needs a new preview.

API: `POST /api/tickets/{id}/broadcast/preview`, then `POST /api/tickets/{id}/broadcast` with the preview's `fingerprint` and `"confirm": true`.
