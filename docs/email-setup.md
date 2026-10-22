# Email intake setup (Exchange Online)

The worker reads one shared mailbox through Microsoft Graph and sends ticket email from it. Access is app-only (no signed-in user), limited to that mailbox.

> Check these steps against Microsoft's current docs for "RBAC for Applications in Exchange Online" before running them; cmdlet details change.

## 1. Mailbox

Create a shared mailbox, e.g. `helpdesk@example.com`. The worker creates the `Processed`, `Rejected` and `Failed` folders itself.

## 2. App registration (Entra admin center)

1. App registrations, New registration, name `Service Desk mail`, single tenant.
2. Note the **Application (client) ID**, **Directory (tenant) ID** and **Object ID** of its enterprise application (service principal).
3. Certificates & secrets, New client secret. Note the value.
4. **Don't** add Mail.* application permissions in Entra. Those apply to every mailbox in the tenant. Exchange grants them below, scoped to one mailbox.

## 3. Scope access to the one mailbox (Exchange Online PowerShell)

```powershell
Connect-ExchangeOnline

New-ServicePrincipal -AppId <client-id> -ObjectId <enterprise-app-object-id> -DisplayName "Service Desk mail"

New-ManagementScope -Name "Service Desk mailbox" `
  -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'helpdesk@example.com'"

New-ManagementRoleAssignment -App <client-id> -Role "Application Mail.ReadWrite" -CustomResourceScope "Service Desk mailbox"
New-ManagementRoleAssignment -App <client-id> -Role "Application Mail.Send"      -CustomResourceScope "Service Desk mailbox"

# Should say InScope = True for the helpdesk mailbox and False for any other.
Test-ServicePrincipalAuthorization -Identity <client-id> -Resource helpdesk@example.com
```

## 4. Configure and test

In the app: **Settings > Email** (admins). Fill in:

- **Service desk mailbox**: `helpdesk@example.com`
- **Directory (tenant) ID** and **Application (client) ID** from step 2
- **Client secret**: the secret's value. It's stored on the server where only the service can read it, and never shown again. Paste a new one here before the old one expires.
- **Accept email from**: one domain per line, e.g. `example.com`

Save, then press **Test connection**. It signs in to Graph and reads the mailbox's Inbox, without sending anything. Errors come back as Microsoft reports them, e.g. `AADSTS7000215` (wrong secret) or `403` (the mailbox isn't in the app's management scope; recheck step 3).

The worker picks up saved changes on its next pass, with no restart. The installer can set the same values with `python -m app.cli settings import` (section `email`). A `HELPDESK_*` environment variable of the same name overrides the app and locks the field.

To try it end to end, send a test email to the mailbox and press **Check mailbox now** (same tab). It reads the Inbox into tickets and sends queued email straight away, and lists what happened to each message. From a shell, `python -m app.worker --once` does the same plus the other background jobs.

Then run it continuously as a service: `python -m app.worker`.

## How mail is handled

- **New email** from an allowed domain (or from an existing active user) becomes a ticket with source `email` and goes through the normal routing rules. Unknown senders from allowed domains are added as requesters.
- **Replies** join a ticket by the `[#1234]` tag in the subject or by In-Reply-To/References. Quoted history is stripped. A reply to a closed ticket opens a new ticket that refers back to it.
- **Rejected** (moved to `Rejected`, no ticket): other domains, inactive users, auto-replies, bounces, bulk mail, and mail from the intake mailbox itself.
- **Failed** (moved to `Failed`): errors while processing. The error is recorded in `inbound_emails.reason`. Move the message back to the inbox to retry; it can't create a duplicate.
- **Outgoing**: an acknowledgement on new tickets and each public agent reply, never internal notes. Mail is queued in `outbound_emails` and retried with backoff for about 3 hours before it's marked failed.

## Working tickets by email (Tickets Anywhere)

Technicians can act on a ticket from their mail client. Put one command per line, starting with `#`, in a reply to the ticket's email. The rest of the message is the reply as usual.

| Command | Does |
|---|---|
| `#close` / `#resolve` (optionally with a resolution type or closure reason, e.g. `#resolve Fixed`) | Closes or resolves the ticket |
| `#reopen`, `#status open\|waiting\|resolved\|closed` | Sets the status |
| `#assign me`, `#assign dcole`, `#assign dcole@example.com`, `#unassign` | Assigns or unassigns |
| `#priority low\|medium\|high\|critical` | Sets the priority |
| `#queue Network` | Moves to another queue (noted, like in the console) |
| `#category Printers` | Sets the category |
| `#internal` | Keeps this reply as an internal note (not sent to the requester) |

In a **new** email to the service desk, a technician can log a ticket for someone else: `#create`, then `#requester rosa@example.com`, plus any of the commands above.

How commands are handled:
- **Who:** commands only count from technicians and admins who can see the ticket's queue. Anyone else's `#` lines are ordinary text.
- **Logging:** each email's actions are noted on the ticket ("By email: assigned to Dana Cole; priority high").
- **Mistakes:** commands that can't be done, such as an unknown command or a missing resolution type, come back to the technician by email.
- **Turning it off:** Settings > Email > *Technicians can work tickets by email*.
