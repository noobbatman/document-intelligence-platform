#!/usr/bin/env python
"""GuardianCI Phase 3: Gemini-powered PR security and compliance review."""

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

DEFAULT_MODEL = "gemma-4-31b-it"
MAX_DIFF_CHARS = 8000
MAX_INLINE_COMMENTS = 25
MAX_AUTOFIX_FINDINGS = 3
ALLOWED_SEVERITIES = {"CRITICAL", "WARN", "INFO"}
SEVERITY_ORDER = ("CRITICAL", "WARN", "INFO")
ALLOWED_REMEDIATION_URGENCIES = {"before-merge", "within-sprint", "backlog"}
URGENCY_ORDER = ("before-merge", "within-sprint", "backlog")
HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
SKIPPED_REVIEW_PREFIXES = (
    "docs/",
    "tests/",
    "sample_docs/",
    "evaluation/results/",
    "data/",
)
SKIPPED_REVIEW_PATHS = {
    "scripts/guardianci_ai_review.py",
}
HIGH_RISK_PREFIXES = (
    ".github/workflows/",
    "app/api/",
    "app/core/",
    "app/db/",
    "app/services/",
    "app/workers/",
)
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
    frameworks: tuple[str, ...] = ()
    remediation_urgency: str = "within-sprint"

    @property
    def is_critical(self) -> bool:
        return self.severity == "CRITICAL"


@dataclass(frozen=True)
class AutoFixResult:
    branch: str
    pr_url: str | None
    fixed_files: tuple[str, ...]
    needs_human_review: bool


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GuardianCI Gemini security review.")
    parser.add_argument("--base-ref", default=os.getenv("GITHUB_BASE_REF", "main"))
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--max-diff-chars", type=int, default=MAX_DIFF_CHARS)
    parser.add_argument(
        "--gemini-enabled",
        default=os.getenv("GUARDIANCI_GEMINI_ENABLED", "false"),
        help="Set true to call Gemini after the local security preflight.",
    )
    parser.add_argument(
        "--auto-fix-enabled",
        default=os.getenv("GUARDIANCI_AUTOFIX_ENABLED", "false"),
        help="Set true to create draft fix PRs for CRITICAL findings.",
    )
    args = parser.parse_args()

    context = github_context()
    if context is None:
        print("GuardianCI AI review only runs on pull_request events; skipping.")
        return 0

    try:
        diff_text = collect_diff(args.base_ref)
        file_patches = split_file_patches(diff_text)
        relevant_patches = select_review_patches(file_patches)
        changed_lines = changed_new_lines(relevant_patches)
        review_diff, truncated = truncate_diff(relevant_patches, args.max_diff_chars)
        local_findings = local_security_findings(relevant_patches)

        if not review_diff.strip():
            post_review(
                context,
                body="GuardianCI compliance review found no security-relevant changed files.",
                event="COMMENT",
                comments=[],
            )
            return 0

        if not truthy(args.gemini_enabled):
            body = render_review_body(local_findings, [], truncated, gemini_ran=False)
            body += (
                "\n\nGemini review is disabled for this run to protect API quota. "
                "Set `GUARDIANCI_GEMINI_ENABLED=true` to enable model review."
            )
            comments = inline_comments(local_findings, changed_lines)
            event = (
                "REQUEST_CHANGES"
                if any(finding.is_critical for finding in local_findings)
                else "COMMENT"
            )
            post_review(context, body=body, event=event, comments=comments)
            return 1 if any(finding.is_critical for finding in local_findings) else 0

        raw_response = call_gemini(review_diff, truncated=truncated, model=args.model)
        findings, validation_errors = validate_findings(raw_response, changed_lines)
        findings = merge_findings(local_findings, findings)
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
        if is_quota_or_rate_limit_error(exc):
            post_review(
                context,
                body=(
                    "GuardianCI Gemini review was skipped because Gemini returned a quota or "
                    "rate-limit error. The pipeline is continuing safely.\n\n"
                    f"Provider error: `{exc}`\n\n"
                    "Use a paid Gemini quota, reduce PR size, or rerun after quota resets."
                ),
                event="COMMENT",
                comments=[],
            )
            print(f"GuardianCI skipped Gemini review due to quota/rate limit: {exc}")
            return 0
        post_review(
            context,
            body=f"GuardianCI Gemini review failed before completion: `{exc}`",
            event="COMMENT",
            comments=[],
        )
        return 1

    body = render_review_body(findings, validation_errors, truncated, gemini_ran=True)
    comments = inline_comments(findings, changed_lines)
    event = "REQUEST_CHANGES" if any(finding.is_critical for finding in findings) else "COMMENT"
    post_review(context, body=body, event=event, comments=comments)

    critical_findings = [finding for finding in findings if finding.is_critical]
    if critical_findings:
        if truthy(args.auto_fix_enabled):
            try:
                result = prepare_auto_fix_pull_request(context, critical_findings, model=args.model)
                if result:
                    post_auto_fix_comment(context, result)
            except Exception as exc:
                post_issue_comment(
                    context,
                    (
                        "GuardianCI could not prepare an auto-fix PR for the CRITICAL "
                        f"finding(s): `{exc}`"
                    ),
                )
                print(f"GuardianCI auto-fix failed: {exc}")
        else:
            print("GuardianCI auto-fix is disabled for this run.")
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

    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    return {
        "token": token,
        "repo": repo,
        "pr_number": pr["number"],
        "pr_url": pr.get("html_url", ""),
        "pr_author": (pr.get("user") or {}).get("login", ""),
        "head_ref": head.get("ref", ""),
        "head_sha": head.get("sha", os.getenv("GITHUB_SHA", "")),
        "head_repo": head_repo.get("full_name", repo),
    }


