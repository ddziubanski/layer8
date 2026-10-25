# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only KACE SMA 15.1 exporter. Stdlib only; live interoperability awaits an appliance."""
import argparse
import getpass
import hashlib
import http.cookiejar
import json
import os
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_FILE = 25 * 1024 * 1024


class ExportError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def identifier(value):
    value = str(value)
    if not value.isdigit() or len(value) > 20:
        raise ExportError("The appliance returned an unexpected record ID")
    return value


def lower(row):
    return {str(k).lower(): v for k, v in row.items()} if isinstance(row, dict) else {}


def person(raw):
    value = lower(raw)
    if not value.get("id"):
        return None
    # Never export passwords, roles, sessions, tokens or other user fields.
    return {"id":identifier(value["id"]), "name":str(value.get("full_name") or value.get("name") or value.get("user_name") or "")[:200], "email":str(value.get("email") or "")[:320]}


def label(raw, fallback=""):
    return str(lower(raw).get("name") or fallback) if isinstance(raw, dict) else str(raw or fallback)


def timestamp(raw, zone):
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        raise ExportError("An unrecognized source timestamp was returned; inspect the payload locally before importing") from None
    return (value if value.tzinfo else value.replace(tzinfo=zone)).isoformat()


class Client:
    def __init__(self, base, ca_file=None):
        url = urllib.parse.urlsplit(base)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
            raise ExportError("Use the appliance HTTPS origin, without a path or credentials")
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def call(self, path, body=None, binary=False):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, headers={"Accept":"application/json", "Content-Type":"application/json", "x-kace-api-version":"1"}, method="POST" if body is not None else "GET")
        try:
            with self.opener.open(request, timeout=30) as response:
                payload = response.read(MAX_FILE + 1)
        except urllib.error.HTTPError as exc:
            raise ExportError(f"KACE HTTP {exc.code}; check organization, read permissions and MFA (--mfa). No partial bundle was kept") from None
        except (urllib.error.URLError, TimeoutError):
            raise ExportError("Cannot reach KACE over trusted HTTPS; check address, certificate and network access") from None
        if len(payload) > MAX_FILE:
            raise ExportError("A response or attachment exceeds 25 MiB; use a smaller batch or review this attachment")
        if binary:
            return payload
        try:
            return json.loads(payload)
        except (ValueError, UnicodeError):
            raise ExportError("KACE did not return JSON; check API access") from None

    def pages(self, path, key, shaping, limit=None, start=0):
        offset = start
        for _ in range(10000):
            size = min(50, limit) if limit is not None else 50
            query = urllib.parse.urlencode({"paging":f"offset {offset} limit {size}", "sorting":"id", "shaping":shaping})
            body = self.call(path + "?" + query)
            rows = body.get(key) if isinstance(body, dict) else None
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ExportError(f"Unexpected {key} response; verify the API shape before importing")
            yield from rows
            offset += len(rows)
            if limit is not None:
                limit -= len(rows)
            if len(rows) < size or limit == 0:
                return
        raise ExportError("Pagination limit reached; export smaller batches")


