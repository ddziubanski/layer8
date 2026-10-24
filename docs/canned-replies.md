# Canned replies

Saved text technicians insert into a ticket's composer: **Canned reply…** next to *Attach file*. The text is added to what's already written, with the ticket's details filled in. A reply marked *Inserts as a reply to the requester* also ticks *Show to the requester in the portal*; you still choose whether to email it and press send.

**Settings > Canned replies** (every technician):
- **Yours:** only you see and insert them.
- **Shared:** every technician sees them. Adding, changing and removing them needs **Manage shared canned replies** (Settings > Permissions, admins by default).
- **Queue:** for every queue, or one queue (it's offered only on that queue's tickets).

Filled in from the ticket:

| Placeholder | Becomes |
| --- | --- |
| `{first_name}` | The requester's first name |
| `{name}` | The requester's full name |
| `{ticket}` | The ticket number, e.g. #1042 |
| `{subject}` | The ticket's subject |
| `{technician}` | Your name |
| `{queue}` | The ticket's queue |
| `{portal}` | A link to the ticket in the portal (when Settings > General has the portal address) |

Anything else in braces is left as it is.
