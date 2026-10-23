"""Capture real whiptail welcome dialogs in an 80x24 PTY, without provisioning."""
import os
from pathlib import Path
import tempfile
import time
import pexpect
import pyte
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
COLORS = {"black": "#12243a", "red": "#c94747", "green": "#1eaa94",
          "brown": "#bb9847", "blue": "#12243a", "magenta": "#b46ab0",
          "cyan": "#26bbae", "white": "#d1deeb", "default": "#d1deeb"}


def capture(script, output, label):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        # Only bypass the root check; never let this preview elevate or provision.
        fake_id = root / "id"
        fake_id.write_text("#!/bin/sh\necho 0\n")
        fake_id.chmod(0o755)
        fake_hostname = root / "hostname"
        fake_hostname.write_text("#!/bin/sh\necho helpdesk.corp.example\n")
        fake_hostname.chmod(0o755)
        env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}", "TERM": "xterm"}
        child = pexpect.spawn("sh", [str(script)], env=env, dimensions=(24, 80), encoding="utf-8")
        terminal = pyte.Screen(80, 24)
        stream = pyte.Stream(terminal)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    stream.feed(child.read_nonblocking(size=65536, timeout=.5))
                except pexpect.TIMEOUT:
                    if label in "\n".join(terminal.display):
                        break
            else:
                raise RuntimeError(f"Dialog did not render: {label}\n" + "\n".join(terminal.display))
            output.parent.mkdir(parents=True, exist_ok=True)
            font = ImageFont.truetype("DejaVuSansMono.ttf", 16)
            image = Image.new("RGB", (800, 480), "#12243a")
            draw = ImageDraw.Draw(image)
            for y in range(24):
                for x in range(80):
                    cell = terminal.buffer[y][x]
                    fg = COLORS.get(cell.fg, "#" + cell.fg)
                    bg = COLORS.get(cell.bg, "#" + cell.bg)
                    if cell.reverse:
                        fg, bg = bg, fg
                    draw.rectangle((x*10, y*20, x*10+9, y*20+19), fill=bg)
                    draw.text((x*10, y*20), cell.data, font=font, fill=fg)
            # The active menu row must use the brand accent, not newt's default red.
            if not any(cell.bg == "cyan" for row in terminal.buffer.values() for cell in row.values()):
                raise RuntimeError("The active menu row has no cyan accent")
            image.save(output)
        finally:
            child.terminate(force=True)


if __name__ == "__main__":
    capture(ROOT / "install/wizard.sh", ROOT / "iso/build/previews/setup.png", "Welcome")
    capture(ROOT / "install/files/layer8-console", ROOT / "iso/build/previews/recovery.png", "what needs fixing?")
