# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-package Action1 deployments. No automatic retries of external writes.

Original implementation from https://app.action1.com/apidocs/ (API 3.1.0,
checked 2026-10-22). The existing patch client/credentials remain read-only.
"""
import hashlib
import json
import logging
from datetime import timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app import action1, services, sla
from app.config import settings
from app.models import Device, DevicePatch, EventType, SoftwareDeployment, Ticket, TicketSource

log = logging.getLogger(__name__)
ACTIVE = ("submitting", "unknown", "pending", "running")
TERMINAL = ("success", "warning", "error", "stopped")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def configuration():
    if not settings.action1_enabled or not settings.action1_deploy_enabled:
        raise action1.Action1Error("Software deployment is off (Settings > Action1)")
    if not settings.action1_deploy_client_id or not settings.action1_deploy_client_secret or not settings.action1_deploy_org_ids:
        raise action1.Action1Error("Save separate deployment credentials and allowed organization IDs (Settings > Action1)")
    return digest([settings.action1_region, settings.action1_deploy_client_id,
                   settings.action1_deploy_client_secret, sorted(settings.action1_deploy_org_ids)])


def client():
    configuration()
    return action1.Client(action1.HOSTS[settings.action1_region], settings.action1_deploy_client_id,
                          settings.action1_deploy_client_secret)


def segment(value):
    if not isinstance(value, str) or not value or len(value) > 200 or value in (".", "..", "all") or "/" in value or "\\" in value:
        raise action1.Action1Error("Action1 returned an invalid identifier")
    return quote(value, safe="")


def target(session, device_id):
    configuration()
    device = session.get(Device, device_id)
    row = session.scalar(select(DevicePatch).where(DevicePatch.device_id == device_id, DevicePatch.in_action1))
    if device is None or not device.is_active or row is None:
        raise action1.Action1Error("Choose an active device matched to an Action1 endpoint")
    if not row.organization_id:
        raise action1.Action1Error("Refresh Action1 patch status to record this endpoint's organization ID")
    if row.organization_id not in settings.action1_deploy_org_ids:
        raise action1.Action1Error("This endpoint's organization is not allowed for deployment")
    segment(row.organization_id)
    segment(row.action1_id)
    return device, row


def platform(endpoint):
    value = str(endpoint.get("platform") or endpoint.get("Platform") or endpoint.get("OS") or "").lower()
    if value.startswith("windows"):
        return "Windows"
    if value.startswith(("mac", "os x")):
        return "Mac"
    raise action1.Action1Error("This first deployment workflow supports Windows and Mac endpoints only")


def live_target(session, device_id, api):
    device, row = target(session, device_id)
    endpoint = api.get(f"/endpoints/managed/{segment(row.organization_id)}/{segment(row.action1_id)}", {"fields": "*"})
    names = {n.lower().strip() for n in (device.name, device.dns_hostname) if n}
    if endpoint.get("id") != row.action1_id or str(endpoint.get("name") or "").lower().strip() not in names:
        raise action1.Action1Error("Action1's endpoint identity changed; refresh and check the device mapping first")
    return {"organization_id": row.organization_id, "organization": row.organization,
            "endpoint_id": row.action1_id, "device_name": device.name, "endpoint_name": endpoint["name"],
            "platform": platform(endpoint), "connection": str(endpoint.get("status") or "Unknown")}


def package(api, org, package_id, version_id=None):
    value = api.get(f"/software-repository/{segment(org)}/{segment(package_id)}", {"fields": "*"})
    if value.get("id") != package_id:
        raise action1.Action1Error("The selected package is no longer available")
    if version_id is None:
        return value
    version = next((v for v in value.get("versions", []) if v.get("id") == version_id), None)
    if version is None or not str(version.get("version") or "").strip() or str(version["version"]).lower() == "latest":
        raise action1.Action1Error("Select an exact available package version")
    if version.get("status", "Published") != "Published":
        raise action1.Action1Error("Only published versions may be deployed")
    # Version details carry install switches, binary identifiers, EULA and nested actions.
    full = api.get(f"/software-repository/{segment(org)}/{segment(package_id)}/versions/{segment(version_id)}")
    if (full.get("id") != version_id or full.get("version") != version["version"]
            or full.get("status", "Published") != "Published"):
        raise action1.Action1Error("Package version changed; select it again")
    if full.get("additional_actions") or version.get("additional_actions"):
        raise action1.Action1Error("Packages with additional actions must be deployed in Action1 for now")
    if str(full.get("EULA_accepted", "yes")).lower() == "no":
        raise action1.Action1Error("An administrator must review the package licence in Action1 first")
    return value, full


def preview(session, user, device_id, ticket_id, package_id, version_id, api):
    t = live_target(session, device_id, api)
    p, v = package(api, t["organization_id"], package_id, version_id)
    if p.get("platform") != t["platform"]:
        raise action1.Action1Error("The package platform does not match the endpoint")
    inventory = api.get(f"/installed-software/{segment(t['organization_id'])}/data/{segment(t['endpoint_id'])}",
                        {"filter": str(p.get("name") or package_id), "from": 0, "limit": 20})
    installed = [{"name": str(i.get("fields", {}).get("Name") or ""),
                  "version": str(i.get("fields", {}).get("Version") or "")} for i in inventory.get("items", [])]
    snapshot = {**t, "package_id": package_id, "package_name": str(p.get("name") or package_id),
                "vendor": str(p.get("vendor") or ""), "version_id": version_id, "version": v["version"],
                "version_digest": digest(v), "configuration_digest": configuration(), "installed": installed}
    row = SoftwareDeployment(actor_id=user.id, device_id=device_id, ticket_id=ticket_id, snapshot=snapshot,
                             message="Review this exact package and endpoint. Nothing has been submitted.")
    session.add(row)
    session.commit()
    return row


def audit(session, row, message):
    # Notes use the existing staff-only path; no requester email is sent.
    if row.ticket_id and (ticket := session.get(Ticket, row.ticket_id)):
        from app.models import User
        actor = session.get(User, row.actor_id) if row.actor_id else None
        if actor:
            services.add_comment(session, ticket, actor, body=message, is_internal=True,
                                 source=TicketSource.agent, as_staff=True, email_requester=False)
        services._event(session, ticket, EventType.updated, actor, software_deployment_id=row.id,
                        software_deployment_state=row.state)
    log.info("Software deployment %s by user %s on device %s: %s", row.id, row.actor_id, row.device_id,
             message, extra={"source": "action1"})


def submit(session, row, user, api):
    # The device lock serializes competing previews/submissions across users and processes.
    session.scalar(select(Device).where(Device.id == row.device_id).with_for_update())
    session.refresh(row, with_for_update=True)
    if row.state != "review":
        return row  # A repeated POST never starts another Action1 instance.
    if row.actor_id != user.id:
        raise action1.Action1Error("Only the technician who reviewed this deployment may submit it")
    if sla.utcnow() - row.created_at > timedelta(minutes=10):
        raise action1.Action1Error("This review expired; review the package and endpoint again")
    snap = row.snapshot
    if snap["configuration_digest"] != configuration():
        raise action1.Action1Error("Deployment settings changed; review again")
    current = live_target(session, row.device_id, api)
    if any(current[k] != snap[k] for k in ("organization_id", "endpoint_id", "endpoint_name", "device_name", "platform")):
        raise action1.Action1Error("The endpoint changed; review again")
    p, v = package(api, snap["organization_id"], snap["package_id"], snap["version_id"])
    if digest(v) != snap["version_digest"] or p.get("platform") != snap["platform"] or p.get("name") != snap["package_name"]:
        raise action1.Action1Error("Package settings changed; review again")
    others = session.scalars(select(SoftwareDeployment).where(SoftwareDeployment.device_id == row.device_id,
                              SoftwareDeployment.state.in_(ACTIVE), SoftwareDeployment.id != row.id)).all()
    if any(other.snapshot.get("package_id") == snap["package_id"] for other in others):
        raise action1.Action1Error("This package already has an active or uncertain deployment on this device; check its result first")
    # Authenticate before committing intent; authentication itself is safe to retry.
    token = api._auth()
    row.state, row.submitted_at = "submitting", sla.utcnow()
    row.message = "Submission started. If interrupted, check Action1 before creating another deployment."
    summary = f"Action1 software deployment {row.id}: {snap['package_name']} {snap['version']} to {snap['endpoint_name']} ({snap['organization']})."
    audit(session, row, summary + " Submission started; automatic reboot disabled; offline retry window 30 minutes.")
    session.commit()  # Durable intent before the only write to Action1.
    payload = {"name": f"Layer8 {row.id}", "retry_minutes": "30",
               "endpoints": [{"id": snap["endpoint_id"], "type": "Endpoint"}],
               "actions": [{"name": f"Install {snap['package_name']}", "template_id": "deploy_package",
                            "params": {"display_summary": f"{snap['package_name']} {snap['version']}",
                                       "packages": [{snap["package_id"]: snap["version"]}],
                                       "reboot_options": {"auto_reboot": "no"}}}]}
    try:
        # Never use GET's retry loop for this non-idempotent operation.
        response = api._http.post(f"{api.base}/automations/instances/{segment(snap['organization_id'])}",
                                  json=payload, headers={"Authorization": f"Bearer {token}"})
        result = response.json() if response.status_code < 300 else {}
        if not isinstance(result, dict):
            raise ValueError("unconfirmed")
        instance_id = result.get("id")
        if response.status_code >= 300 or not isinstance(instance_id, str) or not instance_id:
            raise ValueError("unconfirmed")
        segment(instance_id)
        row.instance_id, row.state = instance_id, "pending"
        row.message = "Accepted by Action1; installation is not yet confirmed."
    except (httpx.HTTPError, ValueError, action1.Action1Error):
        row.state = "unknown"
        row.message = "Action1 did not confirm acceptance. Do not resend. Check the automation named Layer8 " + row.id + " in Action1."
    audit(session, row, summary + " " + row.message)
    session.commit()
    return row


def refresh(session, row, api):
    if row.state not in ACTIVE:
        return row
    if row.checked_at and sla.utcnow() - row.checked_at < timedelta(seconds=60):
        return row
    if configuration() != row.snapshot["configuration_digest"]:
        raise action1.Action1Error("Deployment credentials or allowed organizations changed; check this run in Action1")
    if not row.instance_id:
        # Recover a lost POST response by reading the uniquely named instance. Never resend.
        page = api.get(f"/automations/instances/{segment(row.snapshot['organization_id'])}",
                       {"filter": f"Layer8 {row.id}", "from": 0, "limit": 10})
        matches = [i for i in page.get("items", []) if i.get("name") == f"Layer8 {row.id}"]
        row.checked_at = sla.utcnow()
        if len(matches) != 1:
            row.message = "No unique matching automation was confirmed. Do not resend; check Action1 manually."
            session.commit()
            return row
        found = api.instance(row.snapshot["organization_id"], segment(matches[0].get("id")))
        expected = [{"id": row.snapshot["endpoint_id"], "type": "Endpoint"}]
        endpoints = [{"id": e.get("id"), "type": e.get("type")} for e in found.get("endpoints", [])]
        actions = found.get("actions") or []
        if (found.get("name") != f"Layer8 {row.id}" or endpoints != expected or len(actions) != 1
                or actions[0].get("template_id") != "deploy_package"
                or actions[0].get("params", {}).get("packages") != [{row.snapshot["package_id"]: row.snapshot["version"]}]):
            raise action1.Action1Error("The matching automation's target or package differs; check Action1 manually")
        row.instance_id = segment(found.get("id"))
        value = found
    else:
        value = api.instance(row.snapshot["organization_id"], segment(row.instance_id))
    if value.get("id") != row.instance_id:
        raise action1.Action1Error("Action1 returned a different automation instance")
    state = str(value.get("status") or "").lower()
    if state not in (*ACTIVE, *TERMINAL):
        state = "pending"
    old = row.state
    row.state, row.checked_at = state, sla.utcnow()
    row.message = {"success": "Action1 reports success. The package may have been installed or already present.",
                   "warning": "Action1 reports a warning; inspect the automation results before treating this as installed.",
                   "error": "Action1 reports a deployment error.", "stopped": "Action1 reports that the automation stopped."}.get(state,
                   "Waiting for Action1 to report a final result; offline endpoints can remain pending.")
    if old != state:
        audit(session, row, f"Action1 software deployment {row.id}: {row.message}")
    session.commit()
    return row


def poll(session):
    if not settings.action1_enabled or not settings.action1_deploy_enabled:
        return 0
    count = 0
    api = client()
    try:
        ids = session.scalars(select(SoftwareDeployment.id).where(SoftwareDeployment.instance_id.is_not(None),
            SoftwareDeployment.state.in_(ACTIVE)).order_by(SoftwareDeployment.checked_at.asc().nullsfirst()).limit(10)).all()
        for id in ids:
            row = session.scalar(select(SoftwareDeployment).where(SoftwareDeployment.id == id).with_for_update(skip_locked=True))
            if row is None:
                continue
            try:
                refresh(session, row, api)
                count += 1
            except action1.Action1Error:
                session.rollback()
                log.warning("Could not refresh Action1 software deployment %s", row.id, extra={"source": "action1"})
    finally:
        api._http.close()
    return count
