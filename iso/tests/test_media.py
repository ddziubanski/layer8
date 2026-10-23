"""Boot overlay contracts, including the non-destructive default path."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ISO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("media", ISO / "prepare-media.py")
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


class MediaTests(unittest.TestCase):
    def test_partition_choice_retains_appliance_without_partition_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media.prepare(ISO, root)
            manual = (root / "preseed-partitioned.cfg").read_text()
            automatic = (root / "preseed.cfg").read_text()
            self.assertNotIn("d-i partman", manual)
            self.assertNotIn("d-i grub-installer/", manual)
            self.assertIn("d-i partman/confirm boolean true", automatic)
            for text in (manual, automatic):
                self.assertIn("in-target systemctl enable helpdesk-firstboot.service", text)
                self.assertIn("d-i apt-setup/use_mirror boolean false", text)
                self.assertIn("d-i passwd/user-password-crypted password !", text)
            self.assertTrue((root / "boot/grub/layer8/background.png").is_file())

    def test_both_firmwares_default_to_choice_and_wait_indefinitely(self):
        for filename, timeout in (("grub.cfg", "set timeout=-1"), ("isolinux.cfg", "timeout 0")):
            config = (ISO / "branding" / filename).read_text()
            self.assertIn(timeout, config)
            self.assertIn("default" + ("=choose" if filename == "grub.cfg" else " choose"), config)
            self.assertEqual(config.count("preseed/file=/cdrom/preseed-partitioned.cfg"), 1)
            self.assertEqual(config.count("preseed/file=/cdrom/preseed.cfg"), 1)
            self.assertLess(config.index("preseed-partitioned.cfg"), config.index("preseed/file=/cdrom/preseed.cfg"))
            self.assertIn("ERASE entire first disk", config)


if __name__ == "__main__":
    unittest.main()
