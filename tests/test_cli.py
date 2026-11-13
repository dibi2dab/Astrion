from __future__ import annotations

import json
import shutil
from pathlib import Path

from astrion.cli import main
from astrion.config import load_settings
from astrion.queue import list_queue

from .conftest import ROOT

TOPIC_ID = "physics.rayleigh-scattering-sky"
TARGET = "2030-01-01"


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(ROOT / "content", project / "content")
    (project / "config").mkdir(parents=True)
    shutil.copy(ROOT / "config" / "astrion.yaml", project / "config" / "astrion.yaml")
    return project


def test_validate_command(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "validate"]) == 0
    output = capsys.readouterr()
    assert "0 error(s), 0 warning(s)" in output.out


def test_validate_strict_command(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "validate", "--strict"]) == 0


def test_validate_json_command(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "validate", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["topics"] == 2
    assert payload["errors"] == 0


def test_list_command(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "list"]) == 0
    assert TOPIC_ID in capsys.readouterr().out

    assert main(["--root", str(project), "list", "--category", "Physics", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["id"] == TOPIC_ID


def test_check_duplicates_command(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "check-duplicates"]) == 0
    assert "No duplicate candidates" in capsys.readouterr().out


def test_render_command(tmp_path, capsys):
    project = make_project(tmp_path)
    output = tmp_path / "preview.html"
    assert main(
        ["--root", str(project), "render", TOPIC_ID, "--out", str(output)]
    ) == 0
    assert output.exists()
    assert "astrion-post" in output.read_text(encoding="utf-8")
    assert "words" in capsys.readouterr().out


def test_render_unknown_topic(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "render", "math.nope"]) == 1
    assert "topic not found" in capsys.readouterr().err


def test_plan_command(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(
        ["--root", str(project), "plan", "--date", TARGET, "--json"]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["publish_date"] == TARGET
    assert len(payload["items"]) == 2


def test_build_status_publish_flow(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "build", "--date", TARGET]) == 0
    output = capsys.readouterr().out
    assert "Queued 2 payload(s)" in output

    settings = load_settings(root=project)
    payloads = list_queue(settings)
    assert len(payloads) == 2

    assert main(["--root", str(project), "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"]["pending"] == 2
    assert status["queue"]["queued"] == 2

    queue_id = payloads[0]["queue_id"]
    assert main(
        [
            "--root",
            str(project),
            "publish",
            "--dry-run",
            "--queue-id",
            queue_id,
        ]
    ) == 0
    assert "dry_run" in capsys.readouterr().out


def test_second_build_is_reported(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "build", "--date", TARGET]) == 0
    capsys.readouterr()
    assert main(["--root", str(project), "build", "--date", TARGET]) == 0
    output = capsys.readouterr().out
    assert "Nothing was queued" in output


def test_build_dry_run_output(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "build", "--date", TARGET, "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Dry run" in output
    settings = load_settings(root=project)
    assert list_queue(settings) == []


def test_publish_without_web_session_fails(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path)
    for name in (
        "BLOGGER_BLOG_ID",
        "BLOGGER_CLIENT_ID",
        "BLOGGER_CLIENT_SECRET",
        "BLOGGER_REFRESH_TOKEN",
        "BLOGGER_STORAGE_STATE",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings(root=project)
    settings.queue_dir.mkdir(parents=True, exist_ok=True)
    assert main(["--root", str(project), "publish"]) == 2
    assert "browser session not found" in capsys.readouterr().err


def test_healthcheck_without_web_session_fails(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path)
    for name in (
        "BLOGGER_BLOG_ID",
        "BLOGGER_CLIENT_ID",
        "BLOGGER_CLIENT_SECRET",
        "BLOGGER_REFRESH_TOKEN",
        "BLOGGER_STORAGE_STATE",
    ):
        monkeypatch.delenv(name, raising=False)
    assert main(["--root", str(project), "healthcheck"]) == 2
    assert "browser session not found" in capsys.readouterr().err


def test_session_without_web_session_returns_three(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path)
    monkeypatch.delenv("BLOGGER_STORAGE_STATE", raising=False)
    assert main(["--root", str(project), "session"]) == 3
    err = capsys.readouterr().err
    assert "browser session not found" in err
    assert "BLOGGER_STORAGE_STATE" in err


def test_session_with_the_api_backend_is_rejected(tmp_path, capsys):
    project = make_project(tmp_path)
    config = project / "config" / "astrion.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("backend: web", "backend: api"),
        encoding="utf-8",
    )
    assert main(["--root", str(project), "session"]) == 2
    assert "web backend" in capsys.readouterr().err


def test_bad_config_returns_two(tmp_path, capsys):
    project = make_project(tmp_path)
    (project / "config" / "astrion.yaml").write_text("site: [", encoding="utf-8")
    assert main(["--root", str(project), "validate"]) == 2
    assert "configuration" in capsys.readouterr().err


def test_bad_date_returns_two(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "plan", "--date", "not-a-date"]) == 2
    assert "invalid" in capsys.readouterr().err


def test_auth_without_client_credentials_fails(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path)
    monkeypatch.delenv("BLOGGER_CLIENT_ID", raising=False)
    monkeypatch.delenv("BLOGGER_CLIENT_SECRET", raising=False)
    assert main(["--root", str(project), "auth"]) == 2
    err = capsys.readouterr().err
    assert "BLOGGER_CLIENT_ID" in err
    assert "BLOGGER_CLIENT_SECRET" in err


def test_auth_prints_the_refresh_token(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path)

    def fake_authorize(client_id, client_secret, *, port=8765, open_browser=True, timeout=300):
        assert client_id == "client"
        assert client_secret == "secret"
        return {"refresh_token": "refresh-token-value"}

    monkeypatch.setattr("astrion.auth.authorize", fake_authorize)
    assert main(["--root", str(project), "auth", "--client-id", "client", "--client-secret", "secret"]) == 0
    out = capsys.readouterr().out
    assert "BLOGGER_REFRESH_TOKEN=refresh-token-value" in out
    assert "BLOGGER_BLOG_ID" in out
