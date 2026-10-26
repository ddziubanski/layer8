# SPDX-License-Identifier: AGPL-3.0-or-later
import json

import httpx
import pytest
from sqlalchemy import select

from app import configstore, library, teams_chat
from app.config import settings
from app.models import Comment, Queue, Ticket, TicketSource
from tests.conftest import as_user

SITE = 'https://example.sharepoint.com/sites/IT'
ITEM = {'id': 'item-1', 'name': 'VPN FAQ', 'webUrl': SITE + '/SitePages/VPN.aspx?web=1', 'parentReference': {'driveId': 'drive-1'}}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, 'library_enabled', True)
    monkeypatch.setattr(settings, 'library_site_url', SITE)
    monkeypatch.setattr(teams_chat, 'access', lambda user_id, scopes: ('fixture-token', {'library': True}))
    monkeypatch.setattr(teams_chat, 'read', lambda user_id: {'library': True})


@pytest.mark.parametrize('url', ['http://example.sharepoint.com/sites/IT', 'https://example.com/sites/IT', SITE + '?token=x', SITE + '#x', 'https://u:p@example.sharepoint.com/sites/IT', 'https://example.sharepoint.com', SITE + '/pages', 'https://example.sharepoint.com:443/sites/IT'])
def test_site_must_be_named_and_safe(url):
    with pytest.raises(library.LibraryError):library.site_url(url)
    with pytest.raises(configstore.SettingsError):configstore._library_site(url)


@pytest.mark.parametrize('url', [SITE + 'Other/file', SITE + '/%2e%2e/HR/file', SITE + '/%252e%252e/HR/file', 'https://evil.example/file', 'javascript:alert(1)', 'https://example.sharepoint.com/sites/HR/file', SITE + '/%0d%0a/file'])
def test_result_urls_cannot_escape_the_approved_site(url):
    assert library.approved_url(url, SITE) is None


def test_search_is_literal_bounded_and_filters_untrusted_results(configured, monkeypatch):
    calls = []
    def graph(method, route, token, body):
        calls.append((method, route, body))
        return {'value': [{'hitsContainers': [{'total': 9000, 'hits': [
            {'resource': ITEM, 'summary': '<c0>VPN</c0> connection instructions'},
            {'resource': {**ITEM, 'id': 'foreign', 'webUrl': SITE + 'Other/private'}, 'summary': 'never expose'},
            {'resource': ITEM}, {'resource': {'parentReference': 'bad'}}]}]}]}
    monkeypatch.setattr(library, 'graph', graph)
    results = library.search('fixture-token', 'vpn" OR path:elsewhere')
    assert len(results) == 1 and results[0]['summary'] == 'VPN connection instructions'
    assert results[0]['url'] == SITE + '/SitePages/VPN.aspx'
    query = calls[0][2]['requests'][0]['query']['queryString']
    assert query.startswith('path:"' + SITE + '/" AND (') and '"OR"' in query
    assert calls[0][2]['requests'][0]['size'] == 10
    assert '9000' not in json.dumps(results)


@pytest.mark.parametrize('status', [302, 401, 403, 429, 500])
def test_provider_errors_are_sanitized(configured, monkeypatch, status):
    original = httpx.Client
    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return original(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(status, text='secret upstream text', headers={'Location': 'https://evil.example'})))
    monkeypatch.setattr(library.httpx, 'Client', factory)
    with pytest.raises(library.LibraryError) as exc:library.graph('POST', '/search/query', 'fixture-token', {})
    assert 'secret' not in str(exc.value)


def test_oversized_response_is_refused(configured, monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(library.httpx, 'Client', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'x' * (library.MAX_RESPONSE + 1)))))
    with pytest.raises(library.LibraryError, match='too much'):library.graph('POST', '/search/query', 'fixture-token', {})


def make_ticket(db, requester, restricted=False):
    queue = Queue(name='IT', restricted=restricted)
    db.add(queue);db.flush()
    ticket = Ticket(subject='VPN fails', requester_id=requester.id, queue_id=queue.id, source=TicketSource.portal)
    db.add(ticket);db.commit()
    return ticket


