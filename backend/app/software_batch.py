# SPDX-License-Identifier: AGPL-3.0-or-later
"""Action1 deployments to several machines or an Action1 group, one or more packages (a bundle),
now or once at a set time. Builds on app/software_deployment.py and keeps its rules: separate
credentials, the organization allowlist, the action1_deploy permission, a review that expires,
durable intent before the only write, never a retried or repeated write, no automatic reboot.

Checked against a live tenant on 2026-11-06 with GET requests only (structure, not data):
- runs and schedules take endpoints as [{"id", "type": "Endpoint" | "EndpointGroup"}];
- GET /endpoints/groups/{org}[/{id}[/contents]] lists groups and their machines;
- deploy_package's packages is a list, so one run can carry several packages;
- schedules have settings like "ENABLED ONCE AT:HH-MM-SS DATE:YYYY-MM-DD", settings_timezone,
  retry_minutes; a run's endpoint_results link lists one result per machine.
Not tried live (by design: the tenant is production): the two writes, POST
/automations/instances/{org} (as the single-device slice) and POST /automations/schedules/{org}.
"""
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, or_, select

from app import action1, sla
from app.models import DevicePatch, SoftwareBundle, SoftwareDeployment
from app.software_deployment import ACTIVE, TERMINAL, audit, configuration, digest, package, platform, segment

log = logging.getLogger(__name__)
WAITING = (*ACTIVE, "scheduled")
MAX_MACHINES = 50
MAX_PACKAGES = 10
MAX_GROUP = 500
RETRY_MINUTES = ("30", "480", "1440")


class BatchError(action1.Action1Error):
    pass


def _org_name(api, org) -> str:
    for o in api.organizations():
        if o.get("id") == org:
            return str(o.get("name") or org)
    raise BatchError("These deployment credentials can't see that organization")


def groups(api, org) -> list[dict]:
    configuration()
    _allowed(org)
    return [{"id": str(g.get("id") or ""), "name": str(g.get("name") or ""), "description": str(g.get("description") or "")}
            for g in api.pages(f"/endpoints/groups/{segment(org)}") if g.get("id")]


def _allowed(org):
    from app.config import settings

    if org not in settings.action1_deploy_org_ids:
        raise BatchError("This organization is not allowed for deployment (Settings > Action1)")
    segment(org)


def _machines(session, api, org, device_ids: list[int]) -> list[dict]:
    """Each chosen device must be matched to an endpoint in this organization, and Action1 must
    still call it the same: one listing of the organization's endpoints, not one call each."""
    if not device_ids or len(device_ids) > MAX_MACHINES:
        raise BatchError(f"Choose 1 to {MAX_MACHINES} machines")
    from app.models import Device

    rows = {r.device_id: r for r in session.scalars(select(DevicePatch).where(DevicePatch.device_id.in_(device_ids), DevicePatch.in_action1))}
    live = {e.get("id"): e for e in api.pages(f"/endpoints/managed/{segment(org)}", {"fields": "*"})}
    out = []
    for device_id in dict.fromkeys(device_ids):
        device, row = session.get(Device, device_id), rows.get(device_id)
        if device is None or not device.is_active or row is None:
            raise BatchError(f"Device {device_id} isn't an active device matched to an Action1 endpoint")
        if row.organization_id != org:
            raise BatchError(f"{device.name} is in another Action1 organization")
        endpoint = live.get(row.action1_id)
        names = {n.lower().strip() for n in (device.name, device.dns_hostname) if n}
        if endpoint is None or str(endpoint.get("name") or "").lower().strip() not in names:
            raise BatchError(f"Action1's endpoint for {device.name} changed; refresh Action1 and check the device first")
        out.append({"type": "Endpoint", "id": segment(row.action1_id), "name": str(endpoint["name"]), "device_id": device_id,
                    "platform": platform(endpoint), "connection": str(endpoint.get("status") or "Unknown")})
    return out


