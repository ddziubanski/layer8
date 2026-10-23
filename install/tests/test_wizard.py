"""Exercise the real wizard with dialog/provisioning commands replaced by test doubles.

Run on Linux: python3 -m unittest discover -s install/tests -v
No root access, services, credentials, or external integrations are used.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "posix", "wizard requires a POSIX shell")
class WizardFlowTests(unittest.TestCase):
    def run_wizard(self, mode, cancel=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "bin"
            binaries.mkdir()
            source = Path(__file__).resolve().parents[1] / "wizard.sh"
            wizard = root / "wizard.sh"
            # Redirect only the wizard's root-owned answers path into the test sandbox.
            wizard.write_text(source.read_text(encoding="utf-8").replace(
                "ANSWERS=/root/helpdesk-answers.json", f'ANSWERS="{root}/answers.json"'))

            def executable(path, content):
                path.write_text(content)
                path.chmod(0o755)

            executable(binaries / "id", '#!/bin/sh\necho 0\n')
            executable(binaries / "hostname", '#!/bin/sh\necho helpdesk.corp.example\n')
            executable(binaries / "timedatectl", '#!/bin/sh\necho UTC\n')
            executable(binaries / "chpasswd", '#!/bin/sh\ncat >/dev/null\n')
            executable(binaries / "whiptail", '''#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
with open(os.environ['QA_LOG'], 'a') as log:
    log.write(repr(args) + '\\n')
def answer(value):
    print(value, file=sys.stderr, end='')
if '--menu' in args:
    answer(os.environ['QA_MODE'])
elif '--passwordbox' in args:
    answer('Synthetic-password-123')
elif '--inputbox' in args:
    question = args[args.index('--inputbox') + 1]
    defaults = {'First ticket number:': '1000', 'Admin\\'s email or username:': 'admin@example.com'}
    answer(defaults.get(question, args[-1]))
elif '--yesno' in args:
    question = args[args.index('--yesno') + 1]
    if question.startswith('Ready to install.'):
        sys.exit(1 if os.environ['QA_CANCEL'] == '1' else 0)
    if question.startswith('Install the offline demo?'):
        sys.exit(0)
    sys.exit(1)  # decline optional integrations, TLS files and network backups
''')
            executable(root / "install.sh", '''#!/bin/sh
printf '%s\\n' "$@" > "$QA_CALL"
if [ "$1" = --answers ]; then cp "$2" "$QA_RESULT"; fi
''')
            if mode == "file":
                (root / "answers.json").write_text('{"install": {"hostname": "helpdesk.corp.example"}}')
            env = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
                   "QA_MODE": mode, "QA_CANCEL": str(int(cancel)),
                   "QA_LOG": str(root / "dialogs"), "QA_CALL": str(root / "call"),
                   "QA_RESULT": str(root / "result")}
            result = subprocess.run(["sh", str(wizard)], env=env, capture_output=True, text=True)
            dialogs = (root / "dialogs").read_text()
            call = (root / "call").read_text() if (root / "call").exists() else None
            answers = json.loads((root / "result").read_text()) if (root / "result").exists() else None
            return result, dialogs, call, answers

    def test_local_install_skips_integrations_and_keeps_backup_choice(self):
        result, dialogs, call, answers = self.run_wizard("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--answers", call)
        self.assertEqual(answers["install"]["admin"]["email"], "admin@example.com")
        self.assertEqual(answers["general"]["ticket_first_number"], 1000)
        self.assertEqual(answers["install"]["tls"], "self-signed")
        self.assertNotIn("Microsoft Entra / Exchange Online: should", dialogs)
        self.assertNotIn("Active Directory: sync", dialogs)
        self.assertNotIn("Do you use Action1", dialogs)
        self.assertNotIn("Allow SQL in ticket rules", dialogs)
        self.assertIn("Nightly encrypted backups", dialogs)
        self.assertIn("Integrations: deferred", dialogs)
        self.assertFalse({"email", "directory", "windows_sign_in", "action1"} & answers.keys())

    def test_integrated_path_retains_optional_questions(self):
        result, dialogs, call, answers = self.run_wizard("integrated")
        self.assertEqual(result.returncode, 0, result.stderr)
        for prompt in ("Microsoft Entra / Exchange Online: should", "Active Directory: sync",
                       "Do you use Action1", "Allow SQL in ticket rules", "Windows sign-in for the portal"):
            self.assertIn(prompt, dialogs)
        self.assertIn("Nightly encrypted backups", dialogs)
        self.assertIn("--answers", call)
        self.assertNotIn("email", answers)

    def test_cancel_at_review_does_not_run_installer(self):
        result, _, call, _ = self.run_wizard("install", cancel=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(call)

    def test_staged_titles_and_navigation_are_present(self):
        result, dialogs, _, _ = self.run_wizard("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        for title in ("1 of 8 | Welcome", "2 of 8 | Server access", "3 of 8 | Address and HTTPS",
                      "4 of 8 | Administrator accounts", "5 of 8 | Ticket numbers",
                      "6 of 8 | Optional integrations", "7 of 8 | Encrypted backups",
                      "8 of 8 | Review your setup", "Setup complete"):
            self.assertIn(title, dialogs)
        self.assertIn("--backtitle", dialogs)
        self.assertIn("--scrolltext", dialogs)
        self.assertNotIn("Synthetic-password-123", dialogs)

    def test_answers_file_still_reaches_existing_installer(self):
        result, _, call, answers = self.run_wizard("file")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--answers", call)
        self.assertEqual(answers["install"]["hostname"], "helpdesk.corp.example")

    def test_demo_still_uses_existing_installer_mode(self):
        result, _, call, _ = self.run_wizard("demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(call.strip(), "--demo")


if __name__ == "__main__":
    unittest.main()
