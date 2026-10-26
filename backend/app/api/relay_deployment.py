# SPDX-License-Identifier: AGPL-3.0-or-later
"""Token-free, administrator-only Windows relay deployment kit."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

from fastapi import APIRouter, HTTPException, Response
from app.auth import Admin
from app.config import settings

router = APIRouter(tags=['relay deployment'])
ROOT = Path(__file__).resolve().parents[3]
FILES = {
    'helpdesk_runner.py': 'runner/helpdesk_runner.py',
    'Relay.Common.ps1': 'tools/relay-deployment/Relay.Common.ps1',
    'Install-Relay.ps1': 'tools/relay-deployment/Install-Relay.ps1',
    'Install-RelayPrerequisites.ps1': 'tools/relay-deployment/Install-RelayPrerequisites.ps1',
    'New-RelayGpo.ps1': 'tools/relay-deployment/New-RelayGpo.ps1',
    'Enable-RelayGpo.ps1': 'tools/relay-deployment/Enable-RelayGpo.ps1',
    'Remove-Relay.ps1': 'tools/relay-deployment/Remove-Relay.ps1',
    'README.md': 'docs/relay-deployment.md',
    'LICENSE': 'LICENSE',
}


APPLIANCE_FILES = {
    'helpdesk_runner.py': 'runner/helpdesk_runner.py',
    'install-appliance-relay.py': 'tools/relay-deployment/install-appliance-relay.py',
    'README.md': 'docs/relay-deployment.md', 'LICENSE': 'LICENSE',
}


def windows_kit(root=ROOT, files=None):
    content = {}
    for name, relative in (FILES if files is None else files).items():
        source = root / relative
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 1024 * 1024:
            raise HTTPException(503, 'This installation lacks the complete Windows kit; install the matching release bundle.')
        content[name] = source.read_bytes()
    content['kit.json'] = json.dumps({'format': 1, 'portal_url': settings.portal_url,
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}}).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in content.items():archive.writestr(name, data)
    return stream.getvalue()


@router.get('/windowskit', response_class=Response, responses={200: {'content': {'application/zip': {}}}})
def download_windows_kit(_: Admin):
    return Response(windows_kit(), media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="layer8-windows-relay-kit.zip"',
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/appliancekit', response_class=Response, responses={200: {'content': {'application/zip': {}}}})
def download_appliance_kit(_: Admin):
    return Response(windows_kit(files=APPLIANCE_FILES), media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="layer8-appliance-relay-kit.zip"',
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
