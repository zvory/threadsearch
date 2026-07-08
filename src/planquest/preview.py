from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


DEFAULT_PREVIEW_STATE = Path("data/public-preview-state.json")
DEFAULT_PREVIEW_SERVER_LOG = Path("data/public-preview-server.log")
DEFAULT_PREVIEW_TUNNEL_LOG = Path("data/public-preview-tunnel.log")
LOCALTUNNEL_URL_RE = re.compile(r"https://[A-Za-z0-9-]+\.loca\.lt\b")


@dataclass(frozen=True)
class PreviewState:
    started_at_utc: str
    local_base_url: str
    public_base_url: str | None
    server_pid: int | None
    tunnel_pid: int | None
    server_log: str
    tunnel_log: str
    server_command: list[str] = field(default_factory=list)
    tunnel_command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PreviewError(RuntimeError):
    pass


def local_base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def serve_preview_command(
    *,
    db: Path,
    host: str,
    port: int,
    artifact_manifest: Path,
    public_contact: str,
    removal_request_url: str,
    probes: tuple[str, ...],
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "planquest.cli",
        "serve",
        "--db",
        str(db),
        "--host",
        host,
        "--port",
        str(port),
        "--require-launch-ready",
        "--require-artifact-manifest",
        "--artifact-manifest",
        str(artifact_manifest),
        "--public-contact",
        public_contact,
        "--removal-request-url",
        removal_request_url,
    ]
    for probe in probes:
        command.extend(["--probe", probe])
    return command


def localtunnel_command(*, port: int, host: str, subdomain: str | None = None) -> list[str]:
    command = ["npx", "--yes", "localtunnel", "--port", str(port), "--local-host", host]
    if subdomain:
        command.extend(["--subdomain", subdomain])
    return command


def extract_public_preview_url(log_text: str) -> str | None:
    matches = LOCALTUNNEL_URL_RE.findall(log_text)
    return matches[-1] if matches else None


def read_public_preview_url(log_path: Path) -> str | None:
    if not log_path.exists():
        return None
    return extract_public_preview_url(log_path.read_text(encoding="utf-8", errors="replace"))


def pid_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_http_ok(base_url: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url.rstrip('/')}/healthz", timeout=2) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except URLError as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise PreviewError(f"local server did not become healthy at {base_url}: {last_error or 'timed out'}")


def wait_for_public_preview_url(log_path: Path, *, timeout_seconds: float) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        url = read_public_preview_url(log_path)
        if url:
            return url
        time.sleep(0.25)
    raise PreviewError(f"tunnel URL did not appear in {log_path} within {timeout_seconds:g}s")


