#!/usr/bin/env python
"""Publish GuardianCI review metrics: git-branch storage + optional webhook push."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

METRICS_BRANCH = "guardianci-metrics"
REVIEWS_DIR = "reviews"
SUMMARY_FILE = "summary.json"
BADGE_FILE = "SECURITY_BADGE.md"
DASHBOARD_FILE = "index.html"

# Webhook env vars — set these to push metrics to any external system.
WEBHOOK_URL_ENV = "GUARDIANCI_METRICS_WEBHOOK_URL"
WEBHOOK_SECRET_ENV = "GUARDIANCI_METRICS_WEBHOOK_SECRET"


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish GuardianCI metrics.")
    parser.add_argument("--result", default="guardianci-review-result.json")
    parser.add_argument("--branch", default=METRICS_BRANCH)
    parser.add_argument(
        "--webhook-url",
        default=os.getenv(WEBHOOK_URL_ENV, ""),
        help="POST the review result + summary to this URL after every review.",
    )
    parser.add_argument(
        "--webhook-secret",
        default=os.getenv(WEBHOOK_SECRET_ENV, ""),
        help="If set, sign webhook payloads with HMAC-SHA256.",
    )
    args = parser.parse_args()

    result_path = Path(args.result)
    if not result_path.exists():
        print(f"GuardianCI metrics skipped: {result_path} does not exist.")
        return 0

    result = json.loads(result_path.read_text(encoding="utf-8"))

    # ── Git-branch storage (default, backward-compatible) ──────────────────
    ensure_git_identity()
    ensure_metrics_branch(args.branch)
    review_path = write_review_record(result)
    reviews = load_reviews()
    summary = build_summary(reviews)
    Path(SUMMARY_FILE).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    Path(BADGE_FILE).write_text(render_badge(summary), encoding="utf-8")
    Path(DASHBOARD_FILE).write_text(render_dashboard(summary), encoding="utf-8")

    run_git(["add", REVIEWS_DIR, SUMMARY_FILE, BADGE_FILE, DASHBOARD_FILE])
    if not has_git_changes():
        print("GuardianCI metrics branch already up to date.")
    else:
        run_git(["commit", "-m", f"GuardianCI metrics: {review_path.name}"])
        run_git(["push", "origin", f"HEAD:{args.branch}"])
        print(f"GuardianCI metrics published to {args.branch}.")

    # ── Webhook push (opt-in, non-fatal) ───────────────────────────────────
    if args.webhook_url:
        push_webhook(
            result,
            summary,
            url=args.webhook_url,
            secret=args.webhook_secret or None,
        )

    return 0


def push_webhook(
    result: dict[str, Any],
    summary: dict[str, Any],
    *,
    url: str,
    secret: str | None = None,
    timeout: int = 15,
    retries: int = 3,
) -> None:
    """POST the review result + rolling summary to a webhook endpoint.

    The payload shape is stable across GuardianCI versions:
        {
            "event":          "guardianci.review.completed",
            "schema_version": 1,
            "review":         { ...review result... },
            "summary":        { ...rolling 30-day summary... }
        }

    If *secret* is supplied the request includes an
    ``X-GuardianCI-Signature-256: sha256=<hex>`` header so receivers can
    verify authenticity with a constant-time HMAC comparison.

    Webhook failures are logged but never fail the CI job.
    """
    payload: dict[str, Any] = {
        "event": "guardianci.review.completed",
        "schema_version": 1,
        "review": result,
        "summary": summary,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "GuardianCI/1.0",
    }
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-GuardianCI-Signature-256"] = f"sha256={sig}"

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                print(f"GuardianCI metrics webhook delivered: HTTP {resp.status} → {url}")
                return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                print(
                    f"GuardianCI metrics webhook returned HTTP {exc.code} — "
                    "not retrying (client error)."
                )
                return
            print(
                f"GuardianCI metrics webhook attempt {attempt + 1}/{retries}: "
                f"HTTP {exc.code} (server error)"
            )
        except Exception as exc:
            print(f"GuardianCI metrics webhook attempt {attempt + 1}/{retries}: {exc}")

        if attempt < retries - 1:
            time.sleep(2**attempt)

    print(
        f"GuardianCI metrics webhook failed after {retries} attempt(s) — "
        "continuing without delivery."
    )


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


def write_review_record(result: dict[str, Any]) -> Path:
    Path(REVIEWS_DIR).mkdir(exist_ok=True)
    timestamp = safe_slug(str(result.get("timestamp") or datetime.now(UTC).isoformat()))
    sha = safe_slug(str(result.get("sha") or "unknown"))[:12] or "unknown"
    review_path = Path(REVIEWS_DIR) / f"{timestamp}-{sha}.json"
    review_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return review_path


def load_reviews() -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for path in sorted(Path(REVIEWS_DIR).glob("*.json")):
        try:
            reviews.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print(f"GuardianCI metrics ignored invalid review record: {path}")
    return reviews


def build_summary(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(UTC)
    recent = [review for review in reviews if review_timestamp(review) >= now - timedelta(days=30)]
    score_values = [int(review.get("score", 100)) for review in recent]
    all_findings = [finding for review in reviews for finding in review.get("findings", [])]
    top_files = Counter(str(finding.get("file", "unknown")) for finding in all_findings)
    severity_counts = Counter(str(finding.get("severity", "INFO")) for finding in all_findings)
    frameworks = Counter(
        framework
        for finding in all_findings
        for framework in finding.get("frameworks", [])
        if framework
    )

    rolling_score = round(sum(score_values) / len(score_values), 2) if score_values else 100
    return {
        "schema_version": 1,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "rolling_30_day_score": rolling_score,
        "total_prs_reviewed": len(reviews),
        "total_findings": len(all_findings),
        "severity_breakdown": dict(sorted(severity_counts.items())),
        "top_vulnerable_modules": [
            {"file": file_path, "findings": count} for file_path, count in top_files.most_common(5)
        ],
        "top_frameworks": [
            {"framework": framework, "findings": count}
            for framework, count in frameworks.most_common(8)
        ],
        "recent_reviews": [
            {
                "pr_number": review.get("pr_number"),
                "sha": review.get("sha"),
                "score": review.get("score", 100),
                "total_critical": review.get("total_critical", 0),
                "total_warn": review.get("total_warn", 0),
                "timestamp": review.get("timestamp"),
            }
            for review in sorted(reviews, key=lambda item: str(item.get("timestamp", "")))[-30:]
        ],
    }


def review_timestamp(review: dict[str, Any]) -> datetime:
    value = str(review.get("timestamp") or "")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=UTC)


def render_badge(summary: dict[str, Any]) -> str:
    score = int(round(float(summary.get("rolling_30_day_score", 100))))
    color = "brightgreen" if score >= 90 else "yellow" if score >= 70 else "red"
    return (
        f"![GuardianCI](https://img.shields.io/badge/GuardianCI-{score}%2F100-{color})\n\n"
        f"Rolling 30-day GuardianCI security score: **{score}/100**\n"
    )


def render_dashboard(summary: dict[str, Any]) -> str:
    summary_json = html.escape(json.dumps(summary, indent=2))
    generated_at = html.escape(str(summary.get("generated_at", "")))
    rolling_score = html.escape(str(summary.get("rolling_30_day_score", "")))
    total_prs = html.escape(str(summary.get("total_prs_reviewed", "")))
    total_findings = html.escape(str(summary.get("total_findings", "")))
    severity_breakdown = html.escape(json.dumps(summary.get("severity_breakdown", {}), indent=2))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GuardianCI Security Dashboard</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 980px; }}
    h1 {{ margin-bottom: 0.25rem; }}
    .score {{ font-size: 3rem; font-weight: 700; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }}
    section {{ border: 1px solid #ddd; border-radius: 8px; padding: 1rem; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td, th {{ border-bottom: 1px solid #eee; padding: 0.5rem; text-align: left; }}
  </style>
</head>
<body>
  <h1>GuardianCI Security Dashboard</h1>
  <p>Generated at {generated_at}</p>
  <div class="score">{rolling_score}/100</div>
  <div class="grid">
    <section>
      <h2>Findings</h2>
      <p>Total PRs reviewed: {total_prs}</p>
      <p>Total findings: {total_findings}</p>
    </section>
    <section>
      <h2>Severity</h2>
      <pre>{severity_breakdown}</pre>
    </section>
  </div>
  <section>
    <h2>Top Vulnerable Modules</h2>
    <table><tbody>
      {render_rows(summary.get("top_vulnerable_modules", []), "file")}
    </tbody></table>
  </section>
  <section>
    <h2>Recent Reviews</h2>
    <pre id="data">{summary_json}</pre>
  </section>
</body>
</html>
"""


def render_rows(items: list[dict[str, Any]], key: str) -> str:
    return "\n".join(
        (
            f"<tr><td>{html.escape(str(item.get(key, '')))}</td>"
            f"<td>{html.escape(str(item.get('findings', '')))}</td></tr>"
        )
        for item in items
    )


def safe_slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")


def has_git_changes() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=True,
    )
    return bool(result.stdout.strip())


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
