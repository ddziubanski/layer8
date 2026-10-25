# Searching tickets

Type in the **Search tickets** box at the top of the ticket queue, or press **Ctrl+K / ⌘K** and choose **Search tickets for "…"**.

- **What's searched:** every ticket's subject, description and notes, including internal notes and closed history (imported Track-It! tickets too). The scope and queue you've picked still apply. The saved views (All open, Unassigned…) don't, so a closed ticket from years ago is found.
- **Words:** all of them must appear, in any order. Put a phrase in quotes (`"paper jam"`), use `or` for either word (`toner or ink`), and `-word` to leave tickets out (`vpn -cisco`). Words are matched exactly as typed (no stemming), which suits names, hostnames and error codes.
- **Numbers:** a number (or `#number`) also finds that Layer 8 ticket. An old Track-It! number finds the imported ticket, since its description ends with "(Track-It! ticket 12345)".
- **Order:** results are most recently opened first; click a column heading to sort by it. **Clear search**, or choosing a view, goes back to the queue.

Technicians only see tickets in queues they may see. Searching is quick even with tens of thousands of tickets: the subject, description and notes have full-text indexes (`ix_tickets_text`, `ix_comments_text`).

API: `GET /api/tickets?q=<words>` alongside the usual `scope`, `queue_id`, `status`, `sort` and `dir`. With `q`, leave `view` out to search every status.
