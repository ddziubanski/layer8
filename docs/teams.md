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
