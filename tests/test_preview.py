import os
from pathlib import Path

from planquest.preview import (
    PreviewState,
    extract_public_preview_url,
    local_base_url,
    localtunnel_command,
    preview_status,
    serve_preview_command,
    write_preview_state,
)


def test_extract_public_preview_url_returns_last_loca_lt_url() -> None:
    text = """
    booting tunnel
    your url is: https://first-preview.loca.lt
    restarted
    your url is: https://second-preview.loca.lt
    """

    assert extract_public_preview_url(text) == "https://second-preview.loca.lt"


def test_preview_status_reads_state_and_tunnel_log(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    tunnel_log = tmp_path / "tunnel.log"
    tunnel_log.write_text("your url is: https://visible-preview.loca.lt\n", encoding="utf-8")
    write_preview_state(
        state_path,
        PreviewState(
            started_at_utc="2026-07-08T00:00:00Z",
            local_base_url="http://127.0.0.1:8765",
            public_base_url=None,
            server_pid=os.getpid(),
            tunnel_pid=None,
            server_log=str(tmp_path / "server.log"),
            tunnel_log=str(tunnel_log),
            server_command=["python", "-m", "planquest.cli", "serve"],
            tunnel_command=["npx", "--yes", "localtunnel"],
        ),
    )

    status = preview_status(state_path)

    assert status["state_exists"] is True
    assert status["local_base_url"] == "http://127.0.0.1:8765"
    assert status["public_base_url"] == "https://visible-preview.loca.lt"
    assert status["server_running"] is True
    assert status["tunnel_running"] is False


def test_preview_status_without_state_can_parse_tunnel_log(tmp_path: Path) -> None:
    tunnel_log = tmp_path / "tunnel.log"
    tunnel_log.write_text("your url is: https://log-only-preview.loca.lt\n", encoding="utf-8")

    status = preview_status(tmp_path / "missing-state.json", tunnel_log)

    assert status["state_exists"] is False
    assert status["public_base_url"] == "https://log-only-preview.loca.lt"


def test_preview_commands_keep_manifest_gates_and_loopback_binding() -> None:
    command = serve_preview_command(
        db=Path("dist/thread-search-public/thread-search.sqlite"),
        host="127.0.0.1",
        port=8765,
        artifact_manifest=Path("dist/thread-search-public/manifest.json"),
        public_contact="mailto:operator@example.org",
        removal_request_url="https://operator.example.org/removal",
        probes=("Soviet", "Cuba"),
    )

    assert command[:3] == [command[0], "-m", "planquest.cli"]
    assert "--host" in command
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert "--require-launch-ready" in command
    assert "--require-artifact-manifest" in command
    assert "--private-fulltext" not in command
    assert command.count("--probe") == 2


def test_localtunnel_command_can_request_subdomain() -> None:
    assert local_base_url("127.0.0.1", 8765) == "http://127.0.0.1:8765"
    assert localtunnel_command(port=8765, host="127.0.0.1", subdomain="thread-search-review") == [
        "npx",
        "--yes",
        "localtunnel",
        "--port",
        "8765",
        "--local-host",
        "127.0.0.1",
        "--subdomain",
        "thread-search-review",
    ]
