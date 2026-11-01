# Live updates

The console no longer waits for its next poll to show a change:
- **Queue:** the queue and its view counts refresh the moment a ticket arrives or changes.
- **Open ticket:** a ticket you have open shows new replies, history and watched conditions as they're saved.
- **Devices and outages:** device pages and outage state follow device events and outages as the syncs record them.

How it works:
- **Notification on commit:** when a change to a ticket (its comments, history or watches), a device event or an outage is committed, the database sends a notification (Postgres `NOTIFY` on `layer8_live`). A change that's rolled back sends nothing.
- **Stream:** each API process holds one listening connection and passes these notifications to open browsers over Server-Sent Events (`GET /api/live`, staff only).
- **Ids only:** a notification carries only a type and an id. The browser then fetches what it shows through the normal API, so permissions stay in one place.
- **Sign-in:** the stream re-checks the sign-in every minute and ends when it's no longer valid.
- **Keep-alive:** a comment goes out every 25 seconds, within the proxy's 300-second read timeout. The proxy already passes the stream through unbuffered.
- **Fallback:** if the stream isn't available, pages keep polling as they did before.

Not yet: showing who else is viewing a ticket.
