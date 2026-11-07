# SPDX-License-Identifier: AGPL-3.0-or-later
"""install/firstboot.sh: the unattended install is logged, and a failure says why on the console."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "firstboot.sh"


@unittest.skipUnless(os.name == "posix", "needs a POSIX shell")
class FirstbootTests(unittest.TestCase):
    def run_firstboot(self, install_body, answers=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(SOURCE, root / "firstboot.sh")
            for name, body in (("install.sh", install_body), ("wizard.sh", "echo WIZARD")):
                (root / name).write_text(f"#!/bin/sh\n{body}\n")
                (root / name).chmod(0o755)
            if answers:
                (root / "answers.json").write_text("{}")
            env = {**os.environ, "LAYER8_ANSWERS": str(root / "answers.json"), "LAYER8_INSTALL_LOG": str(root / "install.log")}
            result = subprocess.run(["sh", str(root / "firstboot.sh")], capture_output=True, text=True, env=env)
            log = (root / "install.log").read_text() if (root / "install.log").exists() else ""
            return result, log

    def test_success_is_logged(self):
        result, log = self.run_firstboot('echo "== Done"; echo "args: $*"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("== Done", log)
        self.assertIn("--answers", log)
        self.assertNotIn("stopped", result.stdout)

    def test_failure_says_why_and_keeps_the_status(self):
        result, log = self.run_firstboot('echo "== Database schema"; echo "install: the database schema update failed (alembic exit 1)" >&2; exit 1')
        self.assertEqual(result.returncode, 1)
        self.assertIn("install: the database schema update failed", log)
        self.assertIn("the install stopped before it finished", result.stdout)
        self.assertIn("install: the database schema update failed (alembic exit 1)", result.stdout)

    def test_no_answers_runs_the_wizard(self):
        result, _ = self.run_firstboot("echo INSTALL", answers=False)
        self.assertEqual(result.stdout.strip(), "WIZARD")


if __name__ == "__main__":
    unittest.main()
