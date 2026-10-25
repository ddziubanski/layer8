# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from app.models import AppSetting
from tests.conftest import as_user

DEFAULT = {"density": "comfortable", "paging": "scroll", "page_size": 100}


def test_queue_list_is_personal_and_independent(client, db, agent, admin):
    h = as_user(agent)
    assert client.get("/api/auth/me", headers=h).json()["queue_list"] == DEFAULT
    chosen = {"density": "compact", "paging": "pages", "page_size": 25}
    r = client.put("/api/auth/me/preferences", headers=h, json={"queue_list": chosen})
    assert r.status_code == 200, r.text
    assert r.json()["queue_list"] == chosen
    assert client.get("/api/auth/me", headers=as_user(admin)).json()["queue_list"] == DEFAULT
    # Saving other preferences leaves it alone; null puts the defaults back.
    assert client.put("/api/auth/me/preferences", headers=h, json={"ticket_color": "#112233"}).json()["queue_list"] == chosen
    assert client.put("/api/auth/me/preferences", headers=h, json={"queue_list": None}).json()["queue_list"] == DEFAULT
    assert db.get(AppSetting, f"personal_queue_list:{agent.id}").updated_by_id == agent.id


@pytest.mark.parametrize("value", [{"density": "tiny"}, {"paging": "infinite"}, {"page_size": 30}, {"page_size": 1000}, {"extra": 1}])
def test_queue_list_validated(client, agent, value):
    assert client.put("/api/auth/me/preferences", headers=as_user(agent), json={"queue_list": value}).status_code == 422


def test_damaged_queue_list_does_not_block_sign_in(client, db, agent):
    db.add(AppSetting(key=f"personal_queue_list:{agent.id}", value={"page_size": "lots"}))
    db.commit()
    assert client.get("/api/auth/me", headers=as_user(agent)).json()["queue_list"] == DEFAULT


def test_requesters_have_no_queue_list(client, requester):
    r = client.put("/api/auth/me/preferences", headers=as_user(requester), json={"queue_list": DEFAULT})
    assert r.status_code == 403
