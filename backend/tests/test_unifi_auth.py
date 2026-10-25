# SPDX-License-Identifier: AGPL-3.0-or-later
"""Official cloud/local routing and safe authentication diagnostics, without live credentials."""
import httpx
import pytest
from app import unifi
from app.config import settings


def test_cloud_connector_and_local_proxy_have_distinct_paths(monkeypatch):
    monkeypatch.setattr(settings, "unifi_mode", "cloud")
    monkeypatch.setattr(settings, "unifi_console_ids", ["console:123"])
    assert unifi.bases() == [("console:123", "https://api.ui.com/v1/connector/consoles/console%3A123/network/integration/v1")]
    monkeypatch.setattr(settings, "unifi_mode", "local")
    monkeypatch.setattr(settings, "unifi_console_url", "https://unifi.corp.example")
    assert unifi.bases() == [("unifi.corp.example", "https://unifi.corp.example/proxy/network/integration/v1")]


@pytest.mark.parametrize("status", [401,403])
@pytest.mark.parametrize("url,stage", [
    ("https://api.ui.com/v1/hosts", "Site Manager console discovery"),
    ("https://api.ui.com/v1/connector/consoles/example/network/integration/v1/sites", "cloud console Network API"),
    ("https://unifi.corp.example/proxy/network/integration/v1/sites", "local console Network API"),
])
def test_auth_errors_identify_stage_status_and_never_echo_secrets(status,url,stage):
    calls=[]
    def respond(request):
        calls.append(request)
        assert request.headers["X-API-KEY"] == "private-test-key"
        return httpx.Response(status,json={"message":"private-test-key provider-secret"})
    http=httpx.Client(transport=httpx.MockTransport(respond))
    client=unifi.Client("private-test-key",http=http)
    try:
        with pytest.raises(unifi.UnifiError) as error:
            client._call("GET",url)
        message=str(error.value)
        assert stage in message and f"HTTP {status}" in message
        assert "private-test-key" not in message and "provider-secret" not in message
        assert len(calls)==1
        if status==403 and "connector" in url:
            assert "owned by" in message
    finally:
        client.close()
