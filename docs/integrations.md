# Integration API (for other applications)

Other applications can look up and search Layer 8 tickets and add internal notes to them, for example a server-room sign-in app that links each visit to a ticket. They use an **API key** and the endpoints under `/api/integrations`. Nothing else in the API accepts a key.

## API keys

**Settings > API keys > Make a key** (admins). Name it after what uses it ("MER server room"). The key starts `l8k_` and is shown once: paste it into the other application. Layer 8 keeps only a hash of it.

Each key acts as its own technician account, named after the key ("MER server room (API key)"):
- **Sign-in:** the account is inactive, so it can't sign in and isn't offered for assigning tickets.
- **What it sees:** what a technician sees: every ticket except those in restricted queues. To let it see a restricted queue, add its account to that queue's viewers.
- **Notes:** they appear on the ticket as internal notes, under its name.

**Revoke** stops a key at once. The list shows when each key was last used.

## Endpoints

Send the key as `Authorization: Bearer l8k_...`.

| | |
|---|---|
| `GET /api/integrations/tickets/{ref}` | A ticket by number or label: `1042`, `%231042` (an encoded `#1042`) or `HD-1042`, whatever your ticket number format is. 404 if there's no such ticket or the key can't see it. |
| `GET /api/integrations/tickets?q=MER ENTRY` | Tickets whose subject contains all the words, newest first. Open tickets only unless `open_only=false`; `limit` defaults to 20 (100 at most). |
| `POST /api/integrations/tickets/{ref}/notes` | `{"text": "..."}` adds an internal note. |

A ticket comes back as `{id, label, subject, status, priority, category, queue, assignee, requester, created_at, url}`. `url` opens it in Layer 8.

Example:

```sh
curl -H "Authorization: Bearer $KEY" https://helpdesk.corp.example/api/integrations/tickets/HD-1042
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"text": "Server room: J. Park in 09:14, out 09:52"}' \
     https://helpdesk.corp.example/api/integrations/tickets/HD-1042/notes
```
