# Moving from Spiceworks

Layer 8 can take over the ticket history from **Spiceworks Cloud Help Desk**. It's a good fit for a team that's moving across as its own queue, such as a maintenance department.

## 1. Export from Spiceworks

In Spiceworks Cloud Help Desk, open **Tickets**, show all of them (open and closed), and use **Export** to download the CSV.

That export has one row per ticket: number, summary, description, assigned to, category, department, priority, status, created and closed dates, created by, due date and time spent. **It has no comments or attachments,** so those stay in Spiceworks. Each imported ticket keeps a link back to its Spiceworks page.

The file holds your real data: copy it to the appliance on site, and delete it once the import is done.

## 2. Make the queue

Create the queue the tickets go to first (Settings > Queues), for example **Maintenance**, with its technicians as owners. To keep it separate from IT:

- tick **Restricted** on Maintenance, so only its owners, managers and viewers (and admins) see its tickets;
- tick **Restricted** on IT's queue too, with IT's technicians as its owners, so maintenance technicians don't see IT's tickets either. Technicians see every unrestricted queue.

A portal category that routes to Maintenance lets people ask maintenance directly (Settings > Ticket fields and Ticket rules). Desk hours can be set per queue.

## 3. Import

As the helpdesk user, in /opt/helpdesk/backend:

```sh
.venv/bin/python -m app.cli import-spiceworks /path/ticket_export.csv --queue Maintenance            # dry run
.venv/bin/python -m app.cli import-spiceworks /path/ticket_export.csv --queue Maintenance --apply    # import
```

`--departments add` adds Spiceworks departments that aren't department choices yet. `match` (the default) only uses your existing choices; others are kept on the ticket as an imported detail. `skip` leaves them out.

### What comes across

| Spiceworks | Layer 8 |
|---|---|
| Ticket Number, Summary, Description | Ticket title and description. The description ends with "(Spiceworks ticket 123)", so searching the old number finds it. |
| Created By | The requester: matched to an existing person by email, or by a name only one account has. Others are added as requesters with no password. |
| Assigned To | The assignee, matched the same way. Others are added as **inactive** technicians, so their names stay on old tickets. |
| Status, Priority, Category, Department | Open or closed; high, medium or low; category; department. |
| Created On, Closed On, Due On | Opened, closed and due dates. |
| Time Spent | A time entry on the ticket (the *Time spent* report includes it). |
| Contact Number | The ticket's contact phone. |
| Link to Ticket, Vendor Ticket | Imported details on the ticket. |

- **No side effects:** nothing is emailed, and routing rules, SLA timers and surveys don't run on imported tickets.
- **Running it again:** every ticket is remembered, so importing a later export updates those tickets instead of duplicating them. Handy for a last top-up on cut-over day.
- **Long descriptions:** Spiceworks' export cuts descriptions at 10,000 characters; the import says so on those tickets.
