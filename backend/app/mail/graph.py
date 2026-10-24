# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exchange Online mailbox over Microsoft Graph (app-only, client credentials).

The app registration needs application permissions Mail.ReadWrite and Mail.Send,
limited to the intake mailbox with Exchange RBAC for Applications.
"""

import base64
import logging
import time
from collections.abc import Callable
from datetime import datetime

import httpx
import msal

from app.mail.types import Folder, InboundAttachment, InboundMessage, OutgoingMessage

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
RETRY_STATUSES = {429, 502, 503, 504}
MAX_RETRIES = 5
MESSAGE_FIELDS = "id,internetMessageId,subject,from,toRecipients,ccRecipients,receivedDateTime,internetMessageHeaders,body"


def msal_token_provider(tenant_id: str, client_id: str, client_secret: str) -> Callable[[], str]:
    app = msal.ConfidentialClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant_id}", client_credential=client_secret
    )

    def token() -> str:
        # MSAL caches the token and renews it shortly before it expires.
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(f"Graph sign-in failed: {result.get('error')}: {result.get('error_description')}")
        return result["access_token"]

    return token


class GraphError(RuntimeError):
    pass


MAIL_SETTINGS = ("mailbox", "graph_tenant_id", "graph_client_id", "graph_client_secret")
LABELS = {"mailbox": "mailbox", "graph_tenant_id": "tenant ID", "graph_client_id": "client ID", "graph_client_secret": "client secret"}


def missing(settings) -> list[str]:
    """What Settings > Email still needs before the mailbox can be used."""
    return [LABELS[n] for n in MAIL_SETTINGS if not getattr(settings, n)]


def from_settings(settings) -> "GraphMailbox | None":
    """The configured mailbox; None when email is off (no mailbox) or settings are incomplete."""
    if not settings.mailbox or missing(settings):
        return None
    token = msal_token_provider(settings.graph_tenant_id, settings.graph_client_id, settings.graph_client_secret)
    return GraphMailbox(settings.mailbox, token)


class GraphMailbox:
    def __init__(
        self,
        address: str,
        token: Callable[[], str],
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.address = address
        self._token = token
        self._http = http or httpx.Client(timeout=60)
        self._sleep = sleep
        self._folder_ids: dict[Folder, str] = {}

    @property
    def _base(self) -> str:
        return f"{GRAPH}/users/{self.address}"

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = path if path.startswith("https://") else f"{self._base}{path}"
        headers = kwargs.pop("headers", {})
        for attempt in range(MAX_RETRIES + 1):
            resp = self._http.request(method, url, headers={**headers, "Authorization": f"Bearer {self._token()}"}, **kwargs)
            if resp.status_code not in RETRY_STATUSES or attempt == MAX_RETRIES:
                break
            # Graph throttles with 429 and says how long to wait.
            delay = float(resp.headers.get("Retry-After", 2**attempt))
            log.warning("Graph %s %s returned %s; retrying in %ss", method, path, resp.status_code, delay)
            self._sleep(min(delay, 120))
        if resp.is_error:
            raise GraphError(f"Graph {method} {path} failed: {resp.status_code} {resp.text[:500]}")
        return resp

    def check(self) -> dict:
        """Sign in and read the Inbox's details: proves the app may use this mailbox. Sends nothing."""
        inbox = self._request("GET", "/mailFolders/inbox", params={"$select": "displayName,totalItemCount,unreadItemCount"}).json()
        return {"mailbox": self.address, "inbox_items": inbox.get("totalItemCount", 0), "unread": inbox.get("unreadItemCount", 0)}

    # Reading

    def fetch_inbox(self, limit: int) -> list[InboundMessage]:
        resp = self._request(
            "GET",
            "/mailFolders/inbox/messages",
            params={"$top": limit, "$orderby": "receivedDateTime asc", "$select": MESSAGE_FIELDS},
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        messages = []
        for item in resp.json().get("value", []):
            msg = self._parse(item)
            try:
                msg.attachments = self._attachments(item["id"])
                msg.raw_mime = self._request("GET", f"/messages/{item['id']}/$value").content
            except Exception as exc:
                msg.load_error = f"{type(exc).__name__}: {exc}"
            messages.append(msg)
        return messages

    @staticmethod
    def _parse(item: dict) -> InboundMessage:
        sender = (item.get("from") or {}).get("emailAddress") or {}
        headers: dict[str, str] = {}
        for h in item.get("internetMessageHeaders") or []:
            headers.setdefault(h["name"].lower(), h["value"])
        received = item.get("receivedDateTime")
        return InboundMessage(
            mailbox_id=item["id"],
            # Fall back to Graph's own ID so dedupe still works for odd messages without one.
            internet_message_id=item.get("internetMessageId") or f"<graph-{item['id']}>",
            from_address=sender.get("address", ""),
            from_name=sender.get("name", ""),
            subject=item.get("subject") or "",
            body_text=(item.get("body") or {}).get("content", ""),
            received_at=datetime.fromisoformat(received) if received else None,
            to_addresses=[
                r["emailAddress"]["address"].lower()
                for r in (item.get("toRecipients") or []) + (item.get("ccRecipients") or [])
                if (r.get("emailAddress") or {}).get("address")
            ],
            headers=headers,
        )

    def _attachments(self, message_id: str) -> list[InboundAttachment]:
        # Always asked for: hasAttachments is false when there are only inline images.
        resp = self._request(
            "GET", f"/messages/{message_id}/attachments", params={"$select": "id,name,contentType,isInline,size"}
        )
        result = []
        for att in resp.json().get("value", []):
            if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
                # Attached emails and cloud links: kept in the raw .eml, not extracted.
                continue
            data = self._request("GET", f"/messages/{message_id}/attachments/{att['id']}/$value").content
            result.append(
                InboundAttachment(
                    filename=att.get("name") or "attachment",
                    content_type=att.get("contentType") or "application/octet-stream",
                    data=data,
                    is_inline=bool(att.get("isInline")),
                )
            )
        return result

    # Filing

    def _folder_id(self, folder: Folder) -> str:
        if folder not in self._folder_ids:
            name = folder.value
            found = self._request("GET", "/mailFolders", params={"$filter": f"displayName eq '{name}'"}).json()["value"]
            if found:
                self._folder_ids[folder] = found[0]["id"]
            else:
                self._folder_ids[folder] = self._request("POST", "/mailFolders", json={"displayName": name}).json()["id"]
        return self._folder_ids[folder]

    def move(self, message: InboundMessage, folder: Folder) -> None:
        self._request("POST", f"/messages/{message.mailbox_id}/move", json={"destinationId": self._folder_id(folder)})

    # Sending

    def send(self, message: OutgoingMessage) -> str:
        # Draft first, then send: the draft tells us the Message-ID, which replies will quote.
        body: dict = {
            "subject": message.subject,
            "body": {"contentType": "Text", "content": message.body},
            "toRecipients": [{"emailAddress": {"address": message.to_address}}],
        }
        if message.headers:
            body["internetMessageHeaders"] = [{"name": k, "value": v} for k, v in message.headers.items()]
        if message.attachments:
            body["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": a.filename,
                    "contentType": a.content_type,
                    "contentBytes": base64.b64encode(a.data).decode(),
                }
                for a in message.attachments
            ]
        draft = self._request("POST", "/messages", json=body).json()
        self._request("POST", f"/messages/{draft['id']}/send")
        return draft["internetMessageId"]
