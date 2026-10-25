# SPDX-License-Identifier: AGPL-3.0-or-later
"""GRUB's nine-slice highlight must be complete and present in both media variants."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

ISO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("theme_media", ISO / "prepare-media.py")
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)
PARTS = {"c", "n", "s", "e", "w", "nw", "ne", "sw", "se"}


class ThemeTests(unittest.TestCase):
    def test_artwork_and_selection_tiles_have_supported_png_formats(self):
        files = {path.stem.removeprefix("select_") for path in (ISO / "branding").glob("select_*.png")}
        self.assertEqual(files, PARTS)
        for name in ["background.png", *(f"select_{part}.png" for part in sorted(PARTS))]:
            raw = (ISO / "branding" / name).read_bytes()
            self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
            width, height, depth, color = struct.unpack(">IIBB", raw[16:26])
            self.assertEqual(depth, 8)
            if name == "background.png":
                self.assertEqual((width, height, color), (1024, 768, 2))
            else:
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)
                self.assertEqual(color, 6)  # RGBA corners preserve transparency.
        theme = (ISO / "branding/theme.txt").read_text()
        self.assertIn('selected_item_pixmap_style = "select_*.png"', theme)
        self.assertIn('selected_item_color = "#f2f7fb"', theme)

    def test_all_theme_assets_reach_real_and_disposable_test_media(self):
        for test in (False, True):
            with self.subTest(test=test), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                media.prepare(ISO, root, test=test)
                for part in PARTS:
                    name = f"select_{part}.png"
                    self.assertEqual((root / "boot/grub/layer8" / name).read_bytes(), (ISO / "branding" / name).read_bytes())
                self.assertEqual((root / "boot/grub/layer8/theme.txt").read_bytes(), (ISO / "branding/theme.txt").read_bytes())


if __name__ == "__main__":
    unittest.main()