def _group(api, org, group_id) -> tuple[dict, list[dict]]:
    g = api.get(f"/endpoints/groups/{segment(org)}/{segment(group_id)}", {"fields": "*"})
    if g.get("id") != group_id:
        raise BatchError("That Action1 group no longer exists")
    members = []
    for e in api.pages(f"/endpoints/groups/{segment(org)}/{segment(group_id)}/contents"):
        members.append({"name": str(e.get("name") or ""), "platform": str(e.get("platform") or e.get("OS") or ""),
                        "connection": str(e.get("status") or "Unknown")})
        if len(members) > MAX_GROUP:
            raise BatchError(f"That group has more than {MAX_GROUP} machines; deploy to it from Action1")
    if not members:
        raise BatchError("That Action1 group has no machines")
    return {"type": "EndpointGroup", "id": segment(group_id), "name": str(g.get("name") or group_id), "members": len(members)}, members


def _when(run_at: datetime | None) -> str | None:
    if run_at is None:
        return None
    if run_at.tzinfo is None:
        raise BatchError("Give the time with its time zone")
    at = run_at.astimezone(UTC).replace(second=0, microsecond=0)
    now = sla.utcnow()
    if at < now + timedelta(minutes=10):
        raise BatchError("Schedule it at least 10 minutes ahead, or deploy now")
    if at > now + timedelta(days=90):
        raise BatchError("Schedule it within 90 days")
    return at.isoformat()


def preview(session, user, api, *, organization_id: str, device_ids: list[int] | None = None, group_id: str | None = None,
            packages: list[tuple[str, str]] | None = None, bundle_id: int | None = None, ticket_id: int | None = None,
            run_at: datetime | None = None, retry_minutes: str = "30") -> SoftwareDeployment:
    """Check everything against Action1 and save the review. Nothing is sent to Action1."""
    config = configuration()
    org = organization_id
    _allowed(org)
    if bool(device_ids) == bool(group_id):
        raise BatchError("Choose machines or one Action1 group")
    if retry_minutes not in RETRY_MINUTES:
        raise BatchError("Offline machines: retry for 30 minutes, 8 hours or 24 hours")
    if bundle_id is not None:
        bundle = session.get(SoftwareBundle, bundle_id)
        if bundle is None or bundle.organization_id != org:
            raise BatchError("That bundle isn't in this organization")
        packages = [(p["package_id"], p["version_id"]) for p in bundle.packages]
    packages = list(dict.fromkeys(packages or []))
    if not packages or len(packages) > MAX_PACKAGES:
        raise BatchError(f"Choose 1 to {MAX_PACKAGES} packages")
    if len({p for p, _ in packages}) != len(packages):
        raise BatchError("Choose one version of each package")
    when = _when(run_at)
    if group_id:
        target, members = _group(api, org, group_id)
        targets, platforms = [target], {platform({"platform": m["platform"]}) if m["platform"] else "Unknown" for m in members}
    else:
        targets = _machines(session, api, org, device_ids)
        members = [{"name": t["name"], "platform": t["platform"], "connection": t["connection"]} for t in targets]
        platforms = {t["platform"] for t in targets}
    if len(platforms) != 1 or "Unknown" in platforms:
        raise BatchError("Every machine must be the same platform (all Windows or all Mac)")
    plat = platforms.pop()
    chosen = []
    for package_id, version_id in packages:
        p, v = package(api, org, package_id, version_id)
        if p.get("platform") != plat:
            raise BatchError(f"{p.get('name') or package_id} isn't a {plat} package")
        chosen.append({"package_id": package_id, "package_name": str(p.get("name") or package_id), "vendor": str(p.get("vendor") or ""),
                       "version_id": version_id, "version": v["version"], "version_digest": digest(v)})
    snapshot = {"kind": "batch", "organization_id": org, "organization": _org_name(api, org), "platform": plat,
                "targets": targets, "members": members[:50], "member_count": len(members), "packages": chosen,
                "run_at": when, "retry_minutes": retry_minutes, "configuration_digest": config}
    row = SoftwareDeployment(actor_id=user.id, device_id=targets[0].get("device_id") if len(targets) == 1 else None, ticket_id=ticket_id,
                             snapshot=snapshot, message="Review these machines, packages and time. Nothing has been sent to Action1.")
    session.add(row)
    session.commit()
    return row


