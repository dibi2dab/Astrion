"""Blogger client that publishes through the web editor.

Google keeps the Blogger API OAuth app in "Testing" mode for this project, so
refresh tokens expire after seven days. Instead of maintaining a short-lived
credential, this client drives the real Blogger editor with Playwright and a
saved Google browser session, the same approach used by MVP-Blog.

Design contract:

* The browser session is stored as Base64 JSON in ``BLOGGER_STORAGE_STATE`` and
  materialized into ``.state/blogger_storage_state.json`` at runtime.
* All page automation lives in ``web/*.mjs``. This module only builds payloads,
  invokes Node, parses the ``__ASTRION_RESULT__`` sentinel, and maps failures to
  the existing typed exceptions.
* A draft save never clicks Publish. If the draft control is missing the Node
  script fails instead of making a post public.
* Selector repair is automatic: ``web/selectorHealth.mjs fix`` rewrites
  ``web/selectors.json`` and the daily workflow opens a pull request.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .blogger import BloggerAuthError, BloggerClient, BloggerDriftError, BloggerError
from .config import Settings

SENTINEL = "__ASTRION_RESULT__"
PAGE_TIMEOUT_MS = 45_000

SESSION_CODES = {"BLOGGER_SESSION_REQUIRED", "BLOGGER_SESSION_EXPIRED"}
DRIFT_CODES = {
    "BLOGGER_SELECTOR_MISSING",
    "BLOGGER_DRAFT_BUTTON_MISSING",
    "BLOGGER_DRAFT_BECAME_LIVE",
    "BLOGGER_BODY_NOT_SET",
    "BLOGGER_TITLE_NOT_SET",
    "BLOGGER_NAVIGATION_TIMEOUT",
    "BLOGGER_BAD_CONFIG",
}
RETRYABLE_CODES = {"BLOGGER_SAVE_UNCONFIRMED", "BLOGGER_NODE_ERROR"}

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def scrub(text: str) -> str:
    """Remove e-mail addresses from diagnostics before they reach a public log."""

    return _EMAIL_RE.sub("***@***", str(text or ""))


def parse_result(stdout: str) -> dict[str, Any]:
    """Return the last ``__ASTRION_RESULT__`` JSON object on stdout."""

    for line in reversed(str(stdout or "").splitlines()):
        stripped = line.strip()
        if not stripped.startswith(SENTINEL):
            continue
        raw = stripped[len(SENTINEL) :].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BloggerError(
                f"web publisher emitted an unreadable result: {exc}",
                reason="BLOGGER_BAD_RESULT",
            ) from exc
        if not isinstance(payload, dict):
            raise BloggerError(
                "web publisher emitted a non-object result",
                reason="BLOGGER_BAD_RESULT",
            )
        return payload
    return {}


def error_from_result(result: dict[str, Any], *, stderr: str = "") -> BloggerError:
    """Map a Node result/error code to the publisher's typed exceptions."""

    code = str(result.get("errorCode") or "").strip() or "BLOGGER_NODE_ERROR"
    message = scrub(
        str(result.get("error") or "").strip()
        or f"the Blogger web publisher failed with {code}"
    )
    if stderr:
        tail = scrub(stderr.strip().splitlines()[-1] if stderr.strip() else "")
        if tail and tail not in message:
            message = f"{message} ({tail})"

    if code in SESSION_CODES:
        return BloggerAuthError(message, reason=code)
    if code in DRIFT_CODES:
        return BloggerDriftError(message, reason=code)
    return BloggerError(message, reason=code, retryable=code in RETRYABLE_CODES)


