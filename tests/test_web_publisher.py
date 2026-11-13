from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from astrion.blogger import BloggerAuthError, BloggerClient, BloggerDriftError, BloggerError
from astrion.config import Settings
from astrion.web_publisher import (
    SENTINEL,
    WebBloggerClient,
    error_from_result,
    make_client,
    parse_result,
)

STATE = {
    "cookies": [
        {
            "name": "SID",
            "value": "secret",
            "domain": ".google.com",
            "path": "/",
            "expires": -1,
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        }
    ],
    "origins": [],
}


@pytest.fixture
def web_settings(settings: Settings) -> Settings:
    settings.blogger.blog_id = "7103426249941439830"
    settings.blogger.blog_url = "https://sciencew1thoutlimits.blogspot.com/"
    settings.blogger.web.timeout_seconds = 60
    (settings.root / "web").mkdir(parents=True, exist_ok=True)
    for name in ("publishBlogger.mjs", "findPost.mjs", "sessionCheck.mjs"):
        (settings.root / "web" / name).write_text("// stub\n", encoding="utf-8")
    return settings


@pytest.fixture(autouse=True)
def _clear_session_env(monkeypatch):
    monkeypatch.delenv("BLOGGER_STORAGE_STATE", raising=False)
    monkeypatch.delenv("BLOGGER_BLOG_ID", raising=False)
    monkeypatch.delenv("BLOGGER_BLOG_URL", raising=False)


def write_session(client: WebBloggerClient) -> Path:
    client.storage_state.parent.mkdir(parents=True, exist_ok=True)
    client.storage_state.write_text(json.dumps(STATE), encoding="utf-8")
    return client.storage_state


class Completed:
    def __init__(self, payload, *, returncode=0, stderr=""):
        self.stdout = f"noise\n{SENTINEL}{json.dumps(payload)}\n"
        self.stderr = stderr
        self.returncode = returncode


# ----------------------------------------------------------------- sentinel parse
def test_parse_result_reads_the_last_sentinel_line():
    stdout = (
        "starting\n"
        f'{SENTINEL}{{"ok":false,"errorCode":"OLD"}}\n'
        f'{SENTINEL}{{"ok":true,"postId":"123"}}\n'
    )
    assert parse_result(stdout) == {"ok": True, "postId": "123"}


def test_parse_result_returns_empty_when_no_sentinel():
    assert parse_result("just logs") == {}


def test_parse_result_rejects_invalid_json():
    with pytest.raises(BloggerError) as exc:
        parse_result(f"{SENTINEL}not-json\n")
    assert exc.value.reason == "BLOGGER_BAD_RESULT"


# ------------------------------------------------------------------- error mapping
@pytest.mark.parametrize("code", ["BLOGGER_SESSION_REQUIRED", "BLOGGER_SESSION_EXPIRED"])
def test_session_codes_map_to_auth_error(code):
    error = error_from_result({"error": "expired", "errorCode": code})
    assert isinstance(error, BloggerAuthError)
    assert error.reason == code
    assert error.retryable is False


@pytest.mark.parametrize(
    "code",
    [
        "BLOGGER_SELECTOR_MISSING",
        "BLOGGER_DRAFT_BUTTON_MISSING",
        "BLOGGER_BODY_NOT_SET",
        "BLOGGER_TITLE_NOT_SET",
        "BLOGGER_NAVIGATION_TIMEOUT",
        "BLOGGER_BAD_CONFIG",
    ],
)
def test_drift_codes_map_to_drift_error(code):
    error = error_from_result({"error": "ui changed", "errorCode": code})
    assert isinstance(error, BloggerDriftError)
    assert error.reason == code


def test_unconfirmed_save_is_retryable_so_the_next_run_reconciles():
    error = error_from_result(
        {"error": "no confirmation", "errorCode": "BLOGGER_SAVE_UNCONFIRMED"}
    )
    assert type(error) is BloggerError
    assert error.retryable is True


def test_error_messages_are_scrubbed():
    error = error_from_result({"error": "failed for person@example.com", "errorCode": "X"})
    assert "person@example.com" not in str(error)
    assert "***@***" in str(error)


