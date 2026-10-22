"""Layer 8 runner: runs action scripts for the service desk, on this machine or from it.

Copy this one file to the runner host (Python 3.10+, standard library only) and run it as a
service, e.g. on a domain-joined Windows box for PowerShell over WinRM:

    python helpdesk_runner.py --url https://helpdesk.corp.example --token-file C:\\ProgramData\\HelpdeskRunner\\token

It polls the service desk over HTTPS (no inbound ports), claims queued runs in the languages
its runner record allows (Settings > Runners), streams their output back and reports how they
ended. Parameters reach scripts by name:

- PowerShell: as variables ($days). With a target device, the script runs there through
  Invoke-Command -ComputerName (WinRM, as the runner's service account); without, here.
- bash and Python: as environment variables HD_<NAME> (HD_DAYS), plus HD_TARGET and
  HD_TARGET_OS. They run here; a script reaches its target itself (ssh, an API...). Secret
  values (an SNMP community) come on standard input as one JSON object, never in the environment.
- Arista EOS: the script is CLI commands, one per line ($name or ${name} filled in), sent to
  the target switch's eAPI (https://<switch>/command-api) with --eos-user/--eos-password-file.
"""

import argparse
import base64
import json
import os
import queue
import shutil
import signal
import socket
import ssl
import string
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "1.3"
POST_EVERY = 2.0  # seconds between output updates while a script runs


class TimeLimit(Exception):
    pass


class Api:
    def __init__(self, url: str, token: str, ca_file: str | None):
        self.url = url.rstrip("/")
        self.token = token
        self.ctx = ssl.create_default_context(cafile=ca_file) if self.url.startswith("https") else None

    def post(self, path: str, body: dict) -> tuple[int, dict | None]:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "User-Agent": f"helpdesk-runner/{VERSION}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=self.ctx) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            if exc.code == 401:
                raise SystemExit(f"The service desk refused this runner's token: {detail}") from None
            return exc.code, {"detail": detail}


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# Building the command for each language


def env_for(job: dict) -> dict:
    # Never the runner's own settings (its token): a script could act as the runner with it.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("HELPDESK_")}
    for k, v in (job.get("values") or {}).items():
        env[f"HD_{k.upper()}"] = "" if v is None else ("true" if v is True else "false" if v is False else str(v))
    env["HD_TARGET"] = job.get("target") or ""
    env["HD_TARGET_OS"] = job.get("target_os") or ""
    env["HD_RUN_ID"] = str(job["run_id"])
    # Output as it happens, not when Python's buffer fills.
    env["PYTHONUNBUFFERED"] = "1"
    return env


def powershell_exe(given: str | None) -> str:
    exe = given or shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        raise RuntimeError("PowerShell isn't installed on the runner (pwsh or powershell)")
    return exe


def powershell_files(job: dict, work: Path) -> Path:
    """The script, wrapped to receive its parameters as variables, and a launcher that runs it here or on the target."""
    inner = work / "script.ps1"
    inner.write_text(
        "param($HDValues)\n"
        "if ($HDValues) { foreach ($p in $HDValues.PSObject.Properties) { Set-Variable -Name $p.Name -Value $p.Value } }\n"
        + job["body"]
        + "\n",
        encoding="utf-8-sig",
    )
    # Secret values (a new account's password) only reach the script as variables: they come on
    # standard input, never in the environment or a file on disk.
    data = json.dumps({"target": job.get("target"), "values": job.get("values") or {}})
    outer = work / "launch.ps1"
    outer.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$HD = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{base64.b64encode(data.encode()).decode()}')) | ConvertFrom-Json\n"
        f"$inner = '{inner}'\n"
        "$secret = [Console]::In.ReadToEnd()\n"
        "if ($secret) { foreach ($p in ($secret | ConvertFrom-Json).PSObject.Properties) { $HD.values | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force } }\n"
        "if ($HD.target) { Invoke-Command -ComputerName $HD.target -FilePath $inner -ArgumentList (,$HD.values) }\n"
        "else { & $inner $HD.values }\n"
        "if ($LASTEXITCODE) { exit $LASTEXITCODE }\n",
        encoding="utf-8-sig",
    )
    return outer


