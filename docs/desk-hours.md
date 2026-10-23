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
