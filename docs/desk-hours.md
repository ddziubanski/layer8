# Desk hours and Teams status

The portal shows, for each queue requesters can choose, whether someone is in: **Open until 17:00**, or **Closed: opens Monday at 08:00** with your after-hours message. It can also show each queue's technicians with their Microsoft Teams status: available, busy, away or offline. It shows status only: there is no button or link to chat or call.

## Hours

**Settings > Desk hours** holds the desk's hours; a queue can have its own in **Settings > Queues > Opening hours** (blank: the desk's). Write one line per set of days, 24-hour clock, the server's time zone:

```
Mon-Fri 08:00-17:00
Sat 09:00-12:00
Tue 08:00-12:00, 13:00-17:00
Sun 00:00-24:00
```

- Days not listed are closed.
- A later line replaces earlier hours for its days. Above, Tuesday has a lunch break.
- Stretches that touch join up, so `Fri 20:00-24:00` and `Sat 00:00-06:00` read as one night shift.

**Closed days** (Settings > Desk hours) apply to every queue, one per line, a date and an optional name:

```
2026-12-25 Christmas Day
2026-12-26 Boxing Day
```

**After-hours message**: shown for a queue that is closed. `{next}` becomes when someone is next expected ("tomorrow at 08:00", "on Monday at 08:00", "on 2 January at 08:00") and `{queue}` the queue's name. Requesters can still send a request after hours; the message says so.

All of these are ordinary settings, so they can go in an answers file (`hours.desk_hours`, `hours.desk_closed_days`, `hours.after_hours_message`, `hours.presence_on_portal`) and `HELPDESK_DESK_HOURS` and the like override them.

## Teams status

Turn on **Settings > Desk hours > Show technicians' Teams status on the portal**. The people shown for a queue are its owners (Settings > Queues > Owners).

It reads Microsoft Graph with the app registration on **Settings > Email** (tenant ID, client ID, client secret). Add these **application** permissions to that registration in the Entra admin center and grant admin consent:

| Permission | Why |
| --- | --- |
| `Presence.Read.All` | Read people's Teams status (`POST /communications/getPresencesByUserId`). |
| `User.ReadBasic.All` | Find each technician's Microsoft 365 account by UPN or email, once. |

The worker job **Teams: technicians' status for the portal** (Settings > Status) reads everyone's status every minute while the setting is on. The portal shows what it last read; a status older than ten minutes shows as *status unknown*. Teams' finer states fold into four: Available; Busy (including Do not disturb and In a meeting); Away (including Be right back); Offline. A technician with no Microsoft 365 account found shows as unknown.

If Graph refuses, the job's line on Settings > Status says which permission is missing.

## The SLA clock

By default SLA times count only while the ticket's queue is open: its own hours (Settings > Queues), or the desk hours, skipping closed days. A 4-hour SLA on a ticket logged on Friday at 16:00, with Mon-Fri 08:00-17:00, is due on Monday at 11:00. **Settings > Desk hours > SLA clock > Around the clock** makes every minute count instead. With no desk hours set, the clock runs around the clock too.

While a ticket is **Waiting on user**, its SLA clock stops (**Stop the SLA clock while waiting on the user**, on by default). Its due dates are cleared, so the queue, rules, reports and remote displays never count it as at risk or breached. When it's worked again, the time it had left comes back. A change of priority while it waits is taken into account.

SLA chips show the business time left and a small meter of how much of the window is left:
- green while there's time;
- amber once the ticket is at risk (the policy's at-risk percentage);
- red in the last tenth of the window, and once it's breached;
- grey *Paused* while the clock is stopped.

The clock's settings apply to new tickets and to priority changes. Tickets already open keep the due dates they have.

## Tickets nobody has taken

Turn on **Settings > Desk hours > Alert when nobody takes a ticket**. A new or open ticket with nobody assigned for **Not taken for (minutes)** (30 by default, counted in desk hours like the SLA) alerts everyone who could take it, once:
- **Email:** each of the queue's owners, or, for a queue without owners, every technician who can see it. Reply **#assign me** to take the ticket (needs **Settings > Email > Technicians can work tickets by email**). A backlog of more than three at once arrives as one list per person.
- **Teams channel:** one post in the channel of the Settings > Teams workflow, listing the tickets with links. Tickets in restricted queues are never posted there; they're emailed instead.
- **Both.**

The console's top bar shows a red *N not taken* while any are waiting, linking to Unassigned. Each alert is in the ticket's history. Unassigning a ticket starts its wait over.