def command_for(job: dict, work: Path, args) -> list[str]:
    lang = job["language"]
    if lang == "powershell":
        return [powershell_exe(args.powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(powershell_files(job, work))]
    if lang == "bash":
        path = work / "script.sh"
        path.write_text(job["body"], encoding="utf-8")
        return [shutil.which("bash") or "bash", str(path)]
    if lang == "python":
        path = work / "script.py"
        path.write_text(job["body"], encoding="utf-8")
        return [sys.executable, str(path)]
    raise RuntimeError(f"This runner can't run {lang}")


# Running a process with streamed output and a time limit


def run_process(cmd: list[str], env: dict, cwd: Path, limit: int, send, stdin: bytes | None = None) -> int:
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE, env=env, cwd=cwd, **kw)
    if stdin is not None:
        try:
            proc.stdin.write(stdin)
            proc.stdin.close()
        except OSError:  # it ended without reading
            pass
    lines: queue.Queue = queue.Queue()

    def pump():
        for raw in iter(proc.stdout.readline, b""):
            lines.put(raw.decode("utf-8", errors="replace"))
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + limit
    buf, last, ended = [], time.monotonic(), False
    while not ended:
        try:
            line = lines.get(timeout=0.5)
            if line is None:
                ended = True
            else:
                buf.append(line)
        except queue.Empty:
            pass
        if buf and (ended or time.monotonic() - last >= POST_EVERY):
            send("".join(buf))
            buf, last = [], time.monotonic()
        if not ended and time.monotonic() > deadline:
            kill(proc)
            send("".join(buf) + f"\n[stopped: over the {limit} s time limit]\n")
            proc.wait(timeout=30)
            raise TimeLimit(f"Stopped after the {limit} s time limit")
    return proc.wait()


def kill(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# Arista EOS through eAPI


def run_eos(job: dict, args, send) -> int:
    if not job.get("target"):
        raise RuntimeError("An EOS script needs a target switch")
    if not args.eos_user or not args.eos_password_file:
        raise RuntimeError("Set --eos-user and --eos-password-file on the runner for EOS scripts")
    password = Path(args.eos_password_file).read_text().strip()
    values = {k: "" if v is None else str(v) for k, v in (job.get("values") or {}).items()}
    cmds = [string.Template(line).safe_substitute(values).strip() for line in job["body"].splitlines()]
    cmds = [c for c in cmds if c and not c.startswith(("!", "#"))]
    body = json.dumps({"jsonrpc": "2.0", "method": "runCmds", "params": {"version": 1, "cmds": cmds, "format": "text"}, "id": job["run_id"]}).encode()
    auth = base64.b64encode(f"{args.eos_user}:{password}".encode()).decode()
    req = urllib.request.Request(f"https://{job['target']}/command-api", data=body, headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"}, method="POST")
    ctx = ssl.create_default_context(cafile=args.eos_ca_file)
    if args.eos_insecure:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=min(job["time_limit_seconds"], 120), context=ctx) as r:
            answer = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"eAPI on {job['target']} answered {exc.code}: {exc.read().decode(errors='replace')[:300]}") from None
    if "error" in answer:
        err = answer["error"]
        done = [d.get("output", "") for d in (err.get("data") or []) if isinstance(d, dict)]
        send("".join(f"{job['target']}# {c}\n{o}" for c, o in zip(cmds, done)))
        raise RuntimeError(f"eAPI error {err.get('code')}: {err.get('message')}")
    send("".join(f"{job['target']}# {c}\n{r.get('output', '')}" for c, r in zip(cmds, answer.get("result", []))))
    return 0


