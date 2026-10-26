# Ticket workspace: Option B

The default technician ticket view puts Conversation first. Work contains child tickets, checklists, expenses and changes; History retains the existing audit trail. Status, priority, SLA, requester, queue and assignee remain in the ticket header. Status & assignment opens Details.

Tools opens a docked drawer: Details (status, assignment, requester and actions), Devices (computer, linked devices and systems), Directory, Scripts, Network, Copilot, Library, Files, and Guest Wi-Fi when enabled and permitted. Existing forms, permission checks and API actions are reused. Computer script shortcuts open Scripts. Network reads existing ticket device links and cached IPAM/vendor observations; opening it launches no scan or diagnostic.

Activated panels stay mounted while switching tabs/tools or closing the drawer, retaining unfinished forms and running operations. Escape closes Tools and restores focus to its button. Tab lists support Left/Right/Home/End. Reply text, selected files and public/internal choices also survive entering the Arrange editor. Drafts are in memory only, scoped to the signed-in user and ticket; navigating away resets them. Other tool forms can reset when entering Arrange, so finish those edits first.

My preferences offers Conversation with tools drawer or Classic arranged panels, preferred tool, and keeping tools open on other tickets. The drawer's width is set by dragging its left edge (or with the arrow keys on that handle; double-click for the usual width), and it's remembered; the conversation always keeps room beside it. Drawer controls save personal presentation preferences automatically, with errors and Retry. Mode changes take effect on the next ticket navigation so active work is not remounted. An omitted `ticket_workspace` in PUT /api/auth/me/preferences preserves it; null resets defaults. No database migration is required. These settings never grant permissions or enable integrations.

Arrange this page retains the existing editor and saved layouts. Drawer mode respects hidden panels while grouping visible panels by purpose; classic mode also retains saved columns and order. Optional feature/permission gates continue to apply. The layout uses existing theme tokens, including light, Midnight, Ember and Ultraviolet; it does not change the tenant theme or introduce a Carbon color preset. Below 1200px the drawer stacks above the conversation and scrolls internally.

## Related open tickets

The **Work** tab lists up to five open tickets that may be the same problem, each with why it was suggested:

- **Same device / system / VM:** both tickets are linked to it.
- **Subject shares:** at least two meaningful words in the subjects (common words like "printer not working" don't count on their own).

Only open tickets in queues you may see are considered, so a restricted queue's tickets never show, not even as a count. The ticket itself, its parent, its children and its siblings are left out.

Nothing is linked automatically. **Part of this ticket** makes the other ticket a child of this one, and **This is part of #N** does the reverse. Each asks first, then uses the usual child-ticket link, with its loop checks and history. To tell everyone affected, reply on each ticket as usual.

API: `GET /api/tickets/{id}/related` (staff).

## Library

The Library tool retains local KB articles, solved-ticket suggestions and article actions. When configured, it also offers explicit SharePoint runbook/FAQ page search and reviewed internal ticket references. The saved tool ID remains `knowledge`. See [Library setup and access](library.md).

## Update everyone affected

When several reports are one problem, link them under one ticket as its child tickets (from **Related open tickets**, or **Add a child ticket**). Then, from that ticket's **Child tickets** panel, **Update everyone affected**:

1. **Write the update.** Only this text is sent; internal notes and other tickets' contents never are.
2. **Optionally tick Resolve them all,** choosing a resolution type and closure reason if your setup requires them.
3. **Preview.** It lists every ticket included (this one and its open child tickets you may see), each requester, whether they're emailed, and, when resolving, which tickets are resolved. The preview really runs the update and then undoes it, so anything that would block it (a missing resolution type, for example) shows here. Nothing is sent.
4. **Send.** Each ticket gets the update as its own public reply, in its history. A requester with several of these tickets gets one email. If resolved emails are on, they also get the usual resolved email.

Child tickets in queues you can't see are left out, and the preview doesn't count or name them. If one of them is still open, this ticket stays open when you resolve the rest. Changing the message or the ticket list after a preview needs a new preview.

API: `POST /api/tickets/{id}/broadcast/preview`, then `POST /api/tickets/{id}/broadcast` with the preview's `fingerprint` and `"confirm": true`.