def _summary(snap) -> str:
    what = ", ".join(f"{p['package_name']} {p['version']}" for p in snap["packages"])
    where = (f"Action1 group {snap['targets'][0]['name']} ({snap['member_count']} machines)" if snap["targets"][0]["type"] == "EndpointGroup"
             else f"{len(snap['targets'])} machine(s): " + ", ".join(t["name"] for t in snap["targets"][:10]) + ("…" if len(snap["targets"]) > 10 else ""))
    when = f" at {snap['run_at'][:16].replace('T', ' ')} UTC" if snap.get("run_at") else ""
    return f"{what} to {where} ({snap['organization']}){when}"


def _overlaps(session, row) -> bool:
    snap = row.snapshot
    ids = {t["id"] for t in snap["targets"]}
    pkgs = {p["package_id"] for p in snap["packages"]}
    for other in session.scalars(select(SoftwareDeployment).where(SoftwareDeployment.state.in_(WAITING), SoftwareDeployment.id != row.id)):
        o = other.snapshot
        if o.get("organization_id") != snap["organization_id"]:
            continue
        o_ids = {t["id"] for t in o.get("targets", [])} | ({o["endpoint_id"]} if o.get("endpoint_id") else set())
        o_pkgs = {p["package_id"] for p in o.get("packages", [])} | ({o["package_id"]} if o.get("package_id") else set())
        if ids & o_ids and pkgs & o_pkgs:
            return True
    return False


def payload(row) -> dict:
    snap = row.snapshot
    names = [p["package_name"] for p in snap["packages"]]
    body = {"name": f"Layer8 {row.id}", "retry_minutes": snap["retry_minutes"],
            "endpoints": [{"id": t["id"], "type": t["type"]} for t in snap["targets"]],
            "actions": [{"name": f"Install {names[0]}" if len(names) == 1 else f"Install {len(names)} packages", "template_id": "deploy_package",
                         "params": {"display_summary": ", ".join(f"{p['package_name']} {p['version']}" for p in snap["packages"])[:250],
                                    "packages": [{p["package_id"]: p["version"]} for p in snap["packages"]],
                                    "reboot_options": {"auto_reboot": "no"}}}]}
    if snap.get("run_at"):
        at = datetime.fromisoformat(snap["run_at"])
        body.update(settings=f"ENABLED ONCE AT:{at:%H-%M-%S} DATE:{at:%Y-%m-%d}", settings_timezone="UTC", randomize_start="0")
    return body


