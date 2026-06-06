#!/usr/bin/env python
"""GuardianCI deployment helper for GHCR -> Railway releases."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

METRICS_BRANCH = "guardianci-metrics"
LAST_DEPLOY_FILE = "last-deploy.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a GuardianCI-approved image.")
    parser.add_argument("--coverage", default="coverage.xml")
    parser.add_argument("--review-result", default="guardianci-review-result.json")
    parser.add_argument("--metrics-branch", default=METRICS_BRANCH)
    parser.add_argument("--image", default=os.getenv("DEPLOY_IMAGE", ""))
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", "unknown/repo"))
    parser.add_argument("--sha", default=os.getenv("GITHUB_SHA", ""))
    parser.add_argument("--health-url", default=os.getenv("PRODUCTION_HEALTHCHECK_URL", ""))
    parser.add_argument("--health-timeout", type=int, default=300)
    parser.add_argument("--health-interval", type=int, default=10)
    args = parser.parse_args()

    webhook_url = os.getenv("RAILWAY_WEBHOOK_URL", "")
    rollback_webhook_url = os.getenv("RAILWAY_ROLLBACK_WEBHOOK_URL", "")
    slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        raise RuntimeError("RAILWAY_WEBHOOK_URL is required for Phase 7 deploys.")
    if not args.image:
        raise RuntimeError("DEPLOY_IMAGE or --image is required for Phase 7 deploys.")

    previous = read_last_deploy(args.metrics_branch)
    coverage = parse_coverage(Path(args.coverage))
    review = read_json_file(Path(args.review_result))
    deployment = deployment_record(args.repo, args.sha, args.image, coverage, review)

    # Rollback only fires when the deploy trigger or health check fails.
    # Post-deploy side effects (metrics publish, Slack) never trigger rollback.
    try:
        trigger_deploy(webhook_url, deployment)
        if args.health_url:
            wait_for_health(
                args.health_url, timeout_seconds=args.health_timeout, interval=args.health_interval
            )
    except Exception as exc:
        rollback_message = attempt_rollback(rollback_webhook_url, previous, deployment)
        notify_slack(
            slack_webhook_url,
            slack_message(deployment, success=False, error=str(exc), rollback=rollback_message),
        )
        print(f"GuardianCI deploy failed: {exc}")
        if rollback_message:
            print(rollback_message)
        return 1

    publish_last_deploy(args.metrics_branch, deployment)
    notify_slack(slack_webhook_url, slack_message(deployment, success=True))
    print(f"GuardianCI deployed {args.image}.")
    return 0


def deployment_record(
    repo: str,
    sha: str,
    image: str,
    coverage: float | None,
    review: dict[str, Any],
) -> dict[str, Any]:
    short_sha = sha[:7] if sha else "unknown"
    return {
        "schema_version": 1,
        "repo": repo,
        "sha": sha,
        "short_sha": short_sha,
        "image": image,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "coverage_percent": coverage,
        "security": {
            "total_critical": int(review.get("total_critical", 0)),
            "total_warn": int(review.get("total_warn", 0)),
            "score": int(review.get("score", 100)),
            "status": str(review.get("status", "not_available")),
        },
    }


def trigger_deploy(webhook_url: str, deployment: dict[str, Any]) -> None:
    post_json(
        webhook_url,
        {
            "image": deployment["image"],
            "sha": deployment["sha"],
            "source": "GuardianCI",
        },
    )


def attempt_rollback(
    rollback_webhook_url: str,
    previous: dict[str, Any],
    failed_deployment: dict[str, Any],
) -> str:
    previous_image = str(previous.get("image") or "")
    if not rollback_webhook_url:
        return "GuardianCI rollback skipped: RAILWAY_ROLLBACK_WEBHOOK_URL is not configured."
    if not previous_image:
        return "GuardianCI rollback skipped: no previous image tag was recorded."

    try:
        post_json(
            rollback_webhook_url,
            {
                "image": previous_image,
                "failed_image": failed_deployment["image"],
                "source": "GuardianCI rollback",
            },
        )
    except Exception as exc:
        return f"GuardianCI rollback attempt failed: {exc}"
    return f"GuardianCI rollback triggered for previous image {previous_image}."


def wait_for_health(url: str, *, timeout_seconds: int, interval: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if 200 <= response.status < 300:
                    return
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(interval)
    raise RuntimeError(f"Production health check failed for {url}: {last_error}")


def parse_coverage(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except ET.ParseError:
        print("GuardianCI deploy: coverage.xml could not be parsed; coverage will be omitted.")
        return None
    line_rate = root.attrib.get("line-rate")
    if line_rate is None:
        return None
    try:
        return round(float(line_rate) * 100, 2)
    except (TypeError, ValueError):
        print("GuardianCI deploy: invalid coverage line-rate; coverage will be omitted.")
        return None


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_last_deploy(branch: str) -> dict[str, Any]:
    subprocess.run(
        ["git", "fetch", "--no-tags", "origin", f"{branch}:refs/remotes/origin/{branch}"],
        check=False,
        text=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "show", f"origin/{branch}:{LAST_DEPLOY_FILE}"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def publish_last_deploy(branch: str, deployment: dict[str, Any]) -> None:
    ensure_git_identity()
    ensure_metrics_branch(branch)
    Path(LAST_DEPLOY_FILE).write_text(
        json.dumps(deployment, indent=2, sort_keys=True), encoding="utf-8"
    )
    run_git(["add", LAST_DEPLOY_FILE])
    if not has_git_changes([LAST_DEPLOY_FILE]):
        print("GuardianCI deploy metadata already up to date.")
        return
    run_git(["commit", "-m", f"GuardianCI deploy: {deployment['short_sha']}"])
    run_git(["push", "origin", f"HEAD:{branch}"])


def slack_message(
    deployment: dict[str, Any],
    *,
    success: bool,
    error: str = "",
    rollback: str = "",
) -> str:
    icon = ":white_check_mark:" if success else ":rotating_light:"
    action = "Deployed" if success else "Deploy failed"
    coverage = deployment.get("coverage_percent")
    coverage_text = f"{coverage:.2f}%" if isinstance(coverage, float) else "not available"
    security = deployment.get("security", {})
    lines = [
        f"{icon} {action} `{deployment['repo']}` `{deployment['short_sha']}`",
        f"Image: `{deployment['image']}`",
        f"Coverage: {coverage_text}",
        (
            "Security: "
            f"{security.get('total_critical', 0)} critical | "
            f"{security.get('total_warn', 0)} warnings | "
            f"score {security.get('score', 100)}/100"
        ),
    ]
    if error:
        lines.append(f"Error: `{error}`")
    if rollback:
        lines.append(rollback)
    return "\n".join(lines)


def notify_slack(webhook_url: str, message: str) -> None:
    if not webhook_url:
        print("GuardianCI Slack notification skipped: SLACK_WEBHOOK_URL is not configured.")
        return
    try:
        post_json(webhook_url, {"text": message})
    except Exception as exc:
        print(f"GuardianCI Slack notification failed without blocking deploy: {exc}")


def post_json(url: str, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                # Do not include the URL — it may contain webhook secrets.
                raise RuntimeError(f"POST returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"POST returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"POST failed: {exc.reason}") from exc


def ensure_git_identity() -> None:
    run_git(["config", "user.name", "GuardianCI Bot"])
    run_git(["config", "user.email", "guardianci-bot@users.noreply.github.com"])


def ensure_metrics_branch(branch: str) -> None:
    exists = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if exists:
        run_git(["fetch", "origin", f"{branch}:{branch}"])
        run_git(["checkout", branch])
        return

    run_git(["checkout", "--orphan", branch])
    run_git(["rm", "-rf", "."])


def has_git_changes(paths: list[str]) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        text=True,
        capture_output=True,
        check=True,
    )
    return bool(result.stdout.strip())


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
