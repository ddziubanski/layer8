# Ticket timers

Technicians can time their work on a ticket instead of typing minutes.

- **Start timer** is in the ticket's action bar and in the Time panel (Work tab). Each technician has their own timer per ticket, and can run timers on several tickets at once.
- While any of your timers runs, an **Active ticket timers** bar sits at the top of every console page, with a live clock for each one, a link to the ticket and **Stop**.
- **Stop** logs the time as an ordinary time entry: whole minutes, rounded up, at least one, under your name and dated today. From the Time panel, the note typed there goes with it. Totals, the Time spent report and "At a glance" (Time spent, with "timing now" while a timer runs) include it.
- **Closing rule:** when a ticket is resolved, closed or cancelled, every timer still running on it stops and its time is logged, with a note saying so. A closed ticket can't be timed until it's reopened.
- **Forgotten timers:** one entry is at most 24 hours. A longer timer is capped and its note says so; edit the entry if needed.

API: `POST /api/tickets/{id}/timer` (start), `POST /api/tickets/{id}/timer/stop` (`{"note": ""}`), `GET /api/me/timers`. `GET /api/tickets/{id}/time` also lists the timers running on the ticket.