# ---------------------------------------------------------------- session restore
def test_materialize_session_writes_the_base64_secret(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    monkeypatch.setenv("BLOGGER_STORAGE_STATE", base64.b64encode(json.dumps(STATE).encode()).decode())
    path = client.materialize_session()
    assert path == web_settings.web_storage_state_path
    assert json.loads(path.read_text(encoding="utf-8"))["cookies"][0]["name"] == "SID"


def test_materialize_session_rejects_bad_base64(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    monkeypatch.setenv("BLOGGER_STORAGE_STATE", "!!!not base64!!!")
    with pytest.raises(BloggerAuthError) as exc:
        client.materialize_session()
    assert exc.value.reason == "BLOGGER_SESSION_REQUIRED"


def test_materialize_session_rejects_cookie_less_state(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    monkeypatch.setenv(
        "BLOGGER_STORAGE_STATE",
        base64.b64encode(json.dumps({"cookies": [], "origins": []}).encode()).decode(),
    )
    with pytest.raises(BloggerAuthError):
        client.materialize_session()


def test_materialize_session_requires_a_local_file(web_settings):
    client = WebBloggerClient(web_settings)
    with pytest.raises(BloggerAuthError) as exc:
        client.materialize_session()
    assert "npm run login" in str(exc.value)


# -------------------------------------------------------------------- publishing
def test_insert_post_sends_a_draft_payload(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    write_session(client)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs, json.loads(Path(command[2]).read_text(encoding="utf-8"))))
        return Completed(
            {
                "ok": True,
                "mode": "draft",
                "postId": "4293092910592619318",
                "url": "",
                "editUrl": "https://www.blogger.com/blog/post/edit/1/4293092910592619318",
                "status": "DRAFT",
                "publishedAt": "",
                "title": "Why the sky is blue",
                "warnings": ["labels were skipped"],
            }
        )

    monkeypatch.setattr("astrion.web_publisher.subprocess.run", fake_run)

    post = client.insert_post(
        title="Why the sky is blue",
        content="<div class=\"astrion-post\">body</div>",
        labels=["Physics", "Optics"],
        is_draft=True,
    )

    assert post["id"] == "4293092910592619318"
    assert post["status"] == "draft"
    assert post["url"].endswith("4293092910592619318")
    assert post["warnings"] == ["labels were skipped"]

    command, kwargs, payload = calls[0]
    assert command[0] == "node"
    assert command[1].endswith("publishBlogger.mjs")
    assert payload["mode"] == "draft"
    assert payload["title"] == "Why the sky is blue"
    assert payload["contentHtml"].startswith("<div")
    assert payload["labels"] == ["Physics", "Optics"]
    assert payload["blogId"] == "7103426249941439830"
    assert kwargs["timeout"] == 60
    assert kwargs["env"]["BLOGGER_STORAGE_STATE_PATH"] == str(client.storage_state)
    # The temporary payload file is removed after the run.
    assert not Path(command[2]).exists()


def test_insert_post_sends_a_live_publish_payload(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    write_session(client)

    monkeypatch.setattr(
        "astrion.web_publisher.subprocess.run",
        lambda command, **kwargs: Completed(
            {
                "ok": True,
                "mode": "publish",
                "postId": "99",
                "url": "https://sciencew1thoutlimits.blogspot.com/2026/11/post.html",
                "status": "LIVE",
                "publishedAt": "2026-11-13T10:00:00Z",
            }
        ),
    )
    post = client.insert_post(title="T", content="<p>x</p>", labels=[], is_draft=False)
    assert post["status"] == "live"
    assert post["published"] == "2026-11-13T10:00:00Z"
    assert post["url"].startswith("https://sciencew1thoutlimits")


def test_insert_post_requires_a_post_id(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    write_session(client)
    monkeypatch.setattr(
        "astrion.web_publisher.subprocess.run",
        lambda command, **kwargs: Completed({"ok": True, "postId": "", "status": "DRAFT"}),
    )
    with pytest.raises(BloggerDriftError) as exc:
        client.insert_post(title="T", content="<p>x</p>", labels=[], is_draft=True)
    assert exc.value.reason == "BLOGGER_POST_ID_MISSING"


def test_insert_post_maps_a_failed_result(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    write_session(client)
    monkeypatch.setattr(
        "astrion.web_publisher.subprocess.run",
        lambda command, **kwargs: Completed(
            {"ok": False, "error": "sign in", "errorCode": "BLOGGER_SESSION_EXPIRED"}
        ),
    )
    with pytest.raises(BloggerAuthError):
        client.insert_post(title="T", content="<p>x</p>", labels=[], is_draft=True)


def test_run_without_a_result_raises_node_error(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    write_session(client)
    monkeypatch.setattr(
        "astrion.web_publisher.subprocess.run",
        lambda command, **kwargs: Completed({}, returncode=1),
    )
    # A missing sentinel means the process crashed before emitting a result.
    monkeypatch.setattr(
        "astrion.web_publisher.parse_result", lambda stdout: {}
    )
    with pytest.raises(BloggerError) as exc:
        client.insert_post(title="T", content="<p>x</p>", labels=[], is_draft=True)
    assert exc.value.reason == "BLOGGER_NODE_ERROR"


def test_missing_node_binary_is_reported(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    write_session(client)

    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError("node")

    monkeypatch.setattr("astrion.web_publisher.subprocess.run", fake_run)
    with pytest.raises(BloggerError) as exc:
        client.insert_post(title="T", content="<p>x</p>", labels=[], is_draft=True)
    assert exc.value.reason == "BLOGGER_NODE_MISSING"


def test_timeout_is_reported_as_retryable(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    write_session(client)
    import subprocess

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("node", 60)

    monkeypatch.setattr("astrion.web_publisher.subprocess.run", fake_run)
    with pytest.raises(BloggerError) as exc:
        client.insert_post(title="T", content="<p>x</p>", labels=[], is_draft=True)
    assert exc.value.reason == "BLOGGER_NODE_TIMEOUT"
    assert exc.value.retryable is True


# ------------------------------------------------------------------- find post
def test_find_recent_by_marker_uses_the_title(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    write_session(client)
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(json.loads(Path(command[2]).read_text(encoding="utf-8")))
        return Completed(
            {
                "ok": True,
                "found": True,
                "postId": "77",
                "status": "draft",
                "url": "",
                "editUrl": "https://www.blogger.com/blog/post/edit/1/77",
            }
        )

    monkeypatch.setattr("astrion.web_publisher.subprocess.run", fake_run)
    post = client.find_recent_by_marker("physics.rayleigh", title="Why the sky is blue")
    assert captured["title"] == "Why the sky is blue"
    assert post["id"] == "77"
    assert post["status"] == "draft"
    assert post["url"].endswith("77")


def test_find_recent_by_marker_without_a_title_skips_the_network(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)

    def explode(*_args, **_kwargs):
        raise AssertionError("should not run node without a title")

    monkeypatch.setattr("astrion.web_publisher.subprocess.run", explode)
    assert client.find_recent_by_marker("physics.rayleigh") is None


# ------------------------------------------------------------------ healthcheck
def test_healthcheck_reports_a_missing_session(web_settings):
    client = WebBloggerClient(web_settings)
    report = client.healthcheck()
    assert report["ok"] is False
    assert report["backend"] == "web"
    assert report["error"]["code"] == "BLOGGER_SESSION_REQUIRED"


def test_healthcheck_returns_the_session_report(web_settings, monkeypatch):
    client = WebBloggerClient(web_settings)
    write_session(client)
    monkeypatch.setattr(
        "astrion.web_publisher.subprocess.run",
        lambda command, **kwargs: Completed(
            {
                "ok": True,
                "session": "ok",
                "blogId": "7103426249941439830",
                "selectors": {"titleInput": "input"},
                "broken": [],
            }
        ),
    )
    report = client.healthcheck()
    assert report["ok"] is True
    assert report["session"] == "ok"
    assert report["contract"]["status"] == "not_applicable"


# ---------------------------------------------------------------- client factory
def test_make_client_defaults_to_the_web_backend(web_settings):
    assert web_settings.blogger.backend == "web"
    assert isinstance(make_client(web_settings), WebBloggerClient)


def test_make_client_can_still_build_the_api_backend(web_settings, monkeypatch):
    web_settings.blogger.backend = "api"
    for name, value in {
        "BLOGGER_BLOG_ID": "1",
        "BLOGGER_CLIENT_ID": "id",
        "BLOGGER_CLIENT_SECRET": "secret",
        "BLOGGER_REFRESH_TOKEN": "refresh",
    }.items():
        monkeypatch.setenv(name, value)
    assert isinstance(make_client(web_settings), BloggerClient)
