"""Prepare boot overlays; leave Debian's partitioner interactive by default."""
from pathlib import Path
import shutil
import sys


def prepare(source, destination):
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


if __name__ == "__main__":
    prepare(*sys.argv[1:])
