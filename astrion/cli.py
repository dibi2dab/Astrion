"""Astrion command line interface."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .blogger import (
    BloggerAuthError,
    BloggerDriftError,
    BloggerError,
    fetch_contract_snapshot,
    validate_contract_snapshot,
)
from .builder import build
from .config import Settings, load_settings
from .duplicate import record_from_topic, validate_pairs
from .loader import LoadedTopic, TopicLoadError, load_topics
from .planner import build_plan
from .publisher import publish_due
from .queue import list_queue, queue_summary
from .render import render_post
from .state import StateStore
from .text import iso_now
from .validate import counts, validate_topics
from .web_publisher import WebBloggerClient, make_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="astrion",
        description="Deterministic editorial pipeline for Astrion.",
    )
    parser.add_argument("--root", help="project root (default: detect from cwd)")
    parser.add_argument("--config", help="path to astrion.yaml")
    parser.add_argument("--version", action="version", version=f"astrion {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_cmd = subparsers.add_parser("validate", help="validate every topic module")
    validate_cmd.add_argument("--strict", action="store_true", help="treat warnings as errors")
    validate_cmd.add_argument("--json", action="store_true", help="machine-readable output")

    list_cmd = subparsers.add_parser("list", help="list topic modules")
    list_cmd.add_argument("--category", help="filter by main category")
    list_cmd.add_argument("--status", help="filter by module status")
    list_cmd.add_argument("--json", action="store_true")

    dup_cmd = subparsers.add_parser("check-duplicates", help="pairwise duplicate report")
    dup_cmd.add_argument("--json", action="store_true")

    render_cmd = subparsers.add_parser("render", help="render one topic to an HTML preview")
    render_cmd.add_argument("topic_id")
    render_cmd.add_argument("--out", help="output path")

    plan_cmd = subparsers.add_parser("plan", help="show what would be built (no writes)")
    _add_plan_args(plan_cmd)
    plan_cmd.add_argument("--json", action="store_true")

    build_cmd = subparsers.add_parser("build", help="render and queue due topics")
    _add_plan_args(build_cmd)
    build_cmd.add_argument("--dry-run", action="store_true")

    publish_cmd = subparsers.add_parser("publish", help="publish queued payloads via the configured backend")
    publish_cmd.add_argument("--due", action="store_true", help="publish every due payload")
    publish_cmd.add_argument("--queue-id", default="", help="publish one specific queue item")
    publish_cmd.add_argument("--max", type=int, default=None, help="maximum posts in this run")
    publish_cmd.add_argument("--dry-run", action="store_true")

    health_cmd = subparsers.add_parser("healthcheck", help="check the configured Blogger backend")
    health_cmd.add_argument("--strict-contract", action="store_true")
    health_cmd.add_argument("--json", action="store_true")

    session_cmd = subparsers.add_parser(
        "session", help="verify the saved Google browser session (web backend)"
    )
    session_cmd.add_argument("--json", action="store_true")

    login_cmd = subparsers.add_parser(
        "login", help="save a Blogger browser session with `npm run login`"
    )
    login_cmd.add_argument("--timeout", type=int, default=600, help="seconds to wait for sign-in")

    contract_cmd = subparsers.add_parser("contract", help="inspect or refresh the Blogger discovery contract")
    contract_cmd.add_argument("--refresh", action="store_true", help="write the current snapshot")
    contract_cmd.add_argument("--check", action="store_true", help="compare with the committed snapshot")
    contract_cmd.add_argument("--json", action="store_true")

    auth_cmd = subparsers.add_parser("auth", help="obtain a Blogger OAuth refresh token locally")
    auth_cmd.add_argument("--client-id", default=os.environ.get("BLOGGER_CLIENT_ID", ""))
    auth_cmd.add_argument("--client-secret", default=os.environ.get("BLOGGER_CLIENT_SECRET", ""))
    auth_cmd.add_argument("--port", type=int, default=8765)
    auth_cmd.add_argument("--no-browser", action="store_true")

    status_cmd = subparsers.add_parser("status", help="show queue and publication state")
    status_cmd.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        settings = load_settings(root=args.root, config_path=args.config)
    except Exception as exc:  # noqa: BLE001 - configuration errors are terminal
        print(f"FAIL configuration: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "validate":
            return _validate(settings, strict=args.strict, as_json=args.json)
        if args.command == "list":
            return _list(settings, category=args.category, status=args.status, as_json=args.json)
        if args.command == "check-duplicates":
            return _check_duplicates(settings, as_json=args.json)
        if args.command == "render":
            return _render(settings, args.topic_id, args.out)
        if args.command == "plan":
            return _plan(settings, args)
        if args.command == "build":
            return _build(settings, args)
        if args.command == "publish":
            return _publish(settings, args)
        if args.command == "healthcheck":
            return _healthcheck(settings, strict=args.strict_contract, as_json=args.json)
        if args.command == "session":
            return _session(settings, as_json=args.json)
        if args.command == "login":
            return _login(settings, timeout=args.timeout)
        if args.command == "contract":
            return _contract(settings, refresh=args.refresh, check=args.check, as_json=args.json)
        if args.command == "auth":
            return _auth(args)
        if args.command == "status":
            return _status(settings, as_json=args.json)
    except (TopicLoadError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    return 2


def _add_plan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", help="publish date (YYYY-MM-DD, America/New_York)")
    parser.add_argument("--limit", type=int, default=None, help="maximum items to plan")
    parser.add_argument("--topic", default=None, help="build one specific topic id")


def _load(settings: Settings) -> tuple[list[LoadedTopic], StateStore]:
    loaded = load_topics(settings.content_dir)
    state = StateStore(settings.state_path)
    return loaded, state


class _DryRunClient:
    """A client that refuses to publish; dry runs never call it."""

    def insert_post(self, **_kwargs):
        raise BloggerError("a dry run must not publish", reason="DRY_RUN")

    def find_recent_by_marker(self, *_args, **_kwargs):
        return None


def _validate(settings: Settings, *, strict: bool, as_json: bool) -> int:
    loaded, state = _load(settings)
    issues = validate_topics(loaded, settings, state.published)
    errors, warnings = counts(issues)
    if as_json:
        print(
            json.dumps(
                {
                    "topics": len(loaded),
                    "errors": errors,
                    "warnings": warnings,
                    "issues": [issue.__dict__ for issue in issues],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for issue in issues:
            print(issue.format())
        print(f"\n{len(loaded)} topic(s): {errors} error(s), {warnings} warning(s)")
        if strict and warnings:
            print("Strict mode: warnings are treated as errors.")
    failed = errors > 0 or (strict and warnings > 0)
    return 1 if failed else 0


def _list(settings: Settings, *, category: str | None, status: str | None, as_json: bool) -> int:
    loaded, _ = _load(settings)
    items = [
        item
        for item in loaded
        if (not category or item.topic.main_category.value == category)
        and (not status or item.topic.status == status)
    ]
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "id": item.topic.id,
                        "title": item.topic.title,
                        "status": item.topic.status,
                        "category": item.topic.main_category.value,
                        "content_type": item.topic.content_type.value,
                        "review": item.topic.provenance.review_status,
                        "path": str(item.path),
                    }
                    for item in items
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not items:
        print("No topics matched.")
        return 0
    print(f"{'ID':42} {'STATUS':8} {'REVIEW':9} {'CATEGORY':34} TITLE")
    for item in sorted(items, key=lambda entry: entry.topic.id):
        topic = item.topic
        print(
            f"{topic.id:42} {topic.status:8} {topic.provenance.review_status:9} "
            f"{topic.main_category.value:34} {topic.title}"
        )
    return 0


def _check_duplicates(settings: Settings, *, as_json: bool) -> int:
    loaded, _ = _load(settings)
    topics = [item.topic for item in loaded]
    pairs = validate_pairs(topics, settings.duplicates)
    if as_json:
        print(json.dumps(pairs, ensure_ascii=False, indent=2))
    else:
        if not pairs:
            print("No duplicate candidates found.")
        for pair in pairs:
            marker = "RESOLVED" if pair["overridden"] else "OPEN"
            print(
                f"[{marker}] {pair['left']} <-> {pair['right']} "
                f"score={pair['score']:.2f} ({', '.join(pair['reasons'])})"
            )
    open_pairs = [pair for pair in pairs if not pair["overridden"]]
    return 1 if open_pairs else 0


def _render(settings: Settings, topic_id: str, out: str | None) -> int:
    loaded, _ = _load(settings)
    match = next((item.topic for item in loaded if item.topic.id == topic_id), None)
    if match is None:
        print(f"FAIL topic not found: {topic_id}", file=sys.stderr)
        return 1
    rendered = render_post(match)
    target = Path(out) if out else settings.preview_dir / f"{match.slug}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered.html, encoding="utf-8")
    print(f"Rendered {match.id} -> {target}")
    print(f"{rendered.word_count} words, {rendered.reading_minutes} min read, {rendered.size_bytes} bytes")
    return 0


def _plan(settings: Settings, args) -> int:
    loaded, state = _load(settings)
    result = build_plan(
        loaded,
        settings,
        state,
        publish_date=args.date,
        limit=args.limit,
        topic_id=args.topic,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_plan(result)
    return 1 if result.error else 0


def _print_plan(result) -> None:
    print(
        f"{result.publish_date} ({result.existing} already filled, "
        f"{result.remaining} slot(s) available)"
    )
    if result.error:
        print(f"FAIL {result.error}")
    if result.reason:
        print(result.reason)
    for item in result.items:
        print(
            f"  [s{item.slot + 1}] {item.publish_after}  {item.topic.main_category.value:32} "
            f"{item.quality.score:5.1f}  {item.topic.title}"
        )
    for skipped in result.skipped:
        detail = f" ({skipped.decision} {skipped.score:.1f})" if skipped.decision else ""
        print(f"  skip {skipped.topic_id}: {skipped.reason}{detail}")


def _build(settings: Settings, args) -> int:
    loaded, state = _load(settings)
    issues = validate_topics(loaded, settings, state.published)
    errors, warnings = counts(issues)
    if errors:
        for issue in issues:
            if issue.level == "error":
                print(issue.format())
        print(f"FAIL {errors} validation error(s); build stopped.", file=sys.stderr)
        return 1
    if warnings:
        print(f"Warning: {warnings} validation warning(s); continuing.")

    result = build(
        loaded,
        settings,
        state,
        publish_date=args.date,
        limit=args.limit,
        topic_id=args.topic,
        dry_run=args.dry_run,
    )
    if result.dry_run:
        print("Dry run: no files or state were written.")
        _print_plan(result.plan)
        return 1 if result.plan.error else 0

    _print_plan(result.plan)
    if result.plan.error:
        print(f"FAIL {result.plan.error}", file=sys.stderr)
        return 1
    if result.written:
        print(f"\nQueued {result.count} payload(s):")
        for queue_id in result.written:
            print(f"  {settings.queue_dir / (queue_id + '.json')}")
        if result.report_path:
            print(f"Report: {result.report_path}")
    else:
        print("\nNothing was queued.")
    return 0


def _publish(settings: Settings, args) -> int:
    _loaded, state = _load(settings)
    if args.dry_run:
        # A dry run validates and reserves nothing; it never touches the network,
        # so credentials and a browser session are not required.
        client: object = _DryRunClient()
    else:
        try:
            client = make_client(settings)
            # Fail early when the web session is missing, even if nothing is due.
            if isinstance(client, WebBloggerClient):
                client.materialize_session()
        except BloggerAuthError as exc:
            print(f"FAIL {exc}", file=sys.stderr)
            return 2

    result = publish_due(
        settings,
        state,
        client,
        max_posts=args.max,
        dry_run=args.dry_run,
        only_queue_id=args.queue_id,
    )
    for outcome in result.reconciled:
        print(f"reconcile {outcome.queue_id}: {outcome.status} {outcome.error}".rstrip())
    for outcome in result.outcomes:
        line = f"{outcome.status:14} {outcome.queue_id}"
        if outcome.post_id:
            line += f"  id={outcome.post_id}"
        if outcome.url:
            line += f"  {outcome.url}"
        if outcome.error:
            line += f"  ({outcome.error})"
        print(line)

    settings.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = settings.report_dir / f"publish-{iso_now().replace(':', '')}.json"
    report_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Report: {report_path}")

    if result.stopped:
        print(f"FAIL {result.error}", file=sys.stderr)
        return 1
    if any(outcome.status == "failed" for outcome in result.outcomes):
        return 1
    return 0


def _healthcheck(settings: Settings, *, strict: bool, as_json: bool) -> int:
    try:
        client = make_client(settings, max_retries=2)
    except BloggerAuthError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2

    try:
        report = client.healthcheck(
            contract_path=settings.root / "config" / "blogger_contract.json"
        )
    except BloggerError as exc:
        report = {"ok": False, "error": exc.to_dict()}
        _write_report(settings, "healthcheck", report)
        print(f"FAIL {exc}", file=sys.stderr)
        return 1

    backend = settings.blogger.backend
    report["backend"] = backend
    contract_status = (report.get("contract") or {}).get("status")
    report["contract_status"] = contract_status
    _write_report(settings, "healthcheck", report)

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif backend == "web":
        if report.get("ok"):
            print(
                f"OK backend web; session {report.get('session', 'ok')}; "
                f"blog {report.get('blogId') or settings.blogger.blog_id}"
            )
            print(f"editor: {report.get('url', settings.blogger.blog_url)}")
            broken = report.get("broken") or []
            if broken:
                print("required controls missing: " + ", ".join(broken))
            optional = report.get("optionalMissing") or []
            if optional:
                print("optional controls missing: " + ", ".join(optional))
            print("Run `npm run selectors:check` for a full selector report.")
        else:
            error = report.get("error") or {}
            print(f"FAIL {error.get('message') or 'web session check failed'}", file=sys.stderr)
    else:
        blog = report["blog"]
        print(f"OK blog {blog['id']} {blog['name']} ({blog['url']})")
        print(f"OK Blogger API reachable; discovery revision {report['discovery_revision']}")
        print(f"contract: {contract_status}")
        if contract_status in {"changed", "missing"}:
            print("Warning: the API contract differs from config/blogger_contract.json.")

    if not report.get("ok", True):
        return 2 if backend == "web" else 1
    if backend == "api" and contract_status in {"changed", "missing"} and strict:
        return 1
    return 0


def _session(settings: Settings, *, as_json: bool) -> int:
    """Exit codes: 0 = usable, 2 = editor drift, 3 = session missing/expired."""

    if settings.blogger.backend != "web":
        print(
            "FAIL the session command only applies to the web backend "
            "(blogger.backend: web).",
            file=sys.stderr,
        )
        return 2

    client = WebBloggerClient(settings)
    try:
        report = client.session_check()
    except BloggerAuthError as exc:
        payload = {"ok": False, "backend": "web", "error": str(exc), "code": exc.reason}
        _write_report(settings, "session", payload)
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"FAIL {exc}", file=sys.stderr)
            print("Run `python -m astrion login` (or `npm run login`) and update ")
            print("the BLOGGER_STORAGE_STATE secret.")
        return 3
    except BloggerDriftError as exc:
        payload = {"ok": False, "backend": "web", "error": str(exc), "code": exc.reason}
        _write_report(settings, "session", payload)
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"FAIL {exc}", file=sys.stderr)
            print("The editor UI may have changed. Run `npm run selectors:fix`.")
        return 2
    except BloggerError as exc:
        payload = {"ok": False, "backend": "web", "error": str(exc), "code": exc.reason}
        _write_report(settings, "session", payload)
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"FAIL {exc}", file=sys.stderr)
        return 1

    _write_report(settings, "session", report)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"OK session; blog {report.get('blogId') or settings.blogger.blog_id}")
        print(f"editor: {report.get('url', '')}")
        broken = report.get("broken") or []
        if broken:
            print("Missing required controls: " + ", ".join(broken))
        else:
            print("All required editor controls were found.")
    return 0 if report.get("ok") else 2


def _login(settings: Settings, *, timeout: int) -> int:
    """Save a browser session locally by delegating to `npm run login`."""

    npm = shutil.which("npm")
    if not npm:
        print(
            "FAIL npm was not found. Install Node.js 20+ and run `npm install` first.",
            file=sys.stderr,
        )
        return 2
    if not (settings.root / "node_modules" / "playwright").exists():
        print("Installing Node dependencies (npm install)...")
        install = subprocess.run(
            [npm, "install", "--no-audit", "--no-fund"],
            cwd=str(settings.root),
            check=False,
        )
        if install.returncode != 0:
            print("FAIL npm install failed.", file=sys.stderr)
            return 2

    print("Ensuring the Chromium browser is installed...")
    browser_install = subprocess.run(
        [npm, "exec", "--", "playwright", "install", "chromium"],
        cwd=str(settings.root),
        check=False,
    )
    if browser_install.returncode != 0:
        print(
            "FAIL Playwright could not install Chromium. Run "
            "`npx playwright install chromium` manually.",
            file=sys.stderr,
        )
        return 2

    print("Opening a browser window. Sign in to Google, then leave it open.")
    completed = subprocess.run(
        [npm, "run", "login", "--", "--timeout", str(timeout)],
        cwd=str(settings.root),
        check=False,
    )
    if completed.returncode != 0:
        print("FAIL the login helper did not save a session.", file=sys.stderr)
        return completed.returncode or 1
    print(
        "\nCopy the printed Base64 value into the BLOGGER_STORAGE_STATE secret, "
        "then run `python -m astrion session`."
    )
    return 0


def _contract(settings: Settings, *, refresh: bool, check: bool, as_json: bool) -> int:
    path = settings.root / "config" / "blogger_contract.json"
    try:
        current = fetch_contract_snapshot(timeout=settings.blogger.timeout_seconds)
        validate_contract_snapshot(current)
    except BloggerError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    if refresh:
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous.get("sha256") == current["sha256"]:
                print(
                    f"Contract unchanged ({current['revision']}, "
                    f"{current['sha256'][:12]}); snapshot left as-is."
                )
                return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {path} (revision {current['revision']}, {current['sha256'][:12]})")
        return 0

    if not path.exists():
        status = "missing"
        previous = None
    else:
        previous = json.loads(path.read_text(encoding="utf-8"))
        status = "unchanged" if previous.get("sha256") == current["sha256"] else "changed"
    result = {"status": status, "path": str(path), "previous": previous, "current": current}
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"contract: {status}")
        if previous:
            print(f"committed revision: {previous.get('revision')} sha {str(previous.get('sha256'))[:12]}")
        print(f"current revision:   {current['revision']} sha {current['sha256'][:12]}")
        if status == "changed":
            print("Run `astrion contract --refresh` and commit the new snapshot.")
    if check and status == "changed":
        return 1
    return 0


def _auth(args) -> int:
    client_id = args.client_id
    client_secret = args.client_secret
    if not client_id or not client_secret:
        print(
            "FAIL set BLOGGER_CLIENT_ID and BLOGGER_CLIENT_SECRET, or pass "
            "--client-id/--client-secret.",
            file=sys.stderr,
        )
        return 2
    from .auth import authorize

    payload = authorize(
        client_id,
        client_secret,
        port=args.port,
        open_browser=not args.no_browser,
    )
    print("\nAdd these as GitHub Actions secrets:")
    print(f"BLOGGER_REFRESH_TOKEN={payload['refresh_token']}")
    print("BLOGGER_CLIENT_ID and BLOGGER_CLIENT_SECRET (the same values).")
    print("Also set BLOGGER_BLOG_ID.")
    return 0


def _status(settings: Settings, *, as_json: bool) -> int:
    state = StateStore(settings.state_path)
    queue = queue_summary(settings)
    summary = state.summary()
    payload = {"state": summary, "queue": queue}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"Published: {summary['published']}  Pending: {summary['pending']}  Failed: {summary['failed']}")
    print(f"Queue files: {queue['queued']}  invalid: {queue['invalid']}")
    if summary["pending_items"]:
        print("\nPending:")
        for item in summary["pending_items"]:
            print(
                f"  {item.get('publish_after')}  s{int(item.get('slot') or 0) + 1}  "
                f"{item.get('topic_id')}  [{item.get('status')}]"
            )
    if summary["recent_published"]:
        print("\nRecent:")
        for item in summary["recent_published"]:
            print(
                f"  {item.get('publish_date')}  {item.get('topic_id')}  "
                f"{item.get('url') or item.get('blogger_post_id') or ''}"
            )
    if summary["categories"]:
        print("\nCategories:")
        for category, count in sorted(summary["categories"].items()):
            print(f"  {category}: {count}")
    return 0


def _write_report(settings: Settings, kind: str, report: dict) -> None:
    settings.report_dir.mkdir(parents=True, exist_ok=True)
    path = settings.report_dir / f"{kind}-{iso_now().replace(':', '')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