def start_public_preview(
    *,
    db: Path,
    host: str,
    port: int,
    artifact_manifest: Path,
    public_contact: str,
    removal_request_url: str,
    probes: tuple[str, ...],
    state_path: Path = DEFAULT_PREVIEW_STATE,
    server_log: Path = DEFAULT_PREVIEW_SERVER_LOG,
    tunnel_log: Path = DEFAULT_PREVIEW_TUNNEL_LOG,
    timeout_seconds: float = 20.0,
    skip_server: bool = False,
    skip_tunnel: bool = False,
    force: bool = False,
    subdomain: str | None = None,
) -> PreviewState:
    if not skip_tunnel and shutil.which("npx") is None:
        raise PreviewError("npx is required for the default localtunnel preview; install Node/npm or pass --no-tunnel")
    if state_path.exists() and not force:
        existing = load_preview_state(state_path)
        if existing and (pid_running(existing.server_pid) or pid_running(existing.tunnel_pid)):
            raise PreviewError(f"preview already appears to be running from {state_path}; use --force or preview-stop")

    state_path.parent.mkdir(parents=True, exist_ok=True)
    server_log.parent.mkdir(parents=True, exist_ok=True)
    tunnel_log.parent.mkdir(parents=True, exist_ok=True)
    server_command = serve_preview_command(
        db=db,
        host=host,
        port=port,
        artifact_manifest=artifact_manifest,
        public_contact=public_contact,
        removal_request_url=removal_request_url,
        probes=probes,
    )
    tunnel_command = localtunnel_command(port=port, host=host, subdomain=subdomain)

    local_url = local_base_url(host, port)
    server_pid: int | None = None
    tunnel_pid: int | None = None
    public_url: str | None = None

    if not skip_server:
        server_handle = server_log.open("ab")
        server = subprocess.Popen(
            server_command,
            stdout=server_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        server_handle.close()
        server_pid = server.pid

    wait_for_http_ok(local_url, timeout_seconds=timeout_seconds)

    if not skip_tunnel:
        tunnel_handle = tunnel_log.open("ab")
        tunnel = subprocess.Popen(
            tunnel_command,
            stdout=tunnel_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        tunnel_handle.close()
        tunnel_pid = tunnel.pid
        public_url = wait_for_public_preview_url(tunnel_log, timeout_seconds=timeout_seconds)

    state = PreviewState(
        started_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        local_base_url=local_url,
        public_base_url=public_url,
        server_pid=server_pid,
        tunnel_pid=tunnel_pid,
        server_log=str(server_log),
        tunnel_log=str(tunnel_log),
        server_command=server_command,
        tunnel_command=[] if skip_tunnel else tunnel_command,
    )
    write_preview_state(state_path, state)
    return state


def load_preview_state(path: Path) -> PreviewState | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PreviewState(
        started_at_utc=str(payload.get("started_at_utc") or ""),
        local_base_url=str(payload.get("local_base_url") or ""),
        public_base_url=payload.get("public_base_url"),
        server_pid=parse_pid(payload.get("server_pid")),
        tunnel_pid=parse_pid(payload.get("tunnel_pid")),
        server_log=str(payload.get("server_log") or DEFAULT_PREVIEW_SERVER_LOG),
        tunnel_log=str(payload.get("tunnel_log") or DEFAULT_PREVIEW_TUNNEL_LOG),
        server_command=list(payload.get("server_command") or []),
        tunnel_command=list(payload.get("tunnel_command") or []),
    )


def parse_pid(value: object) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def write_preview_state(path: Path, state: PreviewState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def preview_status(path: Path = DEFAULT_PREVIEW_STATE, tunnel_log: Path | None = None) -> dict[str, Any]:
    state = load_preview_state(path)
    effective_tunnel_log = tunnel_log or (Path(state.tunnel_log) if state else DEFAULT_PREVIEW_TUNNEL_LOG)
    public_url = (state.public_base_url if state else None) or read_public_preview_url(effective_tunnel_log)
    return {
        "state_path": str(path),
        "state_exists": state is not None,
        "started_at_utc": state.started_at_utc if state else None,
        "local_base_url": state.local_base_url if state else None,
        "public_base_url": public_url,
        "server_pid": state.server_pid if state else None,
        "server_running": pid_running(state.server_pid) if state else False,
        "tunnel_pid": state.tunnel_pid if state else None,
        "tunnel_running": pid_running(state.tunnel_pid) if state else False,
        "server_log": state.server_log if state else None,
        "tunnel_log": str(effective_tunnel_log),
    }


def stop_public_preview(path: Path = DEFAULT_PREVIEW_STATE) -> dict[str, Any]:
    state = load_preview_state(path)
    if state is None:
        return {"state_path": str(path), "state_exists": False, "stopped": []}
    stopped: list[dict[str, Any]] = []
    for label, pid in (("tunnel", state.tunnel_pid), ("server", state.server_pid)):
        if not pid_running(pid):
            stopped.append({"name": label, "pid": pid, "running_before": False, "signal_sent": None})
            continue
        assert pid is not None
        os.kill(pid, signal.SIGTERM)
        stopped.append({"name": label, "pid": pid, "running_before": True, "signal_sent": "SIGTERM"})
    return {"state_path": str(path), "state_exists": True, "stopped": stopped}
