# People: contact cards and the person page

Technicians can see who they're helping without leaving the queue.

- **Contact card:** click a requester's name in the ticket queue or in a ticket's header. A card opens instead of the ticket, with:
  - job title and department;
  - email (click to email) and phone (click to call);
  - Teams status, when Teams presence is on;
  - how they sign in, and whether the account is active;
  - their open and total tickets.

  **Escape** or a click elsewhere closes it.
- **Person page** (**View profile**, or `/people/<id>`):
  - the contact details;
  - the devices assigned to them;
  - for AD accounts, their OU and, on request, their AD groups (read live from AD);
  - every ticket they've asked for, newest first, with **All** and **Open** filters and **Show more**. Imported Track-It! history is included.

The details come from what Layer 8 already stores: directory sync for AD accounts, the Track-It! import, or the local account. Manager, office and mobile aren't read from AD yet.

Ticket counts and lists only include queues the technician may see, so a restricted queue's tickets don't show up for someone outside it. Requesters can't open either view.

API: `GET /api/people/{id}` (staff only) returns the card's details, counts and devices. `GET /api/tickets?requester_id=<id>&scope=all` lists a person's tickets in the queues you can see.
