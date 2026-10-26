# SPDX-License-Identifier: AGPL-3.0-or-later
"""Remote assistance from Action1's public API 3.1.0 (checked 2026-10-25).

Original implementation; no PSAction1 code. External POSTs are never retried.
Session URLs are credentials: return only to the launching technician, never persist.
"""
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import select

from app import action1
from app.config import settings
from app.models import Device, DevicePatch


class RemoteError(action1.Action1Error):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise RemoteError("Action1 returned an invalid session or endpoint identifier")
    return value


def configuration():
    if not settings.action1_enabled or not settings.action1_remote_enabled:
        raise RemoteError("Action1 remote desktop is off (Settings > Action1)")
    if not (settings.action1_remote_client_id and settings.action1_remote_client_secret and settings.action1_remote_org_ids):
        raise RemoteError("Save remote desktop API credentials and allowed organization IDs (Settings > Action1)")
    if settings.action1_region not in action1.HOSTS:
        raise RemoteError("Choose a supported Action1 region")
    return hashlib.sha256(json.dumps([settings.action1_region, settings.action1_remote_client_id,
        settings.action1_remote_client_secret, sorted(settings.action1_remote_org_ids)]).encode()).hexdigest()


def target(session, device_id):
    configuration()
    device = session.get(Device, device_id)
    row = session.scalar(select(DevicePatch).where(DevicePatch.device_id == device_id, DevicePatch.in_action1))
    if device is None or not device.is_active or row is None:
        raise RemoteError("Choose an active device matched to an Action1 endpoint")
    if not row.organization_id or row.organization_id not in settings.action1_remote_org_ids:
        raise RemoteError("This device's Action1 organization is not allowed for remote desktop; refresh its mapping or review Settings > Action1")
    identifier(row.organization_id)
    identifier(row.action1_id)
    return device, row


def launch_url(value):
    try:
        if not isinstance(value, str) or len(value) > 4096 or re.search(r"\s|[\x00-\x1f]", value):
            raise ValueError()
        url = urlsplit(value)
        host = url.hostname or ""
        trusted = host in ("remote.action1.com", "remote.app.action1.com") or re.fullmatch(r"[a-z0-9-]+\.remote\.app\.action1\.com", host)
        if (url.scheme != "https" or not trusted or url.username or url.password or url.port not in (None, 443)
                or url.fragment or url.path not in ("/remote", "/remote/") or not parse_qs(url.query).get("id")):
            raise ValueError()
    except (ValueError, TypeError):
        raise RemoteError("Action1 returned an unsupported remote desktop URL; no link was opened") from None
    return value


class Client(action1.Client):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._http.close()

    def request(self, method, path):
        try:
            token = self._auth()
        except (action1.Action1Error, httpx.HTTPError, ValueError, KeyError, TypeError):
            raise RemoteError("Action1 authentication failed; check the remote desktop API credentials") from None
        try:
            response = self._http.request(method, self.base + path,
                headers={"Authorization": f"Bearer {token}"},
                **({"json": {"connection_type": "assistance"}} if method == "POST" else {}),
                timeout=15, follow_redirects=False)
        except httpx.HTTPError:
            raise RemoteError("Action1 could not be reached. A remote request may have been accepted; check Action1 before starting another.", uncertain=method == "POST") from None
        if response.status_code >= 300:
            raise RemoteError(f"Action1 remote desktop answered HTTP {response.status_code}. Check API permissions and endpoint availability.", uncertain=method == "POST" and response.status_code >= 500)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except ValueError:
            raise RemoteError("Action1 returned an unreadable remote-session response", uncertain=method == "POST") from None

    def endpoint(self, org, endpoint):
        return self.request("GET", f"/endpoints/managed/{identifier(org)}/{identifier(endpoint)}")

    def start(self, org, endpoint):
        return self.request("POST", f"/endpoints/managed/{identifier(org)}/{identifier(endpoint)}/remote-sessions")

    def poll(self, org, endpoint, session_id):
        return self.request("GET", f"/endpoints/managed/{identifier(org)}/{identifier(endpoint)}/remote-sessions/{identifier(session_id)}")


def client():
    configuration()
    return Client(action1.HOSTS[settings.action1_region], settings.action1_remote_client_id,
                  settings.action1_remote_client_secret, httpx.Client(timeout=15, follow_redirects=False))