def submit(session, row, user, api) -> SoftwareDeployment:
    # One submission at a time per organization, across users and API processes.
    session.execute(select(func.pg_advisory_xact_lock(int(digest(["action1-batch", row.snapshot["organization_id"]])[:15], 16))))
    session.refresh(row, with_for_update=True)
    if row.state != "review":
        return row  # A repeated POST never sends again.
    if row.actor_id != user.id:
        raise BatchError("Only the technician who reviewed this deployment may submit it")
    if sla.utcnow() - row.created_at > timedelta(minutes=10):
        raise BatchError("This review expired; review again")
    snap = row.snapshot
    if snap["configuration_digest"] != configuration():
        raise BatchError("Deployment settings changed; review again")
    if snap.get("run_at") and datetime.fromisoformat(snap["run_at"]) < sla.utcnow() + timedelta(minutes=5):
        raise BatchError("The scheduled time is too close now; review again")
    # Targets and packages again, as at review.
    if snap["targets"][0]["type"] == "EndpointGroup":
        target, _ = _group(api, snap["organization_id"], snap["targets"][0]["id"])
        if target["name"] != snap["targets"][0]["name"]:
            raise BatchError("The Action1 group changed; review again")
    else:
        current = _machines(session, api, snap["organization_id"], [t["device_id"] for t in snap["targets"]])
        if [(t["id"], t["name"]) for t in current] != [(t["id"], t["name"]) for t in snap["targets"]]:
            raise BatchError("The machines changed; review again")
    for p in snap["packages"]:
        _, v = package(api, snap["organization_id"], p["package_id"], p["version_id"])
        if digest(v) != p["version_digest"]:
            raise BatchError(f"{p['package_name']} changed in Action1; review again")
    if _overlaps(session, row):
        raise BatchError("Some of these machines already have one of these packages waiting or running; check that deployment first")
    token = api._auth()
    scheduled = bool(snap.get("run_at"))
    row.state, row.submitted_at = "submitting", sla.utcnow()
    row.message = "Submission started. If interrupted, check Action1 before deploying again."
    summary = f"Action1 software deployment {row.id}: {_summary(snap)}."
    audit(session, row, summary + f" Submission started; automatic reboot off; offline machines retried for {int(snap['retry_minutes']) // 60 or snap['retry_minutes']}{' hours' if int(snap['retry_minutes']) >= 60 else ' minutes'}.")
    session.commit()  # Durable intent before the only write.
    path = "schedules" if scheduled else "instances"
    try:
        response = api._http.post(f"{api.base}/automations/{path}/{segment(snap['organization_id'])}", json=payload(row),
                                  headers={"Authorization": f"Bearer {token}"})
        result = response.json() if response.status_code < 300 else {}
        new_id = result.get("id") if isinstance(result, dict) else None
        if response.status_code >= 300 or not isinstance(new_id, str) or not new_id:
            raise ValueError("unconfirmed")
        segment(new_id)
        if scheduled:
            row.schedule_id, row.state = new_id, "scheduled"
            row.message = f"Scheduled in Action1 for {snap['run_at'][:16].replace('T', ' ')} UTC. Nothing is installed yet."
        else:
            row.instance_id, row.state = new_id, "pending"
            row.message = "Accepted by Action1; installation is not yet confirmed."
    except (httpx.HTTPError, ValueError, action1.Action1Error):
        row.state = "unknown"
        row.message = f"Action1 did not confirm acceptance. Do not resend. Check the automation named Layer8 {row.id} in Action1."
    audit(session, row, summary + " " + row.message)
    session.commit()
    return row


def _named(api, org, kind, name) -> list[dict]:
    page = api.get(f"/automations/{kind}/{segment(org)}", {"filter": name, "from": 0, "limit": 10})
    return [i for i in page.get("items", []) if i.get("name") == name]


def _results(api, value) -> list[dict]:
    link = str(value.get("endpoint_results") or "")
    if not link:
        return []
    path = "/" + link.split("/api/3.0/", 1)[-1].lstrip("/") if "/api/3.0/" in link else link
    if not path.startswith("/automations/instances/") or ".." in path:
        raise BatchError("Action1 returned an unexpected results link")
    out = []
    for r in api.pages(path):
        out.append({"endpoint": str(r.get("endpoint_name") or ""), "status": str(r.get("status") or ""),
                    "time": str(r.get("time") or ""), "details": str(r.get("details") or r.get("description") or "")[:300]})
        if len(out) >= MAX_GROUP:
            break
    return out


