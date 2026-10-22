"""The published code and sample data must name no real organisation: examples use example.com
and corp.example (reserved for documentation). Names that must never appear are kept here
ROT13-encoded, so this file doesn't trip itself."""

import codecs
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BANNED = [codecs.decode(n, "rot13") for n in ("gjvacvar", "gcp.yna", "gjva cvar", "qp=gcp,qp=yna")]


def test_no_real_organisation_names():
    try:
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        files = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts]
    hits = []
    for name in files:
        path = ROOT / name
        if not path.is_file() or path.suffix.lower() in (".png", ".jpg", ".ico", ".woff2", ".pdf"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        hits += [f"{name}: {b}" for b in BANNED if b in text]
    assert not hits, "Real organisation names in the repository (use example.com / corp.example):\n" + "\n".join(hits)
