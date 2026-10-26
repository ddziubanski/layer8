# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit, delegated SharePoint runbook/FAQ page search; no indexing or downloads."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx

from app.config import settings

SCOPES = ['Sites.Read.All']
GRAPH = 'https://graph.microsoft.com/v1.0'
MAX_RESPONSE = 2 * 1024 * 1024


class LibraryError(ValueError):
    """Static messages safe for technicians; no upstream content/tokens in errors."""


def site_url(value):
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}\.sharepoint\.com', url.netloc.lower())
                or not re.fullmatch(r'/(sites|teams)/[A-Za-z0-9_-]{1,128}/?', url.path)
                or url.query or url.fragment):
            raise ValueError
        return urlunsplit(('https', url.netloc.lower(), url.path.rstrip('/'), '', ''))
    except (ValueError, TypeError, AttributeError):
        raise LibraryError('Use a named commercial SharePoint site URL, such as https://tenant.sharepoint.com/sites/IT') from None


def approved_url(value, site):
    """Result filtering is independent of provider search/path filtering."""
    try:
        url, approved = urlsplit(value), urlsplit(site_url(site))
        path = unquote(url.path)
        if (url.scheme != 'https' or url.netloc.lower() != approved.netloc or '%' in path or '\\' in path
                or any(ord(ch) < 32 for ch in path) or any(p in ('.', '..') for p in path.split('/'))
                or not path.casefold().startswith(approved.path.casefold() + '/')):
            return None
        # Never retain sharing tokens, fragments or provider-controlled query parameters.
        return urlunsplit(('https', approved.netloc, url.path, '', ''))
    except (ValueError, TypeError, AttributeError):
        return None


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, text):
        self.parts.append(text)


def plain(value, limit):
    parser = Plain()
    parser.feed(str(value or '')[:10000])
    return ' '.join(' '.join(parser.parts).split())[:limit]


def graph(method, route, token, body=None):
    """Only fixed Microsoft Graph calls; redirects, proxies and large bodies are refused."""
    try:
        with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as http:
            with http.stream(method, GRAPH + route, headers={'Authorization': 'Bearer ' + token}, json=body) as response:
                if response.status_code in (401, 403):
                    raise LibraryError('SharePoint access was refused. Check your site access and reconnect Microsoft after admin consent for Sites.Read.All.')
                if response.status_code == 429:
                    raise LibraryError('SharePoint is busy. Wait a moment before searching again.')
                if response.status_code != 200:
                    raise LibraryError('SharePoint could not complete the lookup. Try again later.')
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE:
                        raise LibraryError('SharePoint returned too much data. Use a more specific search.')
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except httpx.HTTPError:
        raise LibraryError('SharePoint could not be reached. Try again later.') from None
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, LibraryError):
            raise
        raise LibraryError('SharePoint returned a response that could not be read.') from None


def reference(resource):
    if not isinstance(resource, dict):
        return None
    url = approved_url(resource.get('webUrl'), settings.library_site_url)
    drive = resource.get('parentReference') or {}
    if not isinstance(drive, dict):
        return None
    drive_id, item_id = drive.get('driveId'), resource.get('id')
    if (not url or not isinstance(drive_id, str) or not isinstance(item_id, str)
            or not 0 < len(drive_id) <= 512 or not 0 < len(item_id) <= 512):
        return None
    title = plain(resource.get('name'), 300)
    if not title:
        return None
    return {'drive_id': drive_id, 'item_id': item_id, 'title': title, 'url': url}


def search(token, query):
    site = site_url(settings.library_site_url)
    # Treat user input as literal terms, not KQL. The ticket text is never sent implicitly.
    words = re.findall(r'[\w-]+', query, flags=re.UNICODE)[:12]
    if not words:
        raise LibraryError('Enter a few words to find a runbook or FAQ page.')
    text = ' AND '.join('"' + word + '"' for word in words)
    result = graph('POST', '/search/query', token, {'requests': [{'entityTypes': ['driveItem'],
        'query': {'queryString': f'path:"{site}/" AND ({text})'}, 'from': 0, 'size': 10}]})
    found, seen = [], set()
    try:
        for response in result.get('value', []):
            for container in response.get('hitsContainers', []):
                for hit in container.get('hits', []):
                    ref = reference(hit.get('resource'))
                    if ref and (ref['drive_id'], ref['item_id']) not in seen:
                        seen.add((ref['drive_id'], ref['item_id']))
                        found.append({**ref, 'summary': plain(hit.get('summary'), 800)})
        return found[:10]
    except (AttributeError, TypeError):
        raise LibraryError('SharePoint returned a response that could not be read.') from None


def resolve(token, drive_id, item_id):
    resource = graph('GET', '/drives/' + quote(drive_id, safe='') + '/items/' + quote(item_id, safe='')
                     + '?$select=id,name,webUrl,parentReference', token)
    ref = reference(resource)
    if not ref or ref['drive_id'] != drive_id or ref['item_id'] != item_id:
        raise LibraryError('This reference is no longer available in the approved Library site.')
    return ref
