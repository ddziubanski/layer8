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
