# Microsoft 365 integration, step by step

Layer 8 works on its own with local accounts. Everything on this page is optional. Each part can be set up on its own, in any order, and turned off again. This guide covers all of the Microsoft 365 parts in one place; the linked pages go deeper.

| Part | What people get | Microsoft side |
| --- | --- | --- |
| [Email](#1-email-in-and-out) | Tickets from email; replies and notifications sent from the service desk mailbox | App registration **A** (app-only), limited to one mailbox |
| [Teams status on the portal](#2-teams-status-on-the-portal) | Each queue's technicians as available, busy, away or offline (status only) | Two application permissions added to registration **A** |
| [Teams queue summary](#3-teams-queue-summary) | A periodic card with counts in a Teams channel | A Workflows webhook (no app registration) |
| [Technician connections](#4-technician-connections-teams-messages) | Technicians message a requester in Teams from a ticket, as themselves | App registration **B** (delegated) |
| [Library](library.md) | Find SharePoint runbooks/FAQ pages and save reviewed internal ticket references | Delegated Sites.Read.All on registration **B**; optional, no Copilot licence needed |
| [Copilot on tickets](#5-copilot-on-tickets-preview) | Technicians ask Microsoft 365 Copilot from a ticket, as themselves | Seven more delegated permissions on registration **B**; a Copilot licence per technician |

Two registrations keep the two kinds of access apart. **A** acts as the service desk itself (no signed-in person), so it gets only the narrowest permissions. **B** only ever acts as the technician who connected, with their own rights.

**Before you start**

- An account that can create app registrations and grant admin consent in the Microsoft Entra admin center (Cloud Application Administrator or Global Administrator), and an Exchange administrator for step 1.
- Layer 8 reachable over HTTPS at its final address, saved in **Settings > General > Portal address** (needed for registration B's sign-in).
- The Layer 8 server can reach `login.microsoftonline.com` and `graph.microsoft.com` on HTTPS 443. Nothing needs to reach Layer 8 from Microsoft: there are no inbound webhooks.
- Only Microsoft's commercial cloud is supported.

Secrets you paste into Layer 8 are stored as files only the service can read and are never shown again. Put a reminder in your calendar for each secret's expiry date: paste the new one before the old one runs out.

---

## 1. Email in and out

Full detail and the `Tickets Anywhere` email commands: [email-setup.md](email-setup.md).

1. **Mailbox.** In the Exchange admin center, create a shared mailbox, e.g. `helpdesk@example.com`.
2. **Registration A.** Entra admin center > **App registrations > New registration**:
   - Name: `Layer 8 service desk` (any name).
   - Supported account types: **Accounts in this organizational directory only** (single tenant).
   - No redirect URI.
   
   Then:
   - From **Overview**, note the **Application (client) ID** and **Directory (tenant) ID**.
   - From **Enterprise applications** > this app, note its **Object ID**.
   - Under **Certificates & secrets > New client secret**, choose an expiry and copy the **Value**, not the Secret ID.
3. **Don't** add `Mail.*` permissions under *API permissions*: in Entra those cover every mailbox in the tenant. Instead, Exchange grants them for the one mailbox (Exchange Online PowerShell):

   ```powershell
   Connect-ExchangeOnline
   New-ServicePrincipal -AppId <client-id> -ObjectId <enterprise-app-object-id> -DisplayName "Layer 8 service desk"
   New-ManagementScope -Name "Layer 8 mailbox" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'helpdesk@example.com'"
   New-ManagementRoleAssignment -App <client-id> -Role "Application Mail.ReadWrite" -CustomResourceScope "Layer 8 mailbox"
   New-ManagementRoleAssignment -App <client-id> -Role "Application Mail.Send" -CustomResourceScope "Layer 8 mailbox"
   Test-ServicePrincipalAuthorization -Identity <client-id> -Resource helpdesk@example.com   # InScope: True
   ```
4. **Layer 8: Settings > Email.** Fill in the mailbox, tenant ID, client ID, client secret and the domains to accept mail from. Save.
5. **Test.**
   - **Test connection** signs in and reads the Inbox, sending nothing.
   - Send an email to the mailbox, then press **Check mailbox now**: it becomes a ticket, and the acknowledgement goes out.

| Problem | Meaning |
| --- | --- |
| `AADSTS7000215` | Wrong secret: paste the secret's **Value**, not its ID. |
| `AADSTS700016` | Wrong client ID or tenant ID. |
| `403` reading the mailbox | The mailbox isn't in the management scope: re-check step 3 (`Test-ServicePrincipalAuthorization`). |
| Mail from some people is rejected | Their domain isn't in *Accept email from*. |

## 2. Teams status on the portal

The portal can show, for each queue, its owners with their Teams status. It shows a dot and a word only, with nothing that starts a chat or a call. Hours and the after-hours message are covered in [desk-hours.md](desk-hours.md).

1. **Registration A > API permissions > Add a permission > Microsoft Graph > Application permissions**, add:

   | Permission | Why |
   | --- | --- |
   | `Presence.Read.All` | Read Teams status for the technicians shown (`POST /communications/getPresencesByUserId`). |
   | `User.ReadBasic.All` | Find each technician's Microsoft 365 account once, by UPN or email. Basic profile only. |

2. Press **Grant admin consent for <your organization>**. Both rows should show a green *Granted* status.
3. **Layer 8: Settings > Queues**: give each portal queue its owners, the technicians to show.
4. **Layer 8: Settings > Desk hours**: tick **Show technicians' Teams status on the portal**. Save.
5. **Test.** Open **Settings > Status**, find **Teams: technicians' status for the portal** and press **Run now**. It should say "Teams status read for N of N technician(s)". Then open the portal: each queue lists its technicians with a status.

| Problem | Meaning |
| --- | --- |
| "Graph refused the user lookup" | `User.ReadBasic.All` is missing or not consented. |
| "Graph refused to read presence" | `Presence.Read.All` is missing or not consented. |
| A technician shows *status unknown* | No Microsoft 365 account matched their UPN or email, or the job stopped. A status older than ten minutes shows as unknown. |
| "Waiting for the app registration" | Settings > Email isn't filled in. |

## 3. Teams queue summary

A card with open, unassigned, SLA-risk and waiting-on-user counts, posted to a channel on a schedule. Counts only; restricted queues are left out. Detail: [teams.md](teams.md).

1. In the Teams channel: **⋯ > Workflows** > the template **Post to a channel when a webhook request is received**. Choose the team and channel, and finish the wizard.
2. Copy the workflow's URL. Add a co-owner to the workflow, so it keeps working if its creator leaves.
3. **Layer 8: Settings > Teams**: paste the URL into *Workflows webhook URL*, set the interval (15 to 1440 minutes) and tick **Send Teams queue summaries**. Save.
4. **Test.** **Settings > Status > Teams: queue summary > Run now**, then look in the channel.

If your tenant blocks anonymous webhook triggers, this part can't be used yet.

## 4. Technician connections (Teams messages)

Each technician links their own Microsoft work account. They can then send the requester a Teams message from a ticket, and it's saved as a public reply. Detail: [teams.md](teams.md#send-ticket-messages-as-a-technician).

1. **Registration B.** Entra admin center > **App registrations > New registration**:
   - Name: `Layer 8 technicians`.
   - Supported account types: **single tenant**.
   - Redirect URI: platform **Web**, `https://<your portal address>/teams/callback`. It must match Settings > General > Portal address exactly: HTTPS, no path or trailing slash.
   - Note the client ID and tenant ID, and make a client secret.
2. **API permissions > Microsoft Graph > Delegated permissions**, add `User.Read`, `Chat.ReadBasic` and `ChatMessage.Send`. Grant admin consent, or leave it to each technician if your tenant allows user consent.
3. **Layer 8: Settings > Teams**: tenant ID, client ID, client secret. Tick **Allow technician Teams connections**. Save.
4. **Each technician: My preferences > Microsoft Teams > Connect**. Sign in with the work account whose email or UPN matches their Layer 8 account. MFA and consent prompts follow your tenant's policies.
5. **Test.** On a ticket, **Message requester in Teams**: pick the one-to-one chat with the requester, write, send. The message appears in Teams, and the reply appears on the ticket.

| Problem | Meaning |
| --- | --- |
| `AADSTS50011` (redirect URI mismatch) | The redirect URI in registration B differs from the portal address + `/teams/callback`. |
| "Choose the Microsoft work account matching…" | The account's email/UPN isn't the technician's Layer 8 email or UPN. |
| No chats listed | There's no one-to-one chat with the requester yet: start one in Teams, then refresh. |
| "Teams registration changed; reconnect" | Tenant, client or portal address changed: each technician connects again. |

## 5. Copilot on tickets (preview)

Technicians ask Microsoft 365 Copilot from a ticket's **Copilot** panel, as themselves. They can review and include the ticket's subject, description and latest public replies as context. Internal notes require the separate **Also include internal notes** choice. Copilot answers from what that technician can already see in Microsoft 365, with links to its sources. For each answer, **Include this response on the ticket** prepares an editable internal note. **Send to requester** instead prepares an editable public reply; a separate **Send reply** button uses the existing ticket/email workflow. Nothing is saved or sent automatically. Each question is noted in the ticket's history; the answer is saved only if the technician chooses.

> Microsoft's Copilot Chat API is in **beta**. Microsoft says it isn't supported for production use, and it may change without notice. Treat this part as a preview.

Requirements:

- Part 4 set up (technician connections).
- A **Microsoft 365 Copilot** licence for each technician who will use it.

Steps:

1. **Registration B > API permissions > Microsoft Graph > Delegated permissions**, add all seven (Microsoft requires every one of them for the Chat API):

   | Permission | Lets Copilot use |
   | --- | --- |
   | `Sites.Read.All` | SharePoint and OneDrive files the technician can open |
   | `Mail.Read` | The technician's own mail |
   | `People.Read.All` | People relevant to the technician |
   | `OnlineMeetingTranscript.Read.All` | Meeting transcripts the technician can see |
   | `Chat.Read` | The technician's Teams chats |
   | `ChannelMessage.Read.All` | Teams channel messages the technician can see |
   | `ExternalItem.Read.All` | Copilot connector content (e.g. an indexed wiki) |

   Grant admin consent. These are delegated permissions, so Copilot never sees more than the technician could already open themselves.
2. **Layer 8: Settings > Teams**: tick **Copilot on tickets (preview)**. Save.
3. **Each technician: My preferences > Microsoft Teams > Disconnect, then Connect** once, to pick up the new permissions. Until they do, the Copilot panel says *Reconnect your Microsoft account*.
4. **Test.** On a ticket, in the **Copilot** panel, ask "Summarize this ticket" with *Include this ticket's text* ticked. Choose **Review ticket context** and inspect the exact bounded text, then click **Ask**. You should get an answer, and the history shows "Asked Copilot, with this ticket's text".

| Problem | Meaning |
| --- | --- |
| "Copilot refused: your account needs a Microsoft 365 Copilot licence…" | No Copilot licence, or one of the seven permissions isn't consented. |
| "Reconnect your Microsoft account…" | The technician connected before Copilot was turned on: disconnect and connect again. |
| "Copilot is busy" | Microsoft is throttling: try again in a minute. |

**What leaves Layer 8:** the question, plus the reviewed ticket context when included, capped at 12,000 characters. Context includes the subject, status, priority, requester's name, description and latest ten public replies by default. Internal notes are included only through their separate choice; attachments are never included. The preview is generated locally and makes no Microsoft call. The UI supplies its preview digest; if the source text changes before sending, the API refuses the stale preview and asks you to refresh it. Existing API callers can omit the optional digest, but internal notes now default to excluded.

A conversation is bound server-side to the technician, ticket, Microsoft account and app registration. Follow-ups recheck ticket access. Changing sharing choices starts a new conversation because removing a checkbox cannot erase context Microsoft already received. Switching ticket/account clears the visible conversation. **Start over** clears this view; it does not delete Microsoft 365 conversation history.

**Save or send an answer.** After reviewing an answer, select **Include this response on the ticket** and edit the response. Without **Send to requester**, **Add as an internal note** saves it privately to staff, including its source links. With **Send to requester** selected, **Send reply** creates a public reply and requests email delivery through the existing workflow. Only the edited reply is sent, without automatically appending the Copilot question or its source list. The UI distinguishes email queued from portal-only saving; queued email is not proof of delivery. Check the ticket before retrying an uncertain save. No response is auto-saved, emailed or posted to Teams.

**Upgrade note.** A migration adds conversation authorization metadata only, not questions, answers or tokens. Conversations started before this upgrade have no binding record: start a new conversation once. Existing saved ticket notes/replies are unaffected.

---

## Turning things off and revoking access

- **Email:** clear the mailbox in Settings > Email. Remove the role assignments with `Remove-ManagementRoleAssignment` if retiring.
- **Teams status:** untick it in Settings > Desk hours, then remove the two permissions from registration A.
- **Queue summary:** untick it, and delete the workflow in Teams.
- **Technician connections and Copilot:**
  - Untick them in Settings > Teams.
  - Technicians can **Disconnect** in My preferences, which deletes their stored sign-in.
  - To revoke Microsoft's side, open Entra > Enterprise applications > *Layer 8 technicians* > Permissions, or delete registration B.
- **Moving or retiring a server:** backups include the stored secrets and sign-ins. Restrict who can restore them, and rotate the client secrets afterwards.

## Answers-file keys

Everything above, except the Exchange PowerShell and each technician's own connection, can be set before first boot in an answers file (`python -m app.cli settings import answers.json`):

```json
{
  "email": {"mailbox": "helpdesk@example.com", "graph_tenant_id": "<tenant>", "graph_client_id": "<A client id>", "graph_client_secret": "<A secret>", "mail_allowed_domains": ["example.com"]},
  "hours": {"presence_on_portal": true},
  "teams": {"teams_enabled": false, "teams_chat_enabled": true, "teams_tenant_id": "<tenant>", "teams_client_id": "<B client id>", "teams_client_secret": "<B secret>", "copilot_enabled": false}
}
```

The answers file holds secrets: keep it private, and delete it after use. The installer deletes its own copy.