class WebBloggerClient:
    """Publish to Blogger by driving the editor in a real browser."""

    def __init__(
        self,
        settings: Settings,
        *,
        web_dir: Path | str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings
        self.web = settings.blogger.web
        self.blog_id = (
            os.environ.get("BLOGGER_BLOG_ID") or settings.blogger.blog_id
        ).strip()
        self.blog_url = (
            os.environ.get("BLOGGER_BLOG_URL") or settings.blogger.blog_url
        ).strip()
        self.web_dir = Path(web_dir) if web_dir else settings.root / "web"
        self.storage_state = settings.web_storage_state_path
        self._env_overrides = dict(env or {})

    # -------------------------------------------------------------- environment
    def node_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self._env_overrides)
        env.setdefault("ASTRION_ROOT", str(self.settings.root))
        if self.blog_id:
            env["BLOGGER_BLOG_ID"] = self.blog_id
        if self.blog_url:
            env["BLOGGER_BLOG_URL"] = self.blog_url
        env["BLOGGER_STORAGE_STATE_PATH"] = str(self.storage_state)
        env["ASTRION_WEB_ARTIFACT_DIR"] = str(self.settings.web_artifact_dir)
        env["ASTRION_WEB_HEADLESS"] = "true" if self.web.headless else "false"
        env["ASTRION_WEB_SLOW_MO"] = str(self.web.slow_mo_ms)
        return env

    def materialize_session(self) -> Path:
        """Write the Base64 session secret to disk, or verify the saved session."""

        encoded = os.environ.get("BLOGGER_STORAGE_STATE", "").strip()
        if encoded:
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise BloggerAuthError(
                    "BLOGGER_STORAGE_STATE is not valid Base64. Re-run `npm run login` "
                    "and copy the value again.",
                    reason="BLOGGER_SESSION_REQUIRED",
                ) from exc
            try:
                state = json.loads(decoded.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BloggerAuthError(
                    "BLOGGER_STORAGE_STATE does not decode to Playwright storage JSON.",
                    reason="BLOGGER_SESSION_REQUIRED",
                ) from exc
            if not isinstance(state, dict) or not state.get("cookies"):
                raise BloggerAuthError(
                    "BLOGGER_STORAGE_STATE has no cookies; sign in again with `npm run login`.",
                    reason="BLOGGER_SESSION_REQUIRED",
                )
            self.storage_state.parent.mkdir(parents=True, exist_ok=True)
            self.storage_state.write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
            try:
                self.storage_state.chmod(0o600)
            except OSError:
                # Windows ignores POSIX modes; the file still stays out of git.
                pass
            return self.storage_state

        if not self.storage_state.exists():
            raise BloggerAuthError(
                f"Blogger browser session not found at {self.storage_state}. "
                "Run `npm run login` (or `python -m astrion login`) locally, then set "
                "the BLOGGER_STORAGE_STATE secret to the printed Base64 value.",
                reason="BLOGGER_SESSION_REQUIRED",
            )
        return self.storage_state

    # ------------------------------------------------------------------- runner
    def _run(self, script: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.blog_id:
            raise BloggerDriftError(
                "Blogger blog id is not configured. Set blogger.blog_id or BLOGGER_BLOG_ID.",
                reason="BLOGGER_BAD_CONFIG",
            )
        self.materialize_session()

        script_path = self.web_dir / script
        if not script_path.exists():
            raise BloggerError(
                f"web publisher script is missing: {script_path}",
                reason="BLOGGER_NODE_MISSING",
            )

        body = {
            "blogId": self.blog_id,
            "blogUrl": self.blog_url,
            "storageStatePath": str(self.storage_state),
            "artifactDir": str(self.settings.web_artifact_dir),
            "headless": self.web.headless,
            "slowMo": self.web.slow_mo_ms,
            "timeoutMs": PAGE_TIMEOUT_MS,
            **payload,
        }

        handle = tempfile.NamedTemporaryFile(
            "w",
            suffix=".json",
            prefix="astrion-web-",
            delete=False,
            encoding="utf-8",
        )
        try:
            with handle:
                json.dump(body, handle, ensure_ascii=False)
            command = [self.web.node_bin, str(script_path), handle.name]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(self.settings.root),
                    env=self.node_env(),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.web.timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise BloggerError(
                    f"Node.js was not found ({self.web.node_bin}). Install Node 20+ and "
                    "run `npm ci` before publishing.",
                    reason="BLOGGER_NODE_MISSING",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise BloggerError(
                    f"the Blogger web publisher timed out after {self.web.timeout_seconds}s",
                    reason="BLOGGER_NODE_TIMEOUT",
                    retryable=True,
                ) from exc
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

        result = parse_result(completed.stdout)
        if not result:
            detail = scrub((completed.stderr or completed.stdout or "").strip()[-400:])
            raise BloggerError(
                "the Blogger web publisher produced no result"
                + (f": {detail}" if detail else ""),
                reason="BLOGGER_NODE_ERROR",
                retryable=True,
            )
        if not result.get("ok"):
            raise error_from_result(result, stderr=completed.stderr)
        if completed.returncode not in (0, None):
            raise error_from_result(result, stderr=completed.stderr)
        return result

    # ---------------------------------------------------------------- publishing
    def insert_post(
        self,
        *,
        title: str,
        content: str,
        labels: list[str],
        is_draft: bool,
    ) -> dict[str, Any]:
        result = self._run(
            "publishBlogger.mjs",
            {
                "title": title,
                "contentHtml": content,
                "labels": labels,
                "mode": "draft" if is_draft else "publish",
            },
        )
        if is_draft and str(result.get("status") or "").upper() != "DRAFT":
            # Defense in depth: even if the browser engine mislabels a live post
            # as a draft save, never record it as a draft. This is the exact
            # failure that made four one-off drafts public.
            raise BloggerDriftError(
                "the web publisher was asked to save a draft but reported a live post. "
                "Refusing to record a public post as a draft. Revert it in the Blogger editor.",
                reason="BLOGGER_DRAFT_BECAME_LIVE",
            )
        post_id = str(result.get("postId") or "")
        url = str(result.get("url") or "") or str(result.get("editUrl") or "")
        if not post_id:
            raise BloggerDriftError(
                "the web publisher saved the post but could not read its Blogger post id. "
                "The next run reconciles by title before creating another post.",
                reason="BLOGGER_POST_ID_MISSING",
            )
        status = "draft" if str(result.get("status") or "").upper() == "DRAFT" else "live"
        return {
            "id": post_id,
            "url": url,
            "status": status,
            "published": str(result.get("publishedAt") or "") if status == "live" else "",
            "title": str(result.get("title") or title),
            "labels": list(labels),
            "warnings": list(result.get("warnings") or []),
            "saved_by": str(result.get("savedBy") or ""),
        }

    def find_recent_by_marker(
        self,
        topic_id: str,
        *,
        title: str = "",
        limit: int = 50,
    ) -> dict[str, Any] | None:
        """Find an earlier partial publish by title.

        The web posts list cannot be searched by HTML marker, so this relies on
        the exact post title. The caller passes the payload title; an empty title
        disables the check rather than returning a false match.
        """

        del topic_id, limit  # kept for protocol compatibility with BloggerClient
        if not str(title or "").strip():
            return None
        result = self._run("findPost.mjs", {"title": str(title).strip()})
        if not result.get("found"):
            return None
        post_id = str(result.get("postId") or "")
        if not post_id:
            return None
        status = str(result.get("status") or "draft")
        return {
            "id": post_id,
            "url": str(result.get("url") or "") or str(result.get("editUrl") or ""),
            "status": status,
            "published": "",
            "title": str(result.get("title") or title),
        }

    # -------------------------------------------------------------- diagnostics
    def session_check(self) -> dict[str, Any]:
        result = self._run("sessionCheck.mjs", {})
        return {
            **result,
            "backend": "web",
            "blog_id": self.blog_id,
            "blog_url": self.blog_url,
        }

    def healthcheck(self, *, contract_path: Path | str | None = None) -> dict[str, Any]:
        del contract_path  # the web backend does not use the API discovery contract
        try:
            report = self.session_check()
        except BloggerError as exc:
            return {
                "ok": False,
                "backend": "web",
                "contract": {"status": "not_applicable"},
                "error": {
                    **exc.to_dict(),
                    "code": exc.reason,
                },
            }
        report.setdefault("contract", {"status": "not_applicable"})
        return report


def make_client(
    settings: Settings,
    *,
    max_retries: int = 4,
) -> WebBloggerClient | BloggerClient:
    """Build the configured Blogger backend.

    The default is ``web``. ``api`` remains available as an explicit fallback
    for as long as its OAuth refresh token is valid.
    """

    if settings.blogger.backend == "api":
        return BloggerClient.from_env(
            timeout=settings.blogger.timeout_seconds,
            max_retries=max_retries,
        )
    return WebBloggerClient(settings)
