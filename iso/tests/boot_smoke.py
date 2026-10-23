"""Boot the real ISO without a hard disk; capture BIOS/UEFI menus with QMP.

Requires QEMU, OVMF and Pillow. This checks menus, not an installed appliance.
"""
import json
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time
from PIL import Image


def run(iso, output, firmware):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        monitor = root / "qmp.sock"
        args = ["qemu-system-x86_64", "-m", "1024", "-accel", "tcg", "-display", "none",
                "-nic", "none", "-cdrom", str(iso), "-boot", "d",
                "-qmp", f"unix:{monitor},server=on,wait=off"]
        if firmware == "uefi":
            variables = root / "OVMF_VARS.fd"
            shutil.copyfile("/usr/share/OVMF/OVMF_VARS_4M.fd", variables)
            args += ["-drive", "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd",
                     "-drive", f"if=pflash,format=raw,file={variables}"]
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 20
            while not monitor.exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("QEMU failed to open monitor")
                time.sleep(.2)
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(str(monitor))
                stream = connection.makefile("rwb", buffering=0)
                stream.readline()

                def command(name, arguments=None):
                    stream.write((json.dumps({"execute": name, "arguments": arguments or {}}) + "\n").encode())
                    while True:
                        response = json.loads(stream.readline())
                        if "error" in response:
                            raise RuntimeError(response)
                        if "return" in response:
                            return response["return"]

                command("qmp_capabilities")
                # TCG also works on CI hosts without KVM. Wait for the actual artwork.
                deadline = time.monotonic() + 90
                screenshot = root / "screen.ppm"
                while True:
                    time.sleep(2)
                    command("screendump", {"filename": str(screenshot)})
                    with Image.open(screenshot) as screen:
                        rgb = screen.convert("RGB")
                        count = sum(1 for r, g, b in rgb.getdata() if g > 140 and b > 120 and r < 90)
                        ready = rgb.size == (1024, 768) and count > 1000
                        if ready:
                            rgb.save(output / f"boot-{firmware}.png")
                            initial = rgb.tobytes()
                            break
                    if time.monotonic() > deadline:
                        raise RuntimeError(f"{firmware}: branded menu did not appear")
                time.sleep(8)  # The old five-second countdown must not start an install.
                command("screendump", {"filename": str(screenshot)})
                with Image.open(screenshot) as screen:
                    if screen.convert("RGB").tobytes() != initial:
                        raise RuntimeError(f"{firmware}: menu changed without operator input")
                command("quit")
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


if __name__ == "__main__":
    for firmware in ("bios", "uefi"):
        run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), firmware)
