# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every source file carries the AGPL licence line (empty files and generated API types aside)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAG = "SPDX-License-Identifier: AGPL-3.0-or-later"
SOURCE = (".py", ".ts", ".tsx", ".css", ".sh", ".ps1")
GENERATED = {"frontend/src/api/schema.d.ts", "frontend/src/vite-env.d.ts"}


def test_source_files_carry_the_licence_line():
    try:
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        files = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts]
    missing = []
    for name in files:
        path = ROOT / name
        if not name.endswith(SOURCE) or name in GENERATED or name.startswith("gui-sample/") or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if text.strip() and TAG not in text[:400]:
            missing.append(name)
    assert not missing, "add '# SPDX-License-Identifier: AGPL-3.0-or-later' (or // or /* */) at the top of: " + ", ".join(missing[:20])