def collect_diff(base_ref: str) -> str:
    base = f"origin/{base_ref}"
    subprocess.run(
        ["git", "fetch", "--no-tags", "origin", f"{base_ref}:refs/remotes/{base}"],
        check=False,
    )
    result = subprocess.run(
        ["git", "diff", "--unified=5", "--diff-filter=ACMRT", f"{base}...HEAD"],
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


def truthy(value: str | bool | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_reviewable_path(path: str) -> bool:
    lowered = path.lower()
    if lowered in SKIPPED_REVIEW_PATHS:
        return False
    if any(lowered.startswith(prefix) for prefix in SKIPPED_REVIEW_PREFIXES):
        return False
    return is_relevant_path(path)


def review_priority(path: str) -> tuple[int, str]:
    lowered = path.lower()
    name = Path(path).name
    if any(lowered.startswith(prefix) for prefix in HIGH_RISK_PREFIXES):
        return (0, path)
    if lowered.startswith("app/"):
        return (1, path)
    if name in RELEVANT_FILENAMES or ".env" in name:
        return (2, path)
    return (3, path)


def select_review_patches(file_patches: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted(
        [(path, patch) for path, patch in file_patches if is_reviewable_path(path)],
        key=lambda item: review_priority(item[0]),
    )


def truncate_diff(patches: list[tuple[str, str]], max_chars: int) -> tuple[str, bool]:
    chunks: list[str] = []
    total = 0
    truncated = False
    for _path, patch in patches:
        addition = len(patch) + 2
        if total + addition > max_chars:
            if not chunks:
                chunks.append(patch[:max_chars] + "\n... [GuardianCI truncated this file diff]")
            truncated = True
            break
        chunks.append(patch)
        total += addition
    return "\n\n".join(chunks), truncated


def changed_new_lines(patches: list[tuple[str, str]]) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = {}

    for path, patch in patches:
        changed[path] = {
            line_no for line_no, _line, is_added in parse_hunk_lines(patch) if is_added
        }

    return changed


def iter_added_lines(path: str, patch: str) -> list[tuple[str, int, str]]:
    return [
        (path, line_no, line) for line_no, line, is_added in parse_hunk_lines(patch) if is_added
    ]


def parse_hunk_lines(patch: str) -> list[tuple[int, str, bool]]:
    lines: list[tuple[int, str, bool]] = []
    new_line: int | None = None

    for raw_line in patch.splitlines():
        hunk = HUNK_RE.match(raw_line)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if new_line is None:
            continue
        if raw_line.startswith("+"):
            lines.append((new_line, raw_line[1:], True))
            new_line += 1
        elif raw_line.startswith("-") or raw_line.startswith("\\"):
            continue
        else:
            content = raw_line[1:] if raw_line.startswith(" ") else raw_line
            lines.append((new_line, content, False))
            new_line += 1
    return lines


def local_security_findings(patches: list[tuple[str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    secret_re = re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}"
    )
    gemini_key_re = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")

    for path, patch in patches:
        for file_path, line_no, line in iter_added_lines(path, patch):
            lowered = line.lower()
            if ("os.getenv" not in line and "secrets." not in line) and (
                secret_re.search(line) or gemini_key_re.search(line)
            ):
                findings.append(
                    Finding(
                        file=file_path,
                        line_start=line_no,
                        line_end=line_no,
                        severity="CRITICAL",
                        issue="Possible hardcoded secret or API key added in this change.",
                        suggested_fix="Move the value into a GitHub secret or environment variable.",
                        frameworks=("PCI-DSS 6.4.3", "SOC 2 CC6.1", "GDPR Art. 32"),
                        remediation_urgency="before-merge",
                    )
                )
            if (
                "alg" in lowered
                and "none" in lowered
                and ("jwt" in lowered or "algorithm" in lowered)
            ):
                findings.append(
                    Finding(
                        file=file_path,
                        line_start=line_no,
                        line_end=line_no,
                        severity="CRITICAL",
                        issue="JWT code appears to allow or reference the `alg=none` bypass pattern.",
                        suggested_fix="Require a fixed signing algorithm and reject unsigned tokens.",
                        frameworks=("SOC 2 CC6.1", "GDPR Art. 32"),
                        remediation_urgency="before-merge",
                    )
                )
            if re.search(r"\b(execute|text)\s*\(\s*f['\"]", line) or re.search(
                r"f['\"].*\b(select|insert|update|delete)\b.*\{", lowered
            ):
                findings.append(
                    Finding(
                        file=file_path,
                        line_start=line_no,
                        line_end=line_no,
                        severity="CRITICAL",
                        issue="String-built SQL with interpolation can allow SQL injection.",
                        suggested_fix="Use SQLAlchemy bind parameters instead of interpolating user data.",
                        frameworks=("PCI-DSS 6.2.4", "SOC 2 CC6.1", "GDPR Art. 32"),
                        remediation_urgency="before-merge",
                    )
                )
            if "verify=false" in lowered:
                findings.append(
                    Finding(
                        file=file_path,
                        line_start=line_no,
                        line_end=line_no,
                        severity="WARN",
                        issue="TLS certificate verification is disabled.",
                        suggested_fix="Remove `verify=False` and trust a configured CA bundle if needed.",
                        frameworks=("SOC 2 CC6.7", "GDPR Art. 32"),
                        remediation_urgency="within-sprint",
                    )
                )
            if re.search(r"\b(print|logger\.\w+)\s*\(.*\b(ssn|password|token|api_key)\b", lowered):
                findings.append(
                    Finding(
                        file=file_path,
                        line_start=line_no,
                        line_end=line_no,
                        severity="WARN",
                        issue="Potentially sensitive data is written to logs or stdout.",
                        suggested_fix="Remove sensitive values from logs or log only redacted metadata.",
                        frameworks=("SOC 2 CC7.2", "GDPR Art. 32"),
                        remediation_urgency="within-sprint",
                    )
                )

    return dedupe_findings(findings)


def dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, int, str, str]] = set()
    output: list[Finding] = []
    for finding in findings:
        key = (finding.file, finding.line_start, finding.severity, finding.issue)
        if key in seen:
            continue
        seen.add(key)
        output.append(finding)
    return output


def merge_findings(first: list[Finding], second: list[Finding]) -> list[Finding]:
    return dedupe_findings([*first, *second])


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
        "Map each issue to relevant PCI-DSS 4.0, SOC 2 Type II, or GDPR control citations when applicable. "
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

For each finding, map visible compliance impact using only these framework families:
- PCI-DSS 4.0 controls for payment data, cryptography, secure development, access control, and logging
- SOC 2 Type II CC6, CC7, and CC8 controls
- GDPR Art. 25 and Art. 32

Return JSON in this exact shape:
{{
  "findings": [
    {{
      "file": "path/from/repo/root.py",
      "line_start": 10,
      "line_end": 12,
      "severity": "CRITICAL | WARN | INFO",
      "issue": "Concrete issue visible in the diff.",
      "suggested_fix": "Concrete fix.",
      "frameworks": ["PCI-DSS 6.4.3", "SOC 2 CC6.1", "GDPR Art. 32"],
      "remediation_urgency": "before-merge | within-sprint | backlog"
    }}
  ]
}}

If there are no findings, return {{"findings": []}}.
Only use new-file line numbers from the diff. Only report issues on changed lines.

DIFF:
{diff_text}
""".strip()


def fix_system_prompt() -> str:
    return (
        "You are GuardianCI Auto-Fix. Produce the smallest safe code replacement for "
        "the requested vulnerable line range. Do not review unrelated code. Return strict JSON only."
    )


def fix_user_prompt(file_path: str, file_text: str, finding: Finding) -> str:
    return f"""
Fix this CRITICAL GuardianCI finding.

File: {file_path}
Line range to replace: {finding.line_start}-{finding.line_end}
Issue: {finding.issue}
Suggested fix: {finding.suggested_fix}
Frameworks: {", ".join(finding.frameworks) if finding.frameworks else "None mapped"}

Return JSON in this exact shape:
{{
  "replacement": "The corrected code block that should replace exactly the vulnerable line range.",
  "explanation": "One concise sentence explaining the fix."
}}

Rules:
- Return only the replacement code for the stated line range, not the full file.
- Preserve indentation needed at that location.
- Do not include markdown fences inside the replacement value.
- Do not introduce new dependencies unless the file already imports them.

CURRENT FILE CONTENT:
```text
{file_text}
```
""".strip()


def call_gemini_fix(file_path: str, file_text: str, finding: Finding, *, model: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai is required for GuardianCI auto-fix.") from exc

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=fix_user_prompt(file_path, file_text, finding),
        config=types.GenerateContentConfig(
            system_instruction=fix_system_prompt(),
            response_mime_type="application/json",
            temperature=0,
            max_output_tokens=2048,
        ),
    )
    payload = parse_json_response(response.text or "")
    return validate_fix_payload(payload)


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


def validate_fix_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Gemini auto-fix response must be an object.")
    replacement = payload.get("replacement")
    if not isinstance(replacement, str) or not replacement.strip():
        raise ValueError("Gemini auto-fix response must include a non-empty replacement.")
    return strip_code_fence(replacement)


def strip_code_fence(value: str) -> str:
    cleaned = value.strip("\n")
    cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*\n", "", cleaned)
    cleaned = re.sub(r"\n```$", "", cleaned)
    return cleaned


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
            line_end = int(item["line_end"]) if "line_end" in item else line_start
            severity = str(item["severity"]).upper()
            issue = str(item["issue"]).strip()
            suggested_fix = str(item["suggested_fix"]).strip()
            frameworks = normalize_frameworks(item.get("frameworks", []))
            remediation_urgency = (
                str(item.get("remediation_urgency", "within-sprint")).strip().lower()
            )
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
        if remediation_urgency not in ALLOWED_REMEDIATION_URGENCIES:
            errors.append(f"Finding {idx} has invalid remediation_urgency: {remediation_urgency}.")
            continue

        valid.append(
            Finding(
                file=file_path,
                line_start=line_start,
                line_end=line_end,
                severity=severity,
                issue=issue,
                suggested_fix=suggested_fix,
                frameworks=frameworks,
                remediation_urgency=remediation_urgency,
            )
        )

    return valid, errors


def normalize_frameworks(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("frameworks must be a list")
    frameworks: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        frameworks.append(normalized)
    return tuple(frameworks[:8])


def sorted_frameworks(findings: list[Finding]) -> list[str]:
    frameworks = {framework for finding in findings for framework in finding.frameworks}
    return sorted(frameworks)


def render_review_body(
    findings: list[Finding],
    validation_errors: list[str],
    truncated: bool,
    *,
    gemini_ran: bool = True,
) -> str:
    review_label = (
        "GuardianCI Gemini compliance review" if gemini_ran else "GuardianCI compliance review"
    )
    if not findings:
        body = f"{review_label} found no blocking security findings."
    else:
        counts = {severity: 0 for severity in SEVERITY_ORDER}
        urgency_counts = {urgency: 0 for urgency in URGENCY_ORDER}
        for finding in findings:
            counts[finding.severity] += 1
            urgency_counts[finding.remediation_urgency] += 1
        frameworks = sorted_frameworks(findings)
        body = (
            f"{review_label} completed.\n\n"
            f"Frameworks touched: {', '.join(frameworks) if frameworks else 'None mapped'}\n\n"
            f"- CRITICAL: {counts['CRITICAL']}\n"
            f"- WARN: {counts['WARN']}\n"
            f"- INFO: {counts['INFO']}\n"
            f"- before-merge: {urgency_counts['before-merge']}\n"
            f"- within-sprint: {urgency_counts['within-sprint']}\n"
            f"- backlog: {urgency_counts['backlog']}\n"
        )
        if counts["CRITICAL"]:
            body += "\nCRITICAL findings block this PR until fixed.\n"

    if truncated:
        body += "\nNote: the diff was truncated before review due to size limits.\n"
    if validation_errors:
        body += "\nSome Gemini findings were ignored because they failed schema validation:\n"
        body += "\n".join(f"- {error}" for error in validation_errors[:10])
    return body


def prepare_auto_fix_pull_request(
    context: dict[str, Any], critical_findings: list[Finding], *, model: str
) -> AutoFixResult | None:
    if context.get("head_repo") != context.get("repo"):
        print("GuardianCI auto-fix skipped because forked PR branches are not supported yet.")
        return None

    selected = critical_findings[:MAX_AUTOFIX_FINDINGS]
    if not selected:
        return None

    branch = auto_fix_branch_name(str(context.get("head_sha") or "unknown"), selected[0].issue)
    run_git(["config", "user.name", "GuardianCI Bot"])
    run_git(["config", "user.email", "guardianci-bot@users.noreply.github.com"])
    run_git(["checkout", "-B", branch])

    fixed_files: list[str] = []
    needs_human_review = False
    for finding in selected:
        path = Path(finding.file)
        if not path.exists() or not path.is_file():
            print(f"GuardianCI auto-fix skipped missing file: {finding.file}")
            continue

        file_text = path.read_text(encoding="utf-8")
        replacement = call_gemini_fix(finding.file, file_text, finding, model=model)
        apply_line_replacement(path, finding.line_start, finding.line_end, replacement)
        fixed_files.append(finding.file)
        if not quick_syntax_check(path):
            needs_human_review = True

    if not fixed_files:
        return None

    unique_files = sorted(set(fixed_files))
    run_git(["add", *unique_files])
    run_git(["commit", "-m", f"GuardianCI auto-fix: {selected[0].issue[:60]}"])
    run_git(["push", "--force-with-lease", "origin", f"HEAD:{branch}"])

    pr_url, pr_number = create_draft_fix_pr(
        context=context,
        branch=branch,
        findings=selected,
        fixed_files=unique_files,
        needs_human_review=needs_human_review,
    )
    if needs_human_review and pr_number:
        add_issue_labels(context, pr_number, ["needs-human-review"])
    if pr_number and context.get("pr_author"):
        request_fix_pr_reviewer(context, pr_number, str(context["pr_author"]))

    return AutoFixResult(
        branch=branch,
        pr_url=pr_url,
        fixed_files=tuple(unique_files),
        needs_human_review=needs_human_review,
    )


def auto_fix_branch_name(head_sha: str, issue: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", issue.lower()).strip("-")[:42] or "critical-finding"
    short_sha = re.sub(r"[^a-f0-9]", "", head_sha.lower())[:8] or "unknown"
    return f"guardianCI/fix-{short_sha}-{slug}"


def apply_line_replacement(path: Path, line_start: int, line_end: int, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    had_trailing_newline = text.endswith("\n")
    lines = text.splitlines()
    if line_start < 1 or line_end < line_start or line_end > len(lines):
        raise ValueError(f"Invalid replacement range for {path}: {line_start}-{line_end}")

    replacement_lines = strip_code_fence(replacement).splitlines()
    lines[line_start - 1 : line_end] = replacement_lines
    output = "\n".join(lines)
    if had_trailing_newline:
        output += "\n"
    path.write_text(output, encoding="utf-8")


def quick_syntax_check(path: Path) -> bool:
    if path.suffix != ".py":
        return True
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"GuardianCI auto-fix syntax check failed for {path}:\n{result.stderr}")
        return False
    return True


def create_draft_fix_pr(
    *,
    context: dict[str, Any],
    branch: str,
    findings: list[Finding],
    fixed_files: list[str],
    needs_human_review: bool,
) -> tuple[str | None, int | None]:
    url = f"https://api.github.com/repos/{context['repo']}/pulls"
    payload = {
        "title": f"[GuardianCI Auto-Fix] {findings[0].issue[:80]}",
        "head": branch,
        "base": context["head_ref"],
        "body": auto_fix_pr_body(context, findings, fixed_files, needs_human_review),
        "draft": True,
        "maintainer_can_modify": True,
    }
    response = requests.post(url, headers=github_headers(context), json=payload, timeout=20)
    if response.status_code == 422:
        print("GuardianCI auto-fix PR may already exist or GitHub rejected the draft PR request.")
        return None, None
    response.raise_for_status()
    data = response.json()
    return data.get("html_url"), data.get("number")


def auto_fix_pr_body(
    context: dict[str, Any],
    findings: list[Finding],
    fixed_files: list[str],
    needs_human_review: bool,
) -> str:
    finding_lines = "\n".join(
        f"- `{finding.file}:{finding.line_start}` {finding.issue}" for finding in findings
    )
    file_lines = "\n".join(f"- `{file_path}`" for file_path in fixed_files)
    review_note = (
        "\n\nGuardianCI marked this draft with `needs-human-review` because a quick syntax "
        "check failed."
        if needs_human_review
        else ""
    )
    return (
        f"Prepared by GuardianCI for original PR #{context['pr_number']}.\n\n"
        "CRITICAL finding(s):\n"
        f"{finding_lines}\n\n"
        "Changed file(s):\n"
        f"{file_lines}\n\n"
        "Review this draft carefully before merging it into the original PR branch."
        f"{review_note}"
    )


def post_auto_fix_comment(context: dict[str, Any], result: AutoFixResult) -> None:
    target = result.pr_url or f"`{result.branch}`"
    review_note = (
        "\n\nA quick syntax check failed, so the fix PR was marked `needs-human-review`."
        if result.needs_human_review
        else ""
    )
    post_issue_comment(
        context,
        f"GuardianCI prepared an auto-fix draft: {target}{review_note}",
    )


def post_issue_comment(context: dict[str, Any], body: str) -> None:
    url = f"https://api.github.com/repos/{context['repo']}/issues/{context['pr_number']}/comments"
    response = requests.post(
        url,
        headers=github_headers(context),
        json={"body": body},
        timeout=20,
    )
    response.raise_for_status()


def add_issue_labels(context: dict[str, Any], issue_number: int, labels: list[str]) -> None:
    url = f"https://api.github.com/repos/{context['repo']}/issues/{issue_number}/labels"
    response = requests.post(
        url,
        headers=github_headers(context),
        json={"labels": labels},
        timeout=20,
    )
    if response.status_code == 422:
        print(f"GuardianCI could not apply labels {labels} to PR #{issue_number}.")
        return
    response.raise_for_status()


def request_fix_pr_reviewer(context: dict[str, Any], pr_number: int, reviewer: str) -> None:
    url = f"https://api.github.com/repos/{context['repo']}/pulls/{pr_number}/requested_reviewers"
    response = requests.post(
        url,
        headers=github_headers(context),
        json={"reviewers": [reviewer]},
        timeout=20,
    )
    if response.status_code in {201, 422}:
        if response.status_code == 422:
            print(f"GuardianCI could not request reviewer `{reviewer}` for PR #{pr_number}.")
        return
    response.raise_for_status()


def run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True)


def is_quota_or_rate_limit_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in (
            "429",
            "too many requests",
            "resource_exhausted",
            "quota",
            "rate limit",
            "ratelimit",
        )
    )


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
                    f"Suggested fix: {finding.suggested_fix}\n\n"
                    f"Frameworks: {', '.join(finding.frameworks) if finding.frameworks else 'None mapped'}\n\n"
                    f"Remediation urgency: `{finding.remediation_urgency}`"
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
    payload: dict[str, Any] = {"body": body, "event": event}
    if comments:
        payload["comments"] = comments

    response = requests.post(url, headers=github_headers(context), json=payload, timeout=20)
    if response.status_code == 422 and comments:
        # If GitHub rejects inline positions, keep the review signal as a body-only review.
        print(
            f"GuardianCI: GitHub rejected {len(comments)} inline comment(s) "
            "(lines may be outside the diff context window). Falling back to body-only review."
        )
        payload.pop("comments", None)
        response = requests.post(url, headers=github_headers(context), json=payload, timeout=20)
    response.raise_for_status()


def github_headers(context: dict[str, Any]) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {context['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


if __name__ == "__main__":
    sys.exit(main())