# The loop


def handle(api: Api, job: dict, args) -> None:
    run_id = job["run_id"]
    log(f"run #{run_id}: {job['language']} on {job.get('target') or 'this runner'}")

    def send(text: str) -> None:
        status, _ = api.post(f"/api/runner/runs/{run_id}/output", {"text": text[:200_000]})
        if status >= 400:
            log(f"run #{run_id}: output not accepted ({status})")

    exit_code, error = None, None
    try:
        if job["language"] == "eos":
            exit_code = run_eos(job, args, send)
        else:
            with tempfile.TemporaryDirectory(prefix="hd-run-") as tmp:
                work = Path(tmp)
                # Secret values on stdin: PowerShell's launcher turns them into variables (see powershell_files).
                secrets = job.get("secret_values")
                stdin = json.dumps(secrets).encode() if secrets else None
                exit_code = run_process(command_for(job, work, args), env_for(job), work, int(job["time_limit_seconds"]), send, stdin)
    except TimeLimit as exc:
        error = str(exc)
    except Exception as exc:  # report anything, keep serving
        error = f"{type(exc).__name__}: {exc}"
    api.post(f"/api/runner/runs/{run_id}/finish", {"exit_code": exit_code, "error": error})
    log(f"run #{run_id}: exit {exit_code}" + (f", {error}" if error else ""))


def main() -> None:
    p = argparse.ArgumentParser(description="Runs action scripts for the service desk.")
    p.add_argument("--url", required=True, help="The service desk, e.g. https://helpdesk.corp.example")
    p.add_argument("--token-file", help="File holding this runner's token (Settings > Runners). Or set HELPDESK_RUNNER_TOKEN.")
    p.add_argument("--ca-file", help="CA certificate to check the service desk's HTTPS certificate (default: the system's)")
    p.add_argument("--interval", type=float, default=5.0, help="Seconds between checks for work (default 5)")
    p.add_argument("--once", action="store_true", help="Run at most one job, then stop (for testing)")
    p.add_argument("--powershell", help="PowerShell to use (default: pwsh, else powershell)")
    p.add_argument("--eos-user", help="eAPI user for Arista EOS scripts")
    p.add_argument("--eos-password-file", help="File with that user's password")
    p.add_argument("--eos-ca-file", help="CA certificate for the switches' eAPI certificates")
    p.add_argument("--eos-insecure", action="store_true", help="Don't check switch certificates (lab use only)")
    p.add_argument("--allow-http", action="store_true", help="Talk to the service desk over plain http (development only: the token would cross the network in the clear)")
    args = p.parse_args()
    token = Path(args.token_file).read_text().strip() if args.token_file else os.environ.get("HELPDESK_RUNNER_TOKEN", "")
    os.environ.pop("HELPDESK_RUNNER_TOKEN", None)
    if not token:
        raise SystemExit("Give the runner's token with --token-file or HELPDESK_RUNNER_TOKEN")
    host_part = args.url.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]")
    if not args.url.lower().startswith("https://") and host_part not in ("localhost", "127.0.0.1", "::1") and not args.allow_http:
        raise SystemExit("Use the service desk's https:// address (or --allow-http for development)")
    api = Api(args.url, token, args.ca_file)
    host = socket.getfqdn()
    log(f"helpdesk runner {VERSION} on {host}, polling {api.url}")
    backoff = args.interval
    while True:
        try:
            status, job = api.post("/api/runner/claim", {"host": host, "version": VERSION})
            backoff = args.interval
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log(f"can't reach the service desk: {exc}")
            status, job = 0, None
            backoff = min(backoff * 2, 300)
        if status == 200 and job:
            handle(api, job, args)
            if args.once:
                return
            continue
        if status not in (0, 204):
            log(f"claim answered {status}: {job}")
        if args.once:
            return
        time.sleep(backoff)


if __name__ == "__main__":
    main()
