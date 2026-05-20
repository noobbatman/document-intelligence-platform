#!/usr/bin/env python
"""GuardianCI Phase 2: Gemini-powered PR security review."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

DEFAULT_MODEL = "gemini-2.0-flash"
MAX_DIFF_CHARS = 32000
MAX_INLINE_COMMENTS = 25
ALLOWED_SEVERITIES = {"CRITICAL", "WARN", "INFO"}
RELEVANT_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".example",
    ".dockerfile",
}
RELEVANT_FILENAMES = {
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
}


@dataclass(frozen=True)
class Finding:
    file: str
    line_start: int
    line_end: int
    severity: str
    issue: str
    suggested_fix: str

    @property
    def is_critical(self) -> bool:
        return self.severity == "CRITICAL"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GuardianCI Gemini security review.")
    parser.add_argument("--base-ref", default=os.getenv("GITHUB_BASE_REF", "main"))
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--max-diff-chars", type=int, default=MAX_DIFF_CHARS)
    args = parser.parse_args()

    context = github_context()
    if context is None:
        print("GuardianCI AI review only runs on pull_request events; skipping.")
        return 0

    try:
        diff_text = collect_diff(args.base_ref)
        file_patches = split_file_patches(diff_text)
        relevant_patches = [(path, patch) for path, patch in file_patches if is_relevant_path(path)]
        changed_lines = changed_new_lines(relevant_patches)
        review_diff, truncated = truncate_diff(relevant_patches, args.max_diff_chars)

        if not review_diff.strip():
            post_review(
                context,
                body="GuardianCI Gemini review found no security-relevant changed files.",
                event="COMMENT",
                comments=[],
            )
            return 0

        raw_response = call_gemini(review_diff, truncated=truncated, model=args.model)
        findings, validation_errors = validate_findings(raw_response, changed_lines)
    except json.JSONDecodeError as exc:
        post_review(
            context,
            body=(
                "GuardianCI Gemini review could not parse Gemini's JSON response. "
                f"The pipeline is continuing safely.\n\nParse error: `{exc}`"
            ),
            event="COMMENT",
            comments=[],
        )
        return 0
    except Exception as exc:
        post_review(
            context,
            body=f"GuardianCI Gemini review failed before completion: `{exc}`",
            event="COMMENT",
            comments=[],
        )
        return 1

    body = render_review_body(findings, validation_errors, truncated)
    comments = inline_comments(findings, changed_lines)
    event = "REQUEST_CHANGES" if any(finding.is_critical for finding in findings) else "COMMENT"
    post_review(context, body=body, event=event, comments=comments)

    if any(finding.is_critical for finding in findings):
        print("GuardianCI found CRITICAL security findings.")
        return 1

    print(f"GuardianCI completed with {len(findings)} finding(s).")
    return 0


def github_context() -> dict[str, Any] | None:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        return None

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pr = event.get("pull_request")
    if not pr:
        return None

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required.")

    return {"token": token, "repo": repo, "pr_number": pr["number"]}


def collect_diff(base_ref: str) -> str:
    base = f"origin/{base_ref}"
    subprocess.run(
        ["git", "fetch", "--no-tags", "origin", f"{base_ref}:refs/remotes/{base}"],
        check=False,
    )
    result = subprocess.run(
        ["git", "diff", "--unified=20", "--diff-filter=ACMRT", f"{base}...HEAD"],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def split_file_patches(diff_text: str) -> list[tuple[str, str]]:
    patches: list[tuple[str, str]] = []
    current: list[str] = []
    current_path: str | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current_path and current:
                patches.append((current_path, "\n".join(current)))
            current = [line]
            current_path = None
            continue

        if current:
            current.append(line)
            if line.startswith("+++ b/"):
                current_path = line.removeprefix("+++ b/")

    if current_path and current:
        patches.append((current_path, "\n".join(current)))

    return patches


def is_relevant_path(path: str) -> bool:
    name = Path(path).name
    lowered = path.lower()
    if name in RELEVANT_FILENAMES:
        return True
    if ".env" in name:
        return True
    return any(lowered.endswith(suffix) for suffix in RELEVANT_SUFFIXES)


def truncate_diff(patches: list[tuple[str, str]], max_chars: int) -> tuple[str, bool]:
    chunks: list[str] = []
    total = 0
    truncated = False
    for _path, patch in patches:
        addition = len(patch) + 2
        if total + addition > max_chars:
            truncated = True
            break
        chunks.append(patch)
        total += addition
    return "\n\n".join(chunks), truncated


def changed_new_lines(patches: list[tuple[str, str]]) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = {}
    hunk_re = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for path, patch in patches:
        lines: set[int] = set()
        new_line: int | None = None
        for raw_line in patch.splitlines():
            hunk = hunk_re.match(raw_line)
            if hunk:
                new_line = int(hunk.group(1))
                continue
            if new_line is None:
                continue
            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                lines.add(new_line)
                new_line += 1
            elif raw_line.startswith("-") and not raw_line.startswith("---"):
                continue
            else:
                new_line += 1
        changed[path] = lines

    return changed


def call_gemini(diff_text: str, *, truncated: bool, model: str) -> Any:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is required for GuardianCI AI review.") from exc

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=user_prompt(diff_text, truncated=truncated),
        config=types.GenerateContentConfig(
            system_instruction=system_prompt(),
            response_mime_type="application/json",
            temperature=0,
            max_output_tokens=4096,
        ),
    )
    return parse_json_response(response.text or "")


def system_prompt() -> str:
    return (
        "You are GuardianCI, a fintech-focused security reviewer for pull request diffs. "
        "Review only the changed lines in the provided unified diff. "
        "Find concrete security risks. Do not report style, maintainability, or speculative issues. "
        "Return strict JSON only."
    )


def user_prompt(diff_text: str, *, truncated: bool) -> str:
    truncation_note = (
        "The diff was truncated to fit the review budget; mention only findings visible below.\n"
        if truncated
        else ""
    )
    return f"""
{truncation_note}
Review this PR diff for:
- Hardcoded secrets / API keys
- HMAC signature bypass patterns
- JWT algorithm confusion, especially alg=none
- SQL injection in raw SQLAlchemy or string-built SQL
- Missing RBAC or tenant checks on new endpoints
- PII logged in plaintext
- Unvalidated Pydantic models on financial or legal data

