# Teams queue summaries

This optional integration posts a periodic Adaptive Card with open, unassigned, SLA-risk (including breached), and waiting-on-user ticket counts. Counts overlap. It excludes every restricted queue, even for administrators. No ticket subjects, IDs, queue names, requester names, notes, attachments or links are sent.

It starts **off**. No Teams or Entra integration is required for local-only operation. This first slice is a scheduled digest, not individual ticket-event notifications. It sends snapshots even when counts have not changed, including zero counts. A single background worker should run per installation, as with the existing scheduled jobs.

## Configure

1. In the target Teams channel, choose **Workflows** and a webhook-to-channel template accepting Adaptive Cards. Use the **When a Teams webhook request is received** trigger. This integration uses the signed URL without an Entra bearer token; select the trigger's **Anyone** authentication option. Treat that URL as a credential. Tenant policies may prohibit this option; authenticated workflows are not supported in this slice.
2. Assign a co-owner so the flow keeps working when its original owner leaves. Configure the workflow to post received attachments as Adaptive Cards.
3. In Layer 8 **Settings > Teams**, save the generated HTTPS Workflows URL and interval (15–1440 minutes), then enable summaries. Only administrators can configure this section. The URL is stored using the existing secret-file mechanism and never returned by the API.
4. **Settings > Status > Teams: queue summary > Run now** sends an actual summary to the configured channel. Pause the job there or disable it in Settings > Teams to stop delivery.

The first scheduled run after enabling may wait until the job's next due time. Run now requests immediate delivery. HTTP acceptance means the workflow accepted the request, not that Teams rendered it; verify the workflow run history and channel. No live delivery is performed by automated tests.

Only Microsoft commercial-cloud Workflows endpoints under `*.environment.api.powerplatform.com` and `*.logic.azure.com` are accepted, with their expected workflow path and HTTPS port 443. Legacy Office connectors, sovereign clouds and custom proxy endpoints are not supported. Redirects and environment proxy settings are disabled. Failures are recorded without the signed URL or response body and are tried again at the next interval; there is no durable message outbox or exactly-once guarantee.

Settings can also be imported through the existing installer/CLI configuration mechanism:

```json
{"teams":{"teams_enabled":false,"teams_summary_minutes":60,"teams_webhook_url":""}}
```

See [Microsoft's current Workflows guidance](https://learn.microsoft.com/en-us/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook) and [webhook workflow setup](https://support.microsoft.com/en-gb/workflows/send-messages-in-teams-using-incoming-webhooks?nochrome=true). Workflows belong to their owners, rather than the channel itself.


## Send ticket messages as a technician

This separate, optional integration connects each technician's Microsoft work account.
Local and AD sign-in continue to work. It does not sign people into Layer 8 through Entra,
and it does not change the Workflows summary above.

1. Register a **single-tenant** Entra application for Teams. Under Authentication add a
   **Web** redirect URI: `https://helpdesk.corp.example/teams/callback`, replacing the host
   with the exact HTTPS portal address saved under Settings > General. The portal address
   must be an HTTPS origin without a path, query or fragment.
2. Add Microsoft Graph **delegated** permissions `User.Read`, `Chat.ReadBasic` and
   `ChatMessage.Send`. MSAL also requests the OpenID/profile/offline-access scopes needed
   for account verification and renewal. Grant consent as required by tenant policies.
   Application/migration permissions are not used. Create an application secret.
3. An administrator saves the tenant ID, client ID and secret under **Settings > Teams**,
   then enables **Allow technician Teams connections**. The secret remains write-only.
4. Each technician opens **My preferences > Microsoft Teams > Connect Microsoft Teams**.
   Choose the work account whose email or UPN matches their Layer 8 account. Microsoft
   may reuse an existing sign-in, request consent, or require MFA under tenant policy.
5. On a ticket, choose **Message requester in Teams**. Select an existing one-to-one chat
   with the requester and compose the text explicitly. If none exists, start it in Teams
   first, then refresh the chat list. Group/channel chats are excluded. At most 1,000 chat
   records (20 pages) are searched; a larger listing fails rather than using partial results.
6. Sending records a public ticket reply without sending a second email, and appends a
   Teams delivery entry to staff history. Existing reply handling updates response/SLA state.
   No internal note, attachment, subject, history or existing conversation is copied into
   the outbound message. Replies arriving in Teams are **not synchronized back yet**.

The browser receives an authorization code in a URL fragment, clears it, then submits it
through the normal CSRF-protected API. Codes do not enter HTTP access-log URLs. MSAL uses
PKCE; the pending flow expires in ten minutes, is tied to the current Layer 8 session,
and is consumed once. Starting another connection replaces the pending flow. OAuth token
caches are server-side secret files with mode 0600, one per technician, and never returned
through the API. Changing the tenant/client/portal address requires reconnection.

A durable `teams_deliveries` record prevents replay of the same send identifier. An
unconfirmed request is not retried automatically: check Teams before preparing another
send. A crash after Microsoft accepts a message can leave a pending/uncertain delivery
without a public ticket reply; reconcile it manually. This is not an exactly-once delivery
claim. A database migration adds the delivery-intent table; ticket history stays append-only.

**Disconnect Teams** deletes the local token cache and pending flow. It does not remove
Microsoft's consent grant or retract messages already sent; manage consent in Microsoft
if needed. Disabling the feature blocks all new access. Token files are included in the
existing encrypted full backup's secrets; restrict restore access and reconnect/revoke
accounts when moving or retiring an installation.

Only the Microsoft commercial cloud is supported. The API server needs outbound HTTPS to
`login.microsoftonline.com` and `graph.microsoft.com`; it needs no inbound Microsoft webhook
for this slice. Local tests mock Microsoft endpoints. Live tenant sign-in, consent, MFA and
message delivery require validation with an administrator-configured application.

References: [authorization code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow),
[list chats](https://learn.microsoft.com/en-us/graph/api/chat-list?view=graph-rest-1.0),
[send a chat message](https://learn.microsoft.com/en-us/graph/api/chat-post-messages?view=graph-rest-1.0).
