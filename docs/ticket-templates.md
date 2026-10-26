# Ticket templates

Settings > Helpdesk > Ticket templates holds reusable starting points for new technician tickets. Administrators, or technicians granted **templates_manage**, can create, edit, deactivate and delete them. Ordinary technicians can use active templates; requesters cannot access the template API. A template associated with a restricted queue is offered only to technicians who can see that queue. Templates for inactive queues are hidden from the picker.

A template contains a name, subject, details, category, optional priority and optional active queue. It can suggest an existing checklist. It never stores requester or assignee identities and does not execute scripts, start tasks or send notifications. Do not put credentials or sensitive customer records in shared template text.

## Use a template

1. Open **New ticket**, select **Start from a template**, and review its preview. Selecting or cancelling creates nothing.
2. Choose **Apply template**. It fills empty subject, details and category fields. Existing text and the requester/contact/source choices stay intact. A queue or priority you explicitly selected stays selected, including **Let routing decide**. Otherwise the template supplies its queue and priority defaults.
3. Review and edit the completed form, then choose **Create ticket**. The existing ticket service handles permissions, routing rules, SLA and notifications. Templates do not bypass routing. A suggested checklist is a reminder to start it manually from the created ticket's Work tab.

If the template list cannot load, manual ticket creation remains available. Deactivating or deleting a template does not change tickets already created from it.

## API and installer import

- `GET /api/ticket-templates`: active templates visible to the signed-in technician.
- `GET /api/ticket-templates?all=true`: managers can also see inactive templates for administration.
- `POST /api/ticket-templates`, `PUT /api/ticket-templates/{id}`, `DELETE /api/ticket-templates/{id}`: require `templates_manage`.

The API uses `queue_id` and `checklist_id`. Names must be nonblank and at most 120 characters; subjects 300, details 20,000, categories 100. Unknown fields are rejected. Priority is low, medium, high or critical, or null. Duplicate names are refused without regard to case.

Installer answers use queue and checklist **names**, with the same field limits. Import updates matching template names without regard to case. For example:

```json
{
  "ticket_templates": [
    {
      "name": "New starter laptop",
      "subject": "Prepare a laptop",
      "description": "Model:\nStart date:",
      "category": "Hardware",
      "priority": "high",
      "queue": "Service desk",
      "is_active": true
    }
  ]
}
```

Run `python -m app.cli settings import answers.json` after creating the named queues/checklists. Omitting a field on reimport resets that field to its template default. Full encrypted installation exports and database backups include the template table through the existing transfer/backup mechanisms. Upgrade applies migration `d4a7c2e19b58`; back up before upgrading.
