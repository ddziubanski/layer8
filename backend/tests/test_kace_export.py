# SPDX-License-Identifier: AGPL-3.0-or-later
"""API contract fixtures for the standalone exporter; live KACE validation is pending."""
import importlib.util
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app import kace

spec=importlib.util.spec_from_file_location("kace_export",Path(__file__).resolve().parents[2]/"tools/kace-export/kace-export.py")
exporter=importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class FakeKace:
    base="https://kace.corp.example"
    def pages(self,path,key,shaping,limit=None,start=0):
        if key=="Tickets":
            yield {"id":42,"title":"Example issue","summary":"Help please","created":"2024-01-02 10:00:00","status":{"name":"Closed","state":"closed"},"priority":{"name":"High"},"submitter":{"id":1,"full_name":"Example requester","email":"person@example.com","password":"never-export-this"},"custom_1":"HQ","resolution":"Fixed"}
        elif key=="Changes":
            yield {"id":10,"comment":"Private note","owners_only":1,"user":{"id":2,"full_name":"Example technician","password":"never-export-this"},"attachments":[{"id":5,"file_name":"sample.txt"}]}
        elif key=="Work":
            yield {"id":20,"note":"Historical work note"}
        else: raise AssertionError(key)
    def call(self,path,binary=False):
        assert binary and path=="/api/service_desk/tickets/42/changes/10/attachments/5"
        return b"Example file"


def test_exported_fixture_is_importable_and_contains_no_credentials(tmp_path):
    output=tmp_path/"trial.zip"
    assert exporter.export(FakeKace(),"Default",ZoneInfo("UTC"),output)==1
    bundle=kace.Bundle(output.read_bytes())
    assert bundle.rows[0].notes[0].internal
    assert bundle.rows[0].work[0].minutes==0
    assert bundle.rows[0].created_at.isoformat()=="2024-01-02T10:00:00+00:00"
    assert "never-export-this" not in bundle.zip.read("tickets.jsonl").decode()
    with pytest.raises(exporter.ExportError,match="exists"):
        exporter.export(FakeKace(),"Default",ZoneInfo("UTC"),output)


def test_pagination_is_bounded_and_sorted():
    client=object.__new__(exporter.Client)
    paths=[]
    def call(path):
        paths.append(path)
        return {"Tickets":[{"id":1},{"id":2}]}
    client.call=call
    assert len(list(client.pages("/api/service_desk/tickets","Tickets","hd_ticket all",2)))==2
    assert "paging=offset+0+limit+2" in paths[0] and "sorting=id" in paths[0]
    client.call=lambda _: {"Unexpected":[]}
    with pytest.raises(exporter.ExportError,match="Unexpected"):
        list(client.pages("/tickets","Tickets","hd_ticket all",1))


def test_redirects_insecure_origins_and_unknown_dates_are_refused():
    for bad in ("http://kace.corp.example","https://user:password@kace.corp.example","https://kace.corp.example/path"):
        with pytest.raises(exporter.ExportError):exporter.Client(bad)
    assert exporter.NoRedirect().redirect_request(None,None,None,None,None,None) is None
    with pytest.raises(exporter.ExportError):exporter.timestamp("unrecognized",ZoneInfo("UTC"))


def test_required_api_headers_and_login_method():
    client=object.__new__(exporter.Client);client.base=FakeKace.base
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,_):return b'{"Result":"Success"}'
    class Opener:
        def open(self,request,timeout):calls.append(request);return Response()
    client.opener=Opener()
    client.call("/ams/shared/api/security/login",{"userName":"migration","password":"test-only"})
    client.call("/api/service_desk/tickets")
    assert [request.method for request in calls]==["POST","GET"]
    for request in calls:assert {key.lower():value for key,value in request.header_items()}["x-kace-api-version"]=="1"
