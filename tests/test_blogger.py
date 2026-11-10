from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from astrion.blogger import (
    BLOGGER_SCOPE,
    DISCOVERY_URL,
    TOKEN_URL,
    BloggerAuthError,
    BloggerClient,
    BloggerDriftError,
    BloggerError,
    discovery_snapshot,
    validate_contract_snapshot,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def http_error(code: int, body: dict) -> HTTPError:
    return HTTPError(
        "https://example.googleapis.com/test",
        code,
        "error",
        {},
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


def discovery_document() -> dict:
    method = lambda params: {"parameters": {name: {} for name in params}}  # noqa: E731
    return {
        "revision": "20261101",
        "title": "Blogger API v3",
        "resources": {
            "posts": {
                "methods": {
                    "get": method(["blogId", "postId"]),
                    "insert": method(["blogId", "isDraft"]),
                    "list": method(["blogId", "maxResults", "status"]),
                    "patch": method(["blogId", "postId"]),
                    "publish": method(["blogId", "postId"]),
                }
            }
        },
    }


def make_client(**overrides) -> BloggerClient:
    values = {
        "blog_id": "blog-1",
        "client_id": "client",
        "client_secret": "secret",
        "refresh_token": "refresh",
        "timeout": 5,
        "max_retries": 0,
    }
    values.update(overrides)
    return BloggerClient(**values)


def test_from_env_requires_every_credential(monkeypatch):
    for name in (
        "BLOGGER_BLOG_ID",
        "BLOGGER_CLIENT_ID",
        "BLOGGER_CLIENT_SECRET",
        "BLOGGER_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(BloggerAuthError, match="BLOGGER_BLOG_ID"):
        BloggerClient.from_env()


def test_from_env_reads_credentials(monkeypatch):
    monkeypatch.setenv("BLOGGER_BLOG_ID", "blog")
    monkeypatch.setenv("BLOGGER_CLIENT_ID", "id")
    monkeypatch.setenv("BLOGGER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("BLOGGER_REFRESH_TOKEN", "refresh")
    client = BloggerClient.from_env()
    assert client.blog_id == "blog"
    assert client.client_id == "id"
    assert client.refresh_token == "refresh"


def test_access_token_is_cached(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=30):
        calls.append(request)
        return FakeResponse({"access_token": "token-1", "expires_in": 3600})

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    client = make_client()
    assert client.access_token() == "token-1"
    assert client.access_token() == "token-1"
    assert len(calls) == 1
    body = calls[0].data.decode("utf-8")
    assert "grant_type=refresh_token" in body
    assert BLOGGER_SCOPE not in body


def test_token_failure_is_an_auth_error(monkeypatch):
    def fake_urlopen(request, timeout=30):
        raise http_error(
            400,
            {
                "error": {
                    "message": "invalid_grant",
                    "errors": [{"reason": "invalid_grant"}],
                }
            },
        )

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    with pytest.raises(BloggerAuthError, match="invalid_grant"):
        make_client().access_token()


def test_insert_post_sends_a_valid_request(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=30):
        calls.append(request)
        if request.full_url == TOKEN_URL:
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        return FakeResponse({"id": "post-9", "url": "https://blog/9.html"})

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    client = make_client()
    post = client.insert_post(
        title="A title", content="<p>Body</p>", labels=["Physics"], is_draft=True
    )
    assert post["id"] == "post-9"
    request = calls[-1]
    assert "/blogs/blog-1/posts/" in request.full_url
    assert "isDraft=true" in request.full_url
    assert request.get_header("Authorization") == "Bearer token"
    body = json.loads(request.data.decode("utf-8"))
    assert body["title"] == "A title"
    assert body["labels"] == ["Physics"]
    assert body["content"] == "<p>Body</p>"


def test_insert_response_without_id_is_drift(monkeypatch):
    def fake_urlopen(request, timeout=30):
        if request.full_url == TOKEN_URL:
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        return FakeResponse({"url": "https://blog/9.html"})

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    with pytest.raises(BloggerDriftError):
        make_client().insert_post(
            title="A title", content="<p>Body</p>", labels=[], is_draft=True
        )


def test_publish_response_without_id_is_drift(monkeypatch):
    def fake_urlopen(request, timeout=30):
        if request.full_url == TOKEN_URL:
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        return FakeResponse({"url": "https://blog/9.html"})

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    with pytest.raises(BloggerDriftError):
        make_client().publish_post("9")


def test_forbidden_is_an_auth_error(monkeypatch):
    def fake_urlopen(request, timeout=30):
        if request.full_url == TOKEN_URL:
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        raise http_error(
            403,
            {
                "error": {
                    "message": "The caller does not have permission",
                    "errors": [{"reason": "forbidden"}],
                }
            },
        )

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    with pytest.raises(BloggerAuthError, match="permission"):
        make_client().get_blog()


def test_transient_error_is_retryable(monkeypatch):
    def fake_urlopen(request, timeout=30):
        if request.full_url == TOKEN_URL:
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        raise http_error(
            503,
            {
                "error": {
                    "message": "backend unavailable",
                    "errors": [{"reason": "backendError"}],
                }
            },
        )

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    with pytest.raises(BloggerError) as caught:
        make_client().get_blog()
    assert caught.value.retryable is True
    assert caught.value.status == 503
    assert caught.value.to_dict()["reason"] == "backendError"


def test_network_error_is_retryable(monkeypatch):
    def fake_urlopen(request, timeout=30):
        raise URLError("offline")

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    client = make_client()
    client._access_token = "cached"
    client._expires_at = 9_999_999_999
    with pytest.raises(BloggerError) as caught:
        client.get_blog()
    assert caught.value.retryable is True


def test_get_blog_requires_an_id(monkeypatch):
    def fake_urlopen(request, timeout=30):
        if request.full_url == TOKEN_URL:
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        return FakeResponse({"name": "Astrion"})

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    with pytest.raises(BloggerDriftError, match="no id"):
        make_client().get_blog()


def test_list_posts_requires_a_list(monkeypatch):
    def fake_urlopen(request, timeout=30):
        if request.full_url == TOKEN_URL:
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        return FakeResponse({"items": {"id": "post"}})

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    with pytest.raises(BloggerDriftError, match="non-list"):
        make_client().list_posts()


def test_find_recent_by_marker_searches_live_and_draft(monkeypatch):
    def fake_urlopen(request, timeout=30):
        if request.full_url == TOKEN_URL:
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        if "status=live" in request.full_url:
            return FakeResponse({"items": []})
        return FakeResponse(
            {
                "items": [
                    {
                        "id": "draft-1",
                        "content": '<div data-astrion-topic="physics.rayleigh-scattering-sky"></div>',
                    }
                ]
            }
        )

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    post = make_client().find_recent_by_marker("physics.rayleigh-scattering-sky")
    assert post is not None
    assert post["id"] == "draft-1"


def test_discovery_snapshot_shape():
    snapshot = discovery_snapshot(discovery_document())
    assert snapshot["schema"] == "astrion.blogger-contract/v1"
    assert snapshot["revision"] == "20261101"
    assert sorted(snapshot["methods"]) == ["get", "insert", "list", "patch", "publish"]
    assert snapshot["methods"]["insert"] == ["blogId", "isDraft"]
    assert len(snapshot["sha256"]) == 64


def test_contract_snapshot_accepts_the_required_surface():
    validate_contract_snapshot(discovery_snapshot(discovery_document()))


def test_contract_snapshot_rejects_a_missing_parameter():
    discovery = discovery_document()
    del discovery["resources"]["posts"]["methods"]["insert"]["parameters"]["isDraft"]
    snapshot = discovery_snapshot(discovery)
    assert "isDraft" not in snapshot["methods"]["insert"]
    with pytest.raises(BloggerDriftError, match="isDraft"):
        validate_contract_snapshot(snapshot)


def test_contract_snapshot_rejects_a_missing_method():
    discovery = discovery_document()
    del discovery["resources"]["posts"]["methods"]["publish"]
    snapshot = discovery_snapshot(discovery)
    with pytest.raises(BloggerDriftError, match="missing methods: publish"):
        validate_contract_snapshot(snapshot)


def test_contract_snapshot_rejects_an_empty_surface():
    with pytest.raises(BloggerDriftError, match="no methods"):
        validate_contract_snapshot({"methods": {}})
    with pytest.raises(BloggerDriftError, match="no methods"):
        validate_contract_snapshot({})


def test_check_contract_fails_when_the_live_api_loses_isDraft(monkeypatch, tmp_path):
    discovery = discovery_document()
    del discovery["resources"]["posts"]["methods"]["insert"]["parameters"]["isDraft"]
    monkeypatch.setattr(
        "astrion.blogger.urlopen", lambda request, timeout=30: FakeResponse(discovery)
    )
    with pytest.raises(BloggerDriftError, match="isDraft"):
        make_client().check_contract(tmp_path / "contract.json")


def test_check_contract_detects_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "astrion.blogger.urlopen", lambda request, timeout=30: FakeResponse(discovery_document())
    )
    client = make_client()
    result = client.check_contract(tmp_path / "contract.json")
    assert result["status"] == "missing"
    assert result["current"]["revision"] == "20261101"


def test_check_contract_detects_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "astrion.blogger.urlopen", lambda request, timeout=30: FakeResponse(discovery_document())
    )
    client = make_client()
    path = tmp_path / "contract.json"
    snapshot = client.refresh_contract(path)
    assert path.exists()
    assert client.check_contract(path)["status"] == "unchanged"

    snapshot["sha256"] = "0" * 64
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert client.check_contract(path)["status"] == "changed"


def test_healthcheck_reports_blog_and_contract(monkeypatch, tmp_path):
    document = discovery_document()

    def fake_urlopen(request, timeout=30):
        url = request.full_url
        if url == TOKEN_URL:
            return FakeResponse({"access_token": "token", "expires_in": 3600})
        if url == DISCOVERY_URL:
            return FakeResponse(document)
        if "/posts" in url:
            return FakeResponse({"items": [{"id": "post-1"}]})
        return FakeResponse(
            {"id": "blog-1", "name": "Astrion", "url": "https://astrion.blogspot.com/"}
        )

    monkeypatch.setattr("astrion.blogger.urlopen", fake_urlopen)
    path = tmp_path / "contract.json"
    report = make_client().healthcheck(contract_path=path)
    assert report["ok"] is True
    assert report["blog"]["id"] == "blog-1"
    assert report["posts_visible"] == 1
    assert report["contract"]["status"] == "missing"
