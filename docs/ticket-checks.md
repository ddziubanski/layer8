# Self-checking tickets (Watching)

On a ticket's **Work** tab, the **Watching** card lets a technician wait on a condition that Layer 8 already measures:

| Condition | For | From |
|---|---|---|
| Restart pending clears | A linked device | Action1 |
| Critical updates installed | A linked device | Action1 |
| Action1 agent back online | A linked device | Action1 |
| Enabled in Active Directory | A linked device | Directory sync |
| VM powered on | A linked device matched to a Nutanix VM | Nutanix |
| Account unlocked | The requester's directory account | Directory sync |
| Account enabled | The requester's directory account | Directory sync |

Only conditions that can be measured and aren't true yet are offered.

How watches work:
- **Checking:** the worker's `ticket_checks` job looks every 5 minutes, reading data the syncs already hold. It makes no vendor calls.
- **When it clears:** the ticket's history records it, and the ticket shows **Verify** in the queue.
- **Confirming:** a technician confirms with **Verified, it's fixed**, or chooses **Stop watching**. A ticket never closes by itself.
- **Automatic watches:** Action1's "Patching overdue" tickets watch what they were opened for. [Proactive tickets](proactive-tickets.md) watch their own condition.

API: `GET/POST /api/tickets/{id}/checks`, `POST /api/tickets/{id}/checks/{check}/confirm`, `DELETE /api/tickets/{id}/checks/{check}`. Ticket lists include `check_review`.
