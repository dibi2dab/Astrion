"""Minimal, dependency-free Blogger API v3 client.

Only the endpoints Astrion needs are implemented. The client validates the
response shape, classifies errors, retries transient failures, and can compare
Google's live discovery document with a committed contract snapshot. That is
how a future API change becomes a visible, isolated failure instead of a
silently malformed post.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://oauth2.googleapis.com/token"
DISCOVERY_URL = "https://blogger.googleapis.com/$discovery/rest?version=v3"
BLOGGER_SCOPE = "https://www.googleapis.com/auth/blogger"
USER_AGENT = "AstrionPublisher/0.1 (+https://github.com)"
REQUIRED_POST_METHODS = ("get", "insert", "list", "patch", "publish")
REQUIRED_INSERT_PARAMETERS = ("blogId", "isDraft")


class BloggerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        reason: str = "",
        retryable: bool = False,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.retryable = retryable
        self.body = body[:2000]

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "status": self.status,
            "reason": self.reason,
            "retryable": self.retryable,
            "body": self.body,
        }


class BloggerAuthError(BloggerError):
    pass


class BloggerDriftError(BloggerError):
    pass


@dataclass
class BlogInfo:
    id: str
    name: str
    url: str


class BloggerClient:
    def __init__(
        self,
        *,
        blog_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        api_base: str = "https://www.googleapis.com/blogger/v3",
        timeout: int = 30,
        max_retries: int = 4,
    ) -> None:
        self.blog_id = blog_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._access_token = ""
        self._expires_at = 0.0

    @classmethod
    def from_env(cls, *, timeout: int = 30, max_retries: int = 4) -> "BloggerClient":
        names = [
            "BLOGGER_BLOG_ID",
            "BLOGGER_CLIENT_ID",
            "BLOGGER_CLIENT_SECRET",
            "BLOGGER_REFRESH_TOKEN",
        ]
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise BloggerAuthError(
                "missing Blogger credentials: " + ", ".join(missing)
                + ". Set them as environment variables or GitHub Secrets."
            )
        return cls(
            blog_id=os.environ["BLOGGER_BLOG_ID"],
            client_id=os.environ["BLOGGER_CLIENT_ID"],
            client_secret=os.environ["BLOGGER_CLIENT_SECRET"],
            refresh_token=os.environ["BLOGGER_REFRESH_TOKEN"],
            api_base=os.environ.get("BLOGGER_API_BASE", "https://www.googleapis.com/blogger/v3"),
            timeout=timeout,
            max_retries=max_retries,
        )

    # ------------------------------------------------------------------ auth
    def access_token(self, *, force: bool = False) -> str:
        if not force and self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        data = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = Request(
            TOKEN_URL,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = _read_error(exc)
            reason = _error_reason(body)
            raise BloggerAuthError(
                f"could not refresh the OAuth token ({exc.code} {reason}): {_error_message(body)}",
                status=exc.code,
                reason=reason,
                body=body,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise BloggerAuthError(f"token endpoint is unreachable: {exc}") from exc

        token = str(payload.get("access_token") or "")
        if not token:
            raise BloggerAuthError("token endpoint returned no access_token")
        self._access_token = token
        self._expires_at = time.time() + int(payload.get("expires_in") or 3600)
        return token

    # ------------------------------------------------------------------ HTTP
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        auth: bool = True,
        retries: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self.api_base}{path}"
        if params:
            cleaned = {key: value for key, value in params.items() if value is not None}
            url = f"{url}?{urlencode(cleaned)}"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        attempts = 0 if retries is None else retries
        refreshed = False

        while True:
            headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
            if payload is not None:
                headers["Content-Type"] = "application/json; charset=utf-8"
            if auth:
                headers["Authorization"] = f"Bearer {self.access_token()}"
            request = Request(url, data=payload, headers=headers, method=method)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw.strip() else {}
            except HTTPError as exc:
                raw = _read_error(exc)
                reason = _error_reason(raw)
                retryable = exc.code in {429, 500, 502, 503, 504} or reason in {
                    "rateLimitExceeded",
                    "userRateLimitExceeded",
                    "backendError",
                    "internalError",
                }
                if exc.code == 401 and auth and not refreshed:
                    refreshed = True
                    self.access_token(force=True)
                    continue
                if exc.code in {401, 403} and not retryable:
                    raise BloggerAuthError(
                        f"Blogger rejected the credentials ({exc.code} {reason}): {_error_message(raw)}",
                        status=exc.code,
                        reason=reason,
                        body=raw,
                    ) from exc
                if retryable and attempts < self.max_retries:
                    _sleep_for_retry(exc, attempts)
                    attempts += 1
                    continue
                raise BloggerError(
                    f"Blogger request failed ({exc.code} {reason}): {_error_message(raw)}",
                    status=exc.code,
                    reason=reason,
                    retryable=retryable,
                    body=raw,
                ) from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempts < self.max_retries:
                    time.sleep(min(30, 2 ** attempts))
                    attempts += 1
                    continue
                raise BloggerError(f"Blogger API is unreachable: {exc}", retryable=True) from exc

    # ------------------------------------------------------------------ API
    def get_blog(self) -> BlogInfo:
        payload = self._request("GET", f"/blogs/{self.blog_id}")
        blog_id = str(payload.get("id") or "")
        if not blog_id:
            raise BloggerDriftError("blogs.get response has no id")
        return BlogInfo(
            id=blog_id,
            name=str(payload.get("name") or ""),
            url=str(payload.get("url") or ""),
        )

    def list_posts(
        self,
        *,
        max_results: int = 10,
        fetch_bodies: bool = False,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            f"/blogs/{self.blog_id}/posts",
            params={
                "maxResults": max(1, min(max_results, 500)),
                "fetchBodies": "true" if fetch_bodies else "false",
                "status": status,
            },
        )
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise BloggerDriftError("posts.list response has a non-list items field")
        return [item for item in items if isinstance(item, dict)]

    def insert_post(
        self,
        *,
        title: str,
        content: str,
        labels: list[str],
        is_draft: bool,
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            f"/blogs/{self.blog_id}/posts/",
            params={"isDraft": "true" if is_draft else "false"},
            body={
                "kind": "blogger#post",
                "title": title,
                "content": content,
                "labels": labels,
            },
        )
        if not payload.get("id") or not payload.get("url"):
            raise BloggerDriftError("posts.insert response is missing id or url")
        return payload

    def publish_post(self, post_id: str) -> dict[str, Any]:
        payload = self._request("POST", f"/blogs/{self.blog_id}/posts/{post_id}/publish")
        if not payload.get("id"):
            raise BloggerDriftError("posts.publish response is missing id")
        return payload

    def update_post(
        self,
        post_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if content is not None:
            body["content"] = content
        if labels is not None:
            body["labels"] = labels
        payload = self._request("PATCH", f"/blogs/{self.blog_id}/posts/{post_id}", body=body)
        if not payload.get("id"):
            raise BloggerDriftError("posts.patch response is missing id")
        return payload

    def get_post(self, post_id: str) -> dict[str, Any]:
        return self._request("GET", f"/blogs/{self.blog_id}/posts/{post_id}")

    def delete_post(self, post_id: str) -> None:
        self._request("DELETE", f"/blogs/{self.blog_id}/posts/{post_id}")

    def find_recent_by_marker(self, topic_id: str, *, limit: int = 50) -> dict[str, Any] | None:
        marker = f'data-astrion-topic="{topic_id}"'
        seen: set[str] = set()
        # Blogger treats an unspecified status filter as live posts in some
        # contexts, so search both live and draft posts explicitly.
        for status in ("live", "draft"):
            for post in self.list_posts(max_results=limit, fetch_bodies=True, status=status):
                post_id = str(post.get("id") or "")
                if post_id and post_id in seen:
                    continue
                seen.add(post_id)
                if marker in str(post.get("content") or ""):
                    return post
        return None

    # ------------------------------------------------------------- discovery
    def fetch_discovery(self) -> dict[str, Any]:
        return fetch_discovery()

    def contract_snapshot(self) -> dict[str, Any]:
        return discovery_snapshot(self.fetch_discovery())

    def check_contract(self, path: Path | str) -> dict[str, Any]:
        path = Path(path)
        current = self.contract_snapshot()
        validate_contract_snapshot(current)
        if not path.exists():
            return {"status": "missing", "path": str(path), "current": current}
        previous = json.loads(path.read_text(encoding="utf-8"))
        status = "unchanged" if previous.get("sha256") == current["sha256"] else "changed"
        return {"status": status, "path": str(path), "previous": previous, "current": current}

    def refresh_contract(self, path: Path | str) -> dict[str, Any]:
        snapshot = self.contract_snapshot()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return snapshot

    # ------------------------------------------------------------ healthcheck
    def healthcheck(self, *, contract_path: Path | str | None = None) -> dict[str, Any]:
        blog = self.get_blog()
        posts = self.list_posts(max_results=1, fetch_bodies=False)
        discovery = self.contract_snapshot()
        contract: dict[str, Any] = {"status": "not_checked"}
        if contract_path:
            contract = self.check_contract(contract_path)
        return {
            "ok": True,
            "blog": {"id": blog.id, "name": blog.name, "url": blog.url},
            "posts_visible": len(posts),
            "discovery_revision": discovery["revision"],
            "methods": sorted(discovery["methods"]),
            "contract": contract,
            "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }


def fetch_discovery(*, timeout: int = 30) -> dict[str, Any]:
    request = Request(DISCOVERY_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise BloggerError(f"could not read the Blogger discovery document: {exc}") from exc
    if not isinstance(payload, dict) or "resources" not in payload:
        raise BloggerDriftError("discovery document has no resources")
    return payload


def discovery_snapshot(discovery: dict[str, Any]) -> dict[str, Any]:
    posts = discovery.get("resources", {}).get("posts", {})
    methods = posts.get("methods", {})
    if not isinstance(methods, dict):
        methods = {}
    signature: dict[str, list[str]] = {}
    for name, method in sorted(methods.items()):
        parameters = method.get("parameters", {}) if isinstance(method, dict) else {}
        signature[name] = sorted(parameters)
    canonical = json.dumps(
        {"revision": discovery.get("revision"), "methods": signature},
        sort_keys=True,
        ensure_ascii=False,
    )
    return {
        "schema": "astrion.blogger-contract/v1",
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "revision": str(discovery.get("revision") or ""),
        "title": str(discovery.get("title") or ""),
        "methods": signature,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def fetch_contract_snapshot(*, timeout: int = 30) -> dict[str, Any]:
    return discovery_snapshot(fetch_discovery(timeout=timeout))


def validate_contract_snapshot(snapshot: dict[str, Any]) -> None:
    """Fail loudly when the required Blogger surface changes or disappears."""

    methods = snapshot.get("methods")
    if not isinstance(methods, dict) or not methods:
        raise BloggerDriftError("contract snapshot has no methods")
    missing = [name for name in REQUIRED_POST_METHODS if name not in methods]
    if missing:
        raise BloggerDriftError(
            "discovery document is missing methods: " + ", ".join(missing)
        )
    insert_params = set(methods["insert"])
    for parameter in REQUIRED_INSERT_PARAMETERS:
        if parameter not in insert_params:
            raise BloggerDriftError(
                f"posts.insert no longer exposes the '{parameter}' parameter"
            )


def _read_error(exc: HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - diagnostics must never mask the HTTP error
        return ""


def _error_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_message(raw: str) -> str:
    payload = _error_payload(raw)
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or raw[:300])
    return raw[:300] or "no response body"


def _error_reason(raw: str) -> str:
    payload = _error_payload(raw)
    error = payload.get("error")
    if isinstance(error, dict):
        errors = error.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("reason") or "")
    return ""


def _sleep_for_retry(exc: HTTPError, attempt: int) -> None:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after and retry_after.isdigit():
        time.sleep(min(60, int(retry_after)))
    else:
        time.sleep(min(30, 2 ** attempt))