Return JSON in this exact shape:
{{
  "findings": [
    {{
      "file": "path/from/repo/root.py",
      "line_start": 10,
      "line_end": 12,
      "severity": "CRITICAL | WARN | INFO",
      "issue": "Concrete issue visible in the diff.",
      "suggested_fix": "Concrete fix."
    }}
  ]
}}

If there are no findings, return {{"findings": []}}.
Only use new-file line numbers from the diff. Only report issues on changed lines.

DIFF:
{diff_text}
""".strip()


def parse_json_response(raw: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    object_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if object_match:
        return json.loads(object_match.group(0))

    array_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if array_match:
        return json.loads(array_match.group(0))

    raise json.JSONDecodeError("Could not parse Gemini response as JSON", raw, 0)


def validate_findings(
    payload: Any, changed_lines: dict[str, set[int]]
) -> tuple[list[Finding], list[str]]:
    raw_findings = payload.get("findings", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_findings, list):
        raise json.JSONDecodeError("Gemini JSON must contain a findings array", str(payload), 0)

    valid: list[Finding] = []
    errors: list[str] = []
    relevant_files = set(changed_lines)

    for idx, item in enumerate(raw_findings, start=1):
        if not isinstance(item, dict):
            errors.append(f"Finding {idx} was not an object.")
            continue

        try:
            file_path = str(item["file"])
            line_start = int(item["line_start"])
            line_end = int(item.get("line_end") or line_start)
            severity = str(item["severity"]).upper()
            issue = str(item["issue"]).strip()
            suggested_fix = str(item["suggested_fix"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"Finding {idx} has invalid fields: {exc}.")
            continue

        if file_path not in relevant_files:
            errors.append(
                f"Finding {idx} references a file outside the reviewed diff: {file_path}."
            )
            continue
        if severity not in ALLOWED_SEVERITIES:
            errors.append(f"Finding {idx} has invalid severity: {severity}.")
            continue
        if line_start < 1 or line_end < line_start:
            errors.append(f"Finding {idx} has invalid line range: {line_start}-{line_end}.")
            continue
        if not issue or not suggested_fix:
            errors.append(f"Finding {idx} must include issue and suggested_fix.")
            continue

        valid.append(
            Finding(
                file=file_path,
                line_start=line_start,
                line_end=line_end,
                severity=severity,
                issue=issue,
                suggested_fix=suggested_fix,
            )
        )

    return valid, errors


def render_review_body(
    findings: list[Finding], validation_errors: list[str], truncated: bool
) -> str:
    if not findings:
        body = "GuardianCI Gemini review found no blocking security findings."
    else:
        counts = {severity: 0 for severity in ALLOWED_SEVERITIES}
        for finding in findings:
            counts[finding.severity] += 1
        body = (
            "GuardianCI Gemini security review completed.\n\n"
            f"- CRITICAL: {counts['CRITICAL']}\n"
            f"- WARN: {counts['WARN']}\n"
            f"- INFO: {counts['INFO']}\n"
        )
        if counts["CRITICAL"]:
            body += "\nCRITICAL findings block this PR until fixed.\n"

    if truncated:
        body += "\nNote: the diff was truncated before review due to size limits.\n"
    if validation_errors:
        body += "\nSome Gemini findings were ignored because they failed schema validation:\n"
        body += "\n".join(f"- {error}" for error in validation_errors[:10])
    return body


def inline_comments(
    findings: list[Finding], changed_lines: dict[str, set[int]]
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for finding in findings[:MAX_INLINE_COMMENTS]:
        if finding.line_start not in changed_lines.get(finding.file, set()):
            continue
        comments.append(
            {
                "path": finding.file,
                "line": finding.line_start,
                "side": "RIGHT",
                "body": (
                    f"**GuardianCI {finding.severity}**\n\n"
                    f"{finding.issue}\n\n"
                    f"Suggested fix: {finding.suggested_fix}"
                ),
            }
        )
    return comments


def post_review(
    context: dict[str, Any],
    *,
    body: str,
    event: str,
    comments: list[dict[str, Any]],
) -> None:
    url = f"https://api.github.com/repos/{context['repo']}/pulls/{context['pr_number']}/reviews"
    headers = {
        "Authorization": f"Bearer {context['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload: dict[str, Any] = {"body": body, "event": event}
    if comments:
        payload["comments"] = comments

    response = requests.post(url, headers=headers, json=payload, timeout=20)
    if response.status_code == 422 and comments:
        # If GitHub rejects inline positions, keep the review signal as a body-only review.
        payload.pop("comments", None)
        response = requests.post(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()


if __name__ == "__main__":
    sys.exit(main())
