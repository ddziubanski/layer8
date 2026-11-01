# Problem management

Problems are staff-only investigation records for recurring or not-yet-understood incidents. They complement, rather than replace, tickets and the Library.

A problem groups the tickets that report the same underlying issue. Its status tracks the investigation: Open, Investigating, Known error, Resolved or Closed. Technicians can record a description, suspected or confirmed root cause, workaround and resolution, assign an owner, and add internal updates to its chronological thread.

Ticket links are explicit; the app does not infer that two tickets share a cause. Staff must be able to see every linked ticket to read or change the problem. Requesters never see problem records or investigation notes. If a linked ticket is in a restricted queue, technicians without access to that queue cannot see the problem.

Use a problem record to coordinate investigation and preserve operational context. Publish reviewed, reusable instructions as a Library article when appropriate. Similar-ticket and article suggestions in the ticket workspace are a separate follow-on workflow.

## From the ticket

A ticket's **Work** tab has a **Problems** panel. It shows the problems the ticket belongs to, with each one's status, workaround and root cause, so the next technician sees what to do without leaving the ticket. **Link to a problem…** searches problems by title, cause or workaround. **Create problem from this ticket** opens a new problem with the ticket's subject and description, already linked. Linking and unlinking from either side is written to the ticket's history by problem number only, because someone who can see the ticket may not be allowed to open the problem. A problem linked to a ticket you can't see is counted ("also part of 1 problem you can't open"), never named.

## Deleting a problem

**Delete problem…** on the problem page needs the *Delete problems* permission (admins by default; Settings > Permissions). Its linked tickets stay, and each one's history says the problem was deleted. To keep the record, set the status to Closed instead.

API: `GET/POST /api/tickets/{id}/problems`, `DELETE /api/tickets/{id}/problems/{problem_id}`, `DELETE /api/problems/{id}`.