def refresh(session, row, api) -> SoftwareDeployment:
    if row.state not in WAITING:
        return row
    if row.checked_at and sla.utcnow() - row.checked_at < timedelta(seconds=60):
        return row
    snap = row.snapshot
    if configuration() != snap["configuration_digest"]:
        raise BatchError("Deployment credentials or allowed organizations changed; check this run in Action1")
    org, name = snap["organization_id"], f"Layer8 {row.id}"
    row.checked_at = sla.utcnow()
    if row.state == "unknown" and not row.instance_id and not row.schedule_id:
        # A lost answer: look the uniquely named run or schedule up. Never resend.
        found = _named(api, org, "schedules" if snap.get("run_at") else "instances", name)
        if len(found) != 1:
            row.message = "No unique matching automation was confirmed. Do not resend; check Action1."
            session.commit()
            return row
        expected = [{"id": t["id"], "type": t["type"]} for t in snap["targets"]]
        if [{"id": e.get("id"), "type": e.get("type")} for e in found[0].get("endpoints", [])] not in ([], expected):
            raise BatchError("The matching automation's machines differ; check Action1")
        if snap.get("run_at"):
            row.schedule_id, row.state = segment(found[0]["id"]), "scheduled"
        else:
            row.instance_id, row.state = segment(found[0]["id"]), "pending"
    if row.schedule_id and not row.instance_id:
        # Scheduled: once it has run, its run carries the same name.
        runs = _named(api, org, "instances", name)
        if not runs:
            if datetime.fromisoformat(snap["run_at"]) + timedelta(hours=2) < sla.utcnow():
                row.message = "The scheduled time has passed but Action1 shows no run yet; check the schedule in Action1."
            session.commit()
            return row
        row.instance_id = segment(runs[0]["id"])
    value = api.instance(org, segment(row.instance_id))
    if value.get("id") != row.instance_id:
        raise BatchError("Action1 returned a different automation run")
    state = str(value.get("status") or "").lower()
    state = state if state in (*ACTIVE, *TERMINAL) else "pending"
    old = row.state
    row.state, row.results = state, _results(api, value)
    counts: dict[str, int] = {}
    for r in row.results:
        counts[r["status"] or "Pending"] = counts.get(r["status"] or "Pending", 0) + 1
    tally = ", ".join(f"{n} {s.lower()}" for s, n in sorted(counts.items()))
    row.message = {"success": "Action1 reports success on every machine.",
                   "warning": "Action1 reports warnings; check the machines below before treating them as installed.",
                   "error": "Action1 reports errors on some machines.", "stopped": "Action1 reports that the run stopped."}.get(
        state, "Running; offline machines stay pending until they come online or the retry window ends.") + (f" ({tally})" if tally else "")
    if old != state:
        audit(session, row, f"Action1 software deployment {row.id}: {row.message}")
    session.commit()
    return row


def waiting(session):
    return select(SoftwareDeployment.id).where(SoftwareDeployment.state.in_(WAITING),
                                              or_(SoftwareDeployment.instance_id.is_not(None), SoftwareDeployment.schedule_id.is_not(None)))


# Bundles


def save_bundle(session, user, api, *, organization_id: str, name: str, packages: list[tuple[str, str]], bundle_id: int | None = None) -> SoftwareBundle:
    configuration()
    _allowed(organization_id)
    name = name.strip()
    if not 1 <= len(name) <= 100:
        raise BatchError("Give the bundle a name (up to 100 characters)")
    packages = list(dict.fromkeys(packages))
    if not packages or len(packages) > MAX_PACKAGES:
        raise BatchError(f"A bundle has 1 to {MAX_PACKAGES} packages")
    chosen, platforms = [], set()
    for package_id, version_id in packages:
        p, v = package(api, organization_id, package_id, version_id)
        platforms.add(p.get("platform"))
        chosen.append({"package_id": package_id, "version_id": version_id, "package_name": str(p.get("name") or package_id), "version": v["version"]})
    if len(platforms) != 1:
        raise BatchError("A bundle's packages must all be for one platform")
    bundle = session.get(SoftwareBundle, bundle_id) if bundle_id else SoftwareBundle(organization_id=organization_id)
    if bundle is None or bundle.organization_id != organization_id:
        raise BatchError("No such bundle in this organization")
    clash = session.scalar(select(SoftwareBundle).where(SoftwareBundle.organization_id == organization_id,
                                                        func.lower(SoftwareBundle.name) == name.lower(), SoftwareBundle.id != (bundle_id or 0)))
    if clash:
        raise BatchError("There's already a bundle with that name")
    bundle.name, bundle.platform, bundle.packages, bundle.updated_by_id = name, platforms.pop(), chosen, user.id
    session.add(bundle)
    session.commit()
    return bundle