def export(client, org, zone, out, limit=100, offset=0):
    if out.exists():
        raise ExportError("Output already exists; choose a new bundle name")
    total = 0
    tickets = []
    with tempfile.NamedTemporaryFile(prefix=".kace-export-", suffix=".zip", dir=out.parent, delete=False) as temp:
        temporary = Path(temp.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for raw in client.pages("/api/service_desk/tickets", "Tickets", "hd_ticket all,owner standard,submitter standard,queue limited,category limited,priority limited,status standard", limit, offset):
                r = lower(raw)
                tid = identifier(r.get("id"))
                state = str(lower(r.get("status")).get("state") or label(r.get("status"))).lower()
                status_name = label(r.get("status")).lower()
                status = "closed" if state in ("closed", "complete", "completed") else "resolved" if state == "resolved" else "waiting_on_user" if any(word in status_name for word in ("wait", "pending", "hold")) else "open"
                priority = label(r.get("priority")).lower()
                priority = "critical" if any(word in priority for word in ("critical", "urgent")) else "high" if "high" in priority else "low" if "low" in priority else "medium"
                row = {"id":tid, "title":str(r.get("title") or f"KACE ticket {tid}")[:300], "description":str(r.get("summary") or ""), "status":status, "priority":priority, "category":label(r.get("category"))[:100], "source_queue":label(r.get("queue"))[:200], "requester":person(r.get("submitter")), "owner":person(r.get("owner")), "created_at":timestamp(r.get("created") or r.get("created_at"),zone), "closed_at":timestamp(r.get("time_closed") or r.get("closed_at"),zone), "custom_fields":{}, "notes":[], "work":[]}
                for key, value in r.items():
                    if key.startswith("custom_") and key[7:].isdigit() and value is not None:
                        row["custom_fields"][key] = (json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else str(value))[:4000]
                for raw_change in client.pages(f"/api/service_desk/tickets/{tid}/changes", "Changes", "hd_ticket_change all,attachments standard,user standard"):
                    change = lower(raw_change)
                    cid = identifier(change.get("id"))
                    comment = change.get("comment") or change.get("comments")
                    note = {"id":cid, "body":str(comment or change.get("description") or ""), "internal":True if comment is None else str(change.get("owners_only", "1")).lower() not in ("0","false"), "author":person(change.get("user")), "created_at":timestamp(change.get("timestamp") or change.get("created") or change.get("created_at"),zone), "files":[]}
                    for raw_file in change.get("attachments") or []:
                        file = lower(raw_file)
                        aid = identifier(file.get("id"))
                        payload = client.call(f"/api/service_desk/tickets/{tid}/changes/{cid}/attachments/{aid}",binary=True)
                        total += len(payload)
                        if total > 240 * 1024 * 1024:
                            raise ExportError("Attachment data exceeds this batch's limit; reduce --limit")
                        path = f"files/{tid}/{cid}/{aid}"
                        archive.writestr(path,payload)
                        note["files"].append({"id":aid,"name":str(file.get("file_name") or "attachment")[:255],"path":path,"sha256":hashlib.sha256(payload).hexdigest()})
                    row["notes"].append(note)
                if r.get("resolution"):
                    # A reserved numeric ID cannot collide with KACE's integer change IDs.
                    row["notes"].append({"id":"99999999999999999999","body":str(r["resolution"]),"internal":True,"author":row["owner"],"created_at":row["closed_at"],"files":[]})
                for raw_work in client.pages(f"/api/service_desk/tickets/{tid}/work", "Work", "hd_work all,user standard"):
                    work = lower(raw_work)
                    minutes = work.get("minutes",0)
                    if not isinstance(minutes,int) or minutes < 0:
                        raise ExportError("Unrecognized work duration; inspect locally rather than guessing time units")
                    row["work"].append({"id":identifier(work.get("id")),"note":str(work.get("note") or ""),"minutes":minutes,"author":person(work.get("user")),"created_at":timestamp(work.get("created") or work.get("created_at"),zone)})
                tickets.append(row)
            if not tickets:
                raise ExportError("No tickets returned for this organization/batch")
            archive.writestr("tickets.jsonl","\n".join(json.dumps(row,ensure_ascii=False) for row in tickets))
            archive.writestr("manifest.json",json.dumps({"format":"layer8-kace-export-v1","appliance":client.base,"organization":org,"tickets":len(tickets)}))
        if temporary.stat().st_size > 64 * 1024 * 1024:
            raise ExportError("Compressed bundle exceeds 64 MiB; reduce --limit")
        os.replace(temporary,out)
        return len(tickets)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",required=True)
    parser.add_argument("--organization",required=True)
    parser.add_argument("--user",required=True)
    parser.add_argument("--timezone",required=True,help="Appliance IANA timezone, e.g. America/Los_Angeles; used only for timestamps without offsets")
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--limit",type=int,default=100)
    parser.add_argument("--offset",type=int,default=0)
    parser.add_argument("--ca-file")
    parser.add_argument("--mfa",action="store_true")
    args = parser.parse_args()
    try:
        if not 1 <= args.limit <= 1000 or args.offset < 0:
            raise ExportError("Use --limit 1..1000 and a non-negative --offset")
        zone = ZoneInfo(args.timezone)
        client = Client(args.url,args.ca_file)
        password = getpass.getpass("KACE password: ")
        client.call("/ams/shared/api/security/login",{"userName":args.user,"password":password,"organizationName":args.organization})
        password = None
        if args.mfa:
            client.call("/ams/shared/api/security/verify_2factor",{"currentCode":getpass.getpass("Current MFA code: ")})
        count = export(client,args.organization,zone,args.out,args.limit,args.offset)
        print(f"Exported {count} tickets to {args.out}. No KACE data was changed. Keep this bundle private.")
    except (ExportError,OSError,ValueError,KeyError) as exc:
        parser.exit(1,f"{exc}\n")


if __name__ == "__main__":
    main()
