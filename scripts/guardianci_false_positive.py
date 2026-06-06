#!/usr/bin/env python
"""Record GuardianCI false-positive feedback from PR comments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

METRICS_BRANCH = "guardianci-metrics"
EXCLUSIONS_FILE = "exclusions.json"
HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def main() -> int:
    parser = argparse.ArgumentParser(description="Handle GuardianCI /fp feedback.")
    parser.add_argument("--branch", default=os.getenv("GUARDIANCI_METRICS_BRANCH", METRICS_BRANCH))
    parser.add_argument("--audit", action="store_true", help="Post the monthly exclusions audit.")
    args = parser.parse_args()

    if args.audit:
        return post_exclusions_audit(args.branch)

    context = feedback_context()
    if context is None:
        print("GuardianCI false-positive feedback skipped: no /fp PR comment found.")
        return 0

    target = resolve_target_review_comment(context)
    if target is None:
        post_issue_comment(
            context,
            (
                "GuardianCI could not identify the review finding to mark as false positive. "
                "Reply with `/fp` directly under a GuardianCI inline finding, or use `/fp <comment-id>`."
            ),
        )
        return 0

    record = exclusion_record(context, target)
    if record is None:
        post_issue_comment(
            context,
            "GuardianCI could not extract a GuardianCI finding from the selected comment.",
        )
        return 0

    written = append_exclusion_record(args.branch, record)
    confirmation = (
        "GuardianCI: this pattern has been noted and will be excluded from future reviews."
        if written
        else "GuardianCI: this false-positive pattern was already recorded."
    )
    post_confirmation(context, target, confirmation)
    return 0


def feedback_context() -> dict[str, Any] | None:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not event_path or not token or not repo:
        return None

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    comment = event.get("comment") or {}
    body = str(comment.get("body") or "").strip()
    if not is_false_positive_command(body):
        return None

    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    pr_number: int | None = None
    if event_name == "issue_comment":
        issue = event.get("issue") or {}
        if not issue.get("pull_request"):
            return None
        pr_number = int(issue["number"])
    elif event_name == "pull_request_review_comment":
        pr = event.get("pull_request") or {}
        pr_number = int(pr["number"])
    else:
        return None

    return {
        "token": token,
        "repo": repo,
        "event_name": event_name,
        "pr_number": pr_number,
        "comment": comment,
        "sender": ((event.get("sender") or {}).get("login") or "unknown"),
    }


def is_false_positive_command(body: str) -> bool:
    parts = body.strip().split()
    return bool(parts and parts[0].lower() == "/fp")


def resolve_target_review_comment(context: dict[str, Any]) -> dict[str, Any] | None:
    comment = context["comment"]
    command_body = str(comment.get("body") or "")
    explicit_id = parse_comment_id(command_body)
    if explicit_id:
        return fetch_review_comment(context, explicit_id)

    if context["event_name"] == "pull_request_review_comment":
        reply_to = comment.get("in_reply_to_id")
        if reply_to:
            return fetch_review_comment(context, int(reply_to))
        return None

    return latest_guardianci_review_comment(context)


def parse_comment_id(body: str) -> int | None:
    match = re.search(r"(?:comment[_ -]?id[:=]?\s*)?(\d{5,})", body, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def latest_guardianci_review_comment(context: dict[str, Any]) -> dict[str, Any] | None:
    comments = fetch_review_comments(context)
    candidates = [
        comment for comment in comments if is_guardianci_finding_body(comment.get("body"))
    ]
    return candidates[-1] if candidates else None


def fetch_review_comments(context: dict[str, Any]) -> list[dict[str, Any]]:
    url = (
        f"https://api.github.com/repos/{context['repo']}/pulls/"
        f"{context['pr_number']}/comments?per_page=100"
    )
    response = requests.get(url, headers=github_headers(context), timeout=20)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def fetch_review_comment(context: dict[str, Any], comment_id: int) -> dict[str, Any] | None:
    url = f"https://api.github.com/repos/{context['repo']}/pulls/comments/{comment_id}"
    response = requests.get(url, headers=github_headers(context), timeout=20)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def exclusion_record(context: dict[str, Any], target: dict[str, Any]) -> dict[str, Any] | None:
    body = str(target.get("body") or "")
    if not is_guardianci_finding_body(body):
        return None

    issue_type = extract_issue_type(body)
    file_path = str(target.get("path") or "").strip()
    code_context = target_code_context(target)
    if not issue_type or not file_path or not code_context:
        return None

    return {
        "schema_version": 1,
        "active": True,
        "file_pattern": file_path,
        "issue_type": issue_type,
        "code_context_hash": code_context_hash(code_context),
        "dismissed_by": context["sender"],
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_pr_number": context["pr_number"],
        "source_comment_id": target.get("id"),
    }


def is_guardianci_finding_body(body: Any) -> bool:
    text = str(body or "")
    return "**GuardianCI " in text and "Suggested fix:" in text


def extract_issue_type(body: str) -> str:
    text = re.sub(r"^\*\*GuardianCI\s+\w+\*\*\s*", "", body.strip())
    issue = text.split("Suggested fix:", 1)[0].strip()
    issue = re.sub(r"\s+", " ", issue)
    return issue[:180]


def target_code_context(comment: dict[str, Any]) -> str:
    target_line = comment.get("line") or comment.get("original_line")
    if not isinstance(target_line, int):
        return ""

    new_line: int | None = None
    for raw_line in str(comment.get("diff_hunk") or "").splitlines():
        hunk = HUNK_RE.match(raw_line)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if new_line is None:
            continue
        if raw_line.startswith("+"):
            if new_line == target_line:
                return raw_line[1:]
            new_line += 1
        elif raw_line.startswith("-") or raw_line.startswith("\\"):
            continue
        else:
            new_line += 1
    return ""


def code_context_hash(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def append_exclusion_record(branch: str, record: dict[str, Any]) -> bool:
    ensure_git_identity()
    ensure_metrics_branch(branch)
    payload = load_local_exclusions()
    exclusions = payload.setdefault("exclusions", [])
    if any(same_exclusion(item, record) for item in exclusions if isinstance(item, dict)):
        return False

    payload["schema_version"] = 1
    payload["updated_at"] = record["timestamp"]
    exclusions.append(record)
    Path(EXCLUSIONS_FILE).write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    run_git(["add", EXCLUSIONS_FILE])
    if not has_git_changes([EXCLUSIONS_FILE]):
        return False
    run_git(
        ["commit", "-m", f"GuardianCI false-positive exclusion: PR #{record['source_pr_number']}"]
    )
    run_git(["push", "origin", f"HEAD:{branch}"])
    print(f"GuardianCI false-positive exclusion published to {branch}.")
    return True


def same_exclusion(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = ("file_pattern", "issue_type", "code_context_hash")
    return all(str(left.get(key) or "") == str(right.get(key) or "") for key in keys)


def load_local_exclusions() -> dict[str, Any]:
    path = Path(EXCLUSIONS_FILE)
    if not path.exists():
        return {"schema_version": 1, "exclusions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": 1, "exclusions": []}
    if isinstance(payload, list):
        return {"schema_version": 1, "exclusions": payload}
    if not isinstance(payload, dict):
        return {"schema_version": 1, "exclusions": []}
    if not isinstance(payload.get("exclusions"), list):
        payload["exclusions"] = []
    return payload


def post_confirmation(context: dict[str, Any], target: dict[str, Any], body: str) -> None:
    if context["event_name"] == "pull_request_review_comment":
        url = (
            f"https://api.github.com/repos/{context['repo']}/pulls/"
            f"{context['pr_number']}/comments/{target['id']}/replies"
        )
        response = requests.post(
            url, headers=github_headers(context), json={"body": body}, timeout=20
        )
        if response.status_code in {201, 422}:
            if response.status_code == 422:
                post_issue_comment(context, body)
            return
        response.raise_for_status()
        return

    post_issue_comment(context, body)


def post_issue_comment(context: dict[str, Any], body: str) -> None:
    url = f"https://api.github.com/repos/{context['repo']}/issues/{context['pr_number']}/comments"
    response = requests.post(url, headers=github_headers(context), json={"body": body}, timeout=20)
    response.raise_for_status()


def post_exclusions_audit(branch: str) -> int:
    exclusions = load_remote_exclusions(branch)
    active = [
        item for item in exclusions if isinstance(item, dict) and item.get("active") is not False
    ]
    issue_number = os.getenv("GUARDIANCI_EXCLUSIONS_AUDIT_ISSUE")
    if not issue_number:
        print(render_audit_body(active))
        print("GuardianCI exclusions audit issue is not configured; printed audit only.")
        return 0

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required for audit comments.")

    context = {"token": token, "repo": repo, "pr_number": int(issue_number)}
    post_issue_comment(context, render_audit_body(active))
    return 0


def load_remote_exclusions(branch: str) -> list[dict[str, Any]]:
    subprocess.run(
        ["git", "fetch", "--no-tags", "origin", f"{branch}:refs/remotes/origin/{branch}"],
        check=False,
        text=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "show", f"origin/{branch}:{EXCLUSIONS_FILE}"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    exclusions = payload.get("exclusions", payload) if isinstance(payload, dict) else payload
    return exclusions if isinstance(exclusions, list) else []


def render_audit_body(exclusions: list[dict[str, Any]]) -> str:
    if not exclusions:
        return "GuardianCI monthly false-positive audit: no active exclusions recorded."

    lines = [
        "GuardianCI monthly false-positive audit.",
        "",
        f"Active exclusions: {len(exclusions)}",
        "",
    ]
    for item in exclusions[:50]:
        lines.append(
            "- "
            f"`{item.get('file_pattern', 'unknown')}` | "
            f"{item.get('issue_type', 'unknown issue')} | "
            f"dismissed by `{item.get('dismissed_by', 'unknown')}`"
        )
    if len(exclusions) > 50:
        lines.append(f"- ... {len(exclusions) - 50} more exclusion(s)")
    return "\n".join(lines)


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
    for path in list(Path(".").iterdir()):
        if path.name == ".git":
            continue
        remove_path(path)


def remove_path(path: Path) -> None:
    if path.is_dir():
        for child in path.iterdir():
            remove_path(child)
        path.rmdir()
    else:
        path.unlink()


def has_git_changes(paths: list[str]) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *paths],
        check=True,
        text=True,
        capture_output=True,
    )
    return bool(result.stdout.strip())


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True)


def github_headers(context: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {context['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


if __name__ == "__main__":
    raise SystemExit(main())
