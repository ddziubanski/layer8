# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reviewed bootstrap of the known appliance proxy, preserving site TLS/host values."""
from pathlib import Path
import re
import sys

MARKER = "    # Attachments:"
OLD_GATE = 'return 503 "Layer 8 update maintenance. Please keep the Updates tab open.\\n";'


def updated(text, template):
    if text.count(MARKER) != 1 or "proxy_pass http://127.0.0.1:8000;" not in text:
        raise ValueError("Unknown Nginx layout; review maintenance configuration locally")
    if "/var/lib/layer8-updater/maintenance" not in text:
        gate = template[template.index("    # Only the root-owned updater"):template.index("    # Restart recovery")]
        text = text.replace(MARKER, gate + MARKER, 1)
        for location in ('    location = /api/meraki/oauth/callback {\n', '    location / {\n'):
            if text.count(location) != 1:
                raise ValueError("Unknown proxy locations; review locally")
            text = text.replace(location, location + '        if ($layer8_maintenance = 1) { return 503; }\n', 1)
    if "location = /__layer8_maintenance.html" not in text:
        if re.search(r"error_page[^;]*\b(?:502|503|504)\b", text):
            raise ValueError("Custom error handling exists; review maintenance configuration locally")
        recovery = template[template.index("    # Restart recovery"):template.index(MARKER)]
        text = text.replace(MARKER, recovery + MARKER, 1)
    return text.replace(OLD_GATE, "return 503;")


def main(template, target=Path("/etc/nginx/sites-available/helpdesk")):
    original = target.read_text(encoding="utf-8")
    result = updated(original, Path(template).read_text(encoding="utf-8"))
    if result == original:
        print("0")
        return
    backup = target.with_name("helpdesk.before-recovery-page")
    if backup.exists():
        raise SystemExit("A previous proxy backup exists; inspect locally before retrying")
    backup.write_text(original, encoding="utf-8")
    target.write_text(result, encoding="utf-8")
    print("1")


if __name__ == "__main__":
    main(sys.argv[1])
