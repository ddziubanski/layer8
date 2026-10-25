# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proxy migration must preserve local settings and remain safe to rerun."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

FILES = Path(__file__).resolve().parents[1] / "files"
spec = importlib.util.spec_from_file_location("proxy_upgrade", FILES / "layer8-proxy-upgrade.py")
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class ProxyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.template = (FILES / "nginx-helpdesk.conf").read_text(encoding="utf-8")
        start = self.template.index("    # Restart recovery")
        end = self.template.index(proxy.MARKER)
        self.old = self.template[:start] + self.template[end:]
        self.old = self.old.replace("return 503;", proxy.OLD_GATE)

    def test_migrates_existing_gate_preserving_site_and_progress(self):
        text = self.old.replace("server_name _;", "server_name desk.corp.example;")
        result = proxy.updated(text, self.template)
        self.assertIn("server_name desk.corp.example;", result)
        self.assertIn("location = /__layer8_maintenance.html", result)
        self.assertIn("/api/update-progress/", result)
        self.assertNotIn(proxy.OLD_GATE, result)
        self.assertEqual(result.count("if ($layer8_maintenance = 1) { return 503; }"), 2)
        self.assertEqual(proxy.updated(result, self.template), result)

    def test_pre_updater_proxy_gets_gate_and_recovery(self):
        start = self.old.index("    # Only the root-owned updater")
        end = self.old.index(proxy.MARKER)
        legacy = self.old[:start] + self.old[end:]
        legacy = legacy.replace('        if ($layer8_maintenance = 1) { ' + proxy.OLD_GATE + ' }\n', '')
        result = proxy.updated(legacy,self.template)
        self.assertIn("location = /__layer8_maintenance.html",result)
        self.assertEqual(result.count("if ($layer8_maintenance = 1) { return 503; }"),2)

    def test_custom_or_unfamiliar_config_is_refused(self):
        with self.assertRaises(ValueError):
            proxy.updated(self.old.replace(proxy.MARKER,"    error_page 503 /custom;\n"+proxy.MARKER),self.template)
        with self.assertRaises(ValueError):
            proxy.updated("server {}",self.template)

    def test_backup_is_exact_and_rerun_does_not_overwrite_it(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"helpdesk"
            target.write_text(self.old,encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                proxy.main(FILES/"nginx-helpdesk.conf",target)
                proxy.main(FILES/"nginx-helpdesk.conf",target)
            self.assertEqual(output.getvalue(),"1\n0\n")
            backup = target.with_name("helpdesk.before-recovery-page")
            self.assertEqual(backup.read_text(),self.old)
            target.write_text(self.old,encoding="utf-8")
            with self.assertRaises(SystemExit):
                proxy.main(FILES/"nginx-helpdesk.conf",target)
            self.assertEqual(target.read_text(),self.old)