def test_search_link_access_review_and_internal_history(client, db, requester, agent, configured, monkeypatch):
    ticket = make_ticket(db, requester)
    monkeypatch.setattr(library, 'search', lambda token, query: [{**ITEM, 'drive_id': 'drive-1', 'item_id': 'item-1', 'title': 'VPN FAQ', 'url': SITE + '/SitePages/VPN.aspx', 'summary': 'Read the guide'}])
    monkeypatch.setattr(library, 'resolve', lambda token, drive, item: {'title': 'VPN FAQ', 'url': SITE + '/SitePages/VPN.aspx'})
    search_url = f'/api/tickets/{ticket.id}/library/search'
    link_url = f'/api/tickets/{ticket.id}/library/link'
    h = as_user(agent)
    assert client.post(search_url, json={'query': 'VPN'}, headers=as_user(requester)).status_code == 403
    assert client.post(search_url, json={'query': 'VPN'}, headers=h).status_code == 200
    assert db.scalars(select(Comment)).all() == []
    body = {'drive_id': 'drive-1', 'item_id': 'item-1', 'confirm': False}
    assert client.post(link_url, json=body, headers=h).status_code == 422
    body['confirm'] = True
    result = client.post(link_url, json=body, headers=h)
    assert result.status_code == 200, result.text
    assert client.post(link_url, json=body, headers=h).json() == result.json()
    comments = db.scalars(select(Comment)).all()
    assert len(comments) == 1 and comments[0].is_internal and not comments[0].emailed
    assert 'VPN FAQ' in comments[0].body


def test_restricted_ticket_disabled_source_and_revoked_access_fail_closed(client, db, requester, agent, admin, configured, monkeypatch):
    ticket = make_ticket(db, requester, restricted=True)
    def forbidden(*args):raise AssertionError('must not contact Microsoft')
    monkeypatch.setattr(library, 'search', forbidden)
    url = f'/api/tickets/{ticket.id}/library/search'
    assert client.post(url, json={'query': 'VPN'}, headers=as_user(agent)).status_code == 404
    monkeypatch.setattr(settings, 'library_enabled', False)
    assert client.post(url, json={'query': 'VPN'}, headers=as_user(admin)).status_code == 409
    monkeypatch.setattr(settings, 'library_enabled', True)
    monkeypatch.setattr(teams_chat, 'access', lambda *args: ('fixture-token', {}))
    assert client.post(url, json={'query': 'VPN'}, headers=as_user(admin)).status_code == 409


def test_library_scope_is_opt_in_and_does_not_duplicate_copilot_scopes(monkeypatch):
    monkeypatch.setattr(settings, 'copilot_enabled', False)
    monkeypatch.setattr(settings, 'library_enabled', False)
    assert teams_chat.scopes() == teams_chat.SCOPES
    monkeypatch.setattr(settings, 'library_enabled', True)
    assert teams_chat.scopes() == teams_chat.SCOPES + library.SCOPES
    monkeypatch.setattr(settings, 'copilot_enabled', True)
    assert teams_chat.scopes().count('Sites.Read.All') == 1


def test_link_rechecks_provider_access_and_site_before_adding_a_note(client, db, requester, agent, configured, monkeypatch):
    ticket = make_ticket(db, requester)
    url = f'/api/tickets/{ticket.id}/library/link'
    body = {'drive_id': 'drive-1', 'item_id': 'item-1', 'confirm': True}
    for item in ({**ITEM, 'webUrl': 'https://example.sharepoint.com/sites/HR/private.docx'}, {**ITEM, 'id': 'different-item'}):
        monkeypatch.setattr(library, 'graph', lambda *args, **kwargs: item)
        result = client.post(url, json=body, headers=as_user(agent))
        assert result.status_code == 409
        assert db.scalars(select(Comment)).all() == []
    assert client.post(url, json=body, headers=as_user(requester)).status_code == 403
