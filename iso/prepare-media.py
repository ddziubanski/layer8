# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prepare boot overlays; leave Debian's partitioner interactive by default."""
from pathlib import Path
import shutil
import sys


# The installer shows its screens on the LAST console= given. Real media name only the monitor:
# with the serial port last, the partitioning questions would go there and the screen stays blank.
SERIAL = " console=tty0 console=ttyS0,115200n8"


def test_menus(destination):
    """Disposable-VM test media only (iso/build.sh --test): serial console for the log, and the
    ERASE entry chosen after 5 seconds, so dev/iso-test.sh runs unattended."""
    grub = destination / "boot/grub/grub.cfg"
    text = grub.read_text(encoding="utf-8").replace(" console=tty0", SERIAL)
    grub.write_text(text.replace("set default=choose", "set default=erase").replace("set timeout=-1", "set timeout=5"), encoding="utf-8")
    isolinux = destination / "isolinux/isolinux.cfg"
    text = isolinux.read_text(encoding="utf-8").replace(" console=tty0", SERIAL)
    text = text.replace("default choose", "default erase").replace("timeout 0", "timeout 50")
    isolinux.write_text(text.replace("    menu default\n", "").replace("label erase\n", "label erase\n    menu default\n"), encoding="utf-8")


def prepare(source, destination, test=False):
    source, destination = Path(source), Path(destination)
    (destination / "isolinux").mkdir(parents=True, exist_ok=True)
    (destination / "boot/grub/layer8").mkdir(parents=True, exist_ok=True)
    preseed = (source / "preseed.cfg").read_text(encoding="utf-8")
    (destination / "preseed.cfg").write_text(preseed, encoding="utf-8")
    # No disk, recipe, LVM deletion, partition-table or write confirmations are
    # supplied on the default path. Debian asks the operator instead.
    interactive = "\n".join(line for line in preseed.splitlines() if not (
        line.startswith("d-i partman") or line.startswith("d-i grub-installer/"))) + "\n"
    (destination / "preseed-partitioned.cfg").write_text(interactive, encoding="utf-8")
    for filename, target in (("isolinux.cfg", "isolinux/isolinux.cfg"),
                             ("grub.cfg", "boot/grub/grub.cfg"),
                             ("theme.txt", "boot/grub/layer8/theme.txt"),
                             ("background.png", "boot/grub/layer8/background.png")):
        shutil.copyfile(source / "branding" / filename, destination / target)
    for tile in (source / "branding").glob("select_*.png"):
        shutil.copyfile(tile, destination / "boot/grub/layer8" / tile.name)
    if test:
        test_menus(destination)


if __name__ == "__main__":
    prepare(sys.argv[1], sys.argv[2], test="--test" in sys.argv[3:])
