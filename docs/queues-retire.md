# Retiring a queue: archive or delete

Settings > Queues > *queue* > **Retire this queue**. Needs the *Queues* permission (admins by default).

## Archive (keeps everything)

**Archive…** makes the queue inactive and hides it from the Settings > Queues list. Its tickets, history and reports keep it; nothing is deleted. Tick **Show archived** under the list to see archived queues, and **Unarchive** to bring one back (it stays inactive until you tick Active and save). The default queue can't be archived.

## Delete (permanent)

Delete takes a deliberate extra step: untick **Active** and **Save changes** first. Only then is **Delete queue…** enabled.

The dialog then shows:

- **What still uses the queue.** A routing rule, a ticket template, canned replies, another queue's escalation target or category routing all block the delete until they're changed. It never quietly rewires them.
- **Its tickets.** If it has any, choose an active queue to move them to. Open tickets are moved as usual, with an internal note. Closed tickets keep who worked them, and their history records the move.
- **A confirmation.** Type the queue's name.

The queue's owners, managers, viewers and portal options go with it. Wallboards stop showing it. The deletion is written to the system log. The default queue can't be deleted.

API: `POST /api/queues/{id}/archive` `{"archived": true|false}`, `GET /api/queues/{id}/delete-check`, `DELETE /api/queues/{id}?move_to={queue id}`.
