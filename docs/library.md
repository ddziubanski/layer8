# Library: SharePoint runbooks and FAQ pages

Library is the ticket's knowledge tool: the existing local knowledge base plus optional read-only SharePoint search. Local article suggestions, solved-ticket matches, article linking and drafting remain available. The saved tool identifier stays `knowledge`, so existing layouts/preferences still work. The standalone Knowledge base page and APIs keep their names.

## First slice

A technician can explicitly search one approved IT SharePoint site for indexed runbook documents and FAQ pages, open the original source, and review an internal ticket reference. This does not create or publish SharePoint pages, read custom FAQ list rows, download/index files, copy a site into Layer 8, or expose SharePoint content on the requester portal. FAQ pages created on your existing IT SharePoint site can be found once Microsoft Search indexes them and the technician has access.

A Microsoft 365 Copilot licence is not required for this direct Graph search. Microsoft work-account access, tenant consent and SharePoint access are required. Copilot remains a separate optional feature.

## Administrator setup

1. Keep the existing technician Microsoft app registration and connection settings in **Settings > Teams**. This is delegated registration B from [the Microsoft 365 guide](microsoft-365.md), separate from mailbox/presence application credentials and Entra sign-in.
2. Add the Microsoft Graph **delegated** permission `Sites.Read.All` to that registration and grant the required tenant consent. Existing Copilot registrations may already have it.
3. Open **Settings > Helpdesk > Library**. Set **IT SharePoint site**, for example `https://example.sharepoint.com/sites/IT`, and enable **SharePoint Library**. Named `/sites/name` and `/teams/name` sites in the commercial `sharepoint.com` cloud are supported; root sites, personal OneDrive, arbitrary hosts and URL query/fragment values are refused.
4. Each technician opens **My preferences > Microsoft Teams > Reconnect Microsoft account** (or Connect for a new connection). The connected Microsoft account must still match their Layer 8 email/UPN. Existing connections with Copilot grants can already have the required scope.
5. On a ticket open **Tools > Library**, type a search and choose **Search SharePoint**. Opening Library only reads local setup/connection status; it does not search Microsoft automatically.

**Scope distinction:** `Sites.Read.All` is broader than the configured IT site. This implementation constrains its Graph search and independently filters returned URLs to that site; this does not reduce the Microsoft OAuth consent to a single site. Microsoft Search and selected-site permission compatibility differ by endpoint; this slice does not claim to use `Sites.Selected`. Microsoft still applies the connected technician's own SharePoint permissions. No service-account fallback is used.

## What leaves Layer 8

Only the search terms entered into the form are sent to Graph. Ticket subject, requester, description, comments and internal notes are not added automatically. Search terms are treated as literal words rather than user-supplied KQL, with up to twelve terms. The first ten accessible matching results are requested; out-of-site/unsafe results are discarded without exposing their titles or counts. There is no pagination or claim of exhaustive results. Search snippets are rendered as plain text.

Results stay in the mounted technician ticket tool; there is no shared content cache, background worker or persistent search index. The source URL/site and enabled state are ordinary administrator settings, with existing environment locks and installer answers support. OAuth credentials/tokens remain in the existing secret-file store.

## Link a reference

Choose **Link reference to ticket**, review the title and original URL, then choose **Confirm internal reference**. The server rechecks the item through Graph as the technician and verifies that its current URL remains within the approved site. It saves only the current title and URL as an internal ticket note through the normal ticket service/history. No requester reply or email is sent and no SharePoint content is changed. Linking the same title/URL again by the same technician reuses the existing note.

The internal note is visible to other technicians who may see the ticket, even if those technicians cannot open that SharePoint document. Review this disclosure before confirming. SharePoint continues to enforce access when opening the original URL. Internal references are never automatically copied into public KB articles or the portal.

Disabling Library stops searches/linking; existing internal reference notes remain as ticket history. Changing the site clears the displayed source results when setup status refreshes. A revoked/expired grant requires reconnection; a moved/deleted/inaccessible item fails linking without adding a note.

## API and installer answers

- `GET /api/library/status`: local enabled/connection status for a technician; no provider calls.
- `POST /api/tickets/{id}/library/search` with `{"query":"VPN setup"}`: validates ticket access before requesting Graph results.
- `POST /api/tickets/{id}/library/link` with `{"drive_id":"...","item_id":"...","confirm":true}`: requires explicit review and revalidates access before creating the internal reference.

Requesters cannot call these endpoints. Standard ticket visibility, CSRF/session protection and connection-cache locking apply. Graph calls use fixed HTTPS Microsoft endpoints with redirects/proxies disabled, bounded responses and sanitized errors.

```json
{"library":{"library_enabled":true,"library_site_url":"https://example.sharepoint.com/sites/IT"}}
```

Use `python -m app.cli settings import answers.json`; corresponding `HELPDESK_LIBRARY_ENABLED` and `HELPDESK_LIBRARY_SITE_URL` environment overrides lock the fields as usual. No database migration is needed.

## Validation and references

Automated tests use invented sites, users, tokens and Graph responses. A real tenant, its consent policy, indexing delay and FAQ page discovery still need owner validation.

- [Microsoft Search for SharePoint and OneDrive](https://learn.microsoft.com/en-us/graph/search-concept-files)
- [Search request API](https://learn.microsoft.com/en-us/graph/api/search-query?view=graph-rest-1.0)
- [Get a drive item](https://learn.microsoft.com/en-us/graph/api/driveitem-get?view=graph-rest-1.0)
- [Selected permissions overview](https://learn.microsoft.com/en-us/graph/permissions-selected-overview)
