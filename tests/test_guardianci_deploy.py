from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "guardianci_deploy.py"
SPEC = importlib.util.spec_from_file_location("guardianci_deploy", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
deploy = importlib.util.module_from_spec(SPEC)
sys.modules["guardianci_deploy"] = deploy
SPEC.loader.exec_module(deploy)


def test_parse_coverage_reads_cobertura_line_rate(tmp_path: Path) -> None:
    coverage = tmp_path / "coverage.xml"
    coverage.write_text('<coverage line-rate="0.82304"></coverage>', encoding="utf-8")

    assert deploy.parse_coverage(coverage) == 82.3


def test_deployment_record_includes_security_and_coverage() -> None:
    record = deploy.deployment_record(
        "noobbatman/document-intelligence-platform",
        "abcdef123456",
        "ghcr.io/noobbatman/document-intelligence-platform:abcdef123456",
        82.3,
        {"total_critical": 0, "total_warn": 2, "score": 90, "status": "completed"},
    )

    assert record["short_sha"] == "abcdef1"
    assert record["coverage_percent"] == 82.3
    assert record["security"] == {
        "total_critical": 0,
        "total_warn": 2,
        "score": 90,
        "status": "completed",
    }


def test_slack_message_contains_release_summary() -> None:
    record = deploy.deployment_record(
        "repo/name",
        "abc1234",
        "ghcr.io/repo/name:abc1234",
        87.5,
        {"total_critical": 0, "total_warn": 1, "score": 95},
    )

    message = deploy.slack_message(record, success=True)

    assert "Deployed" in message
    assert "abc1234" in message
    assert "Coverage: 87.50%" in message
    assert "0 critical | 1 warnings | score 95/100" in message


def test_attempt_rollback_posts_previous_image(monkeypatch) -> None:
    calls = []

    def fake_post_json(url: str, payload: dict) -> None:
        calls.append((url, payload))

    monkeypatch.setattr(deploy, "post_json", fake_post_json)
    failed = deploy.deployment_record("repo/name", "newsha", "new-image", None, {})

    message = deploy.attempt_rollback(
        "https://railway.example/rollback",
        {"image": "previous-image"},
        failed,
    )

    assert "previous-image" in message
    assert calls == [
        (
            "https://railway.example/rollback",
            {
                "image": "previous-image",
                "failed_image": "new-image",
                "source": "GuardianCI rollback",
            },
        )
    ]


def test_notify_slack_is_non_blocking(monkeypatch) -> None:
    def fake_post_json(_url: str, _payload: dict) -> None:
        raise RuntimeError("slack is down")

    monkeypatch.setattr(deploy, "post_json", fake_post_json)

    deploy.notify_slack("https://slack.example/webhook", "hello")


def test_wait_for_health_startup_delay_is_skipped_when_zero(monkeypatch) -> None:
    # Use URLError so wait_for_health's except clause catches it normally
    # (RuntimeError bypasses the handler, giving false coverage of the retry path).
    # Record events as (type, value) tuples to verify both order and sleep duration.
    events: list[tuple[str, object]] = []

    def fake_urlopen(_url, timeout=None):
        events.append(("urlopen", timeout))
        raise deploy.urllib.error.URLError("connection refused")

    def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))
        raise RuntimeError("stop after first sleep")

    monkeypatch.setattr(deploy.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(deploy.time, "sleep", fake_sleep)

    try:
        deploy.wait_for_health(
            "http://example.com", timeout_seconds=10, interval=1, startup_delay=0
        )
    except RuntimeError as exc:
        assert "stop after first sleep" in str(exc)

    assert [event[0] for event in events[:2]] == ["urlopen", "sleep"], (
        f"startup_delay=0 must not sleep before the first poll; events: {events}"
    )
    assert events[1] == ("sleep", 1)


def test_parse_coverage_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert deploy.parse_coverage(tmp_path / "nonexistent.xml") is None


def test_parse_coverage_returns_none_for_malformed_xml(tmp_path: Path) -> None:
    coverage = tmp_path / "coverage.xml"
    coverage.write_text("<coverage line-rate=", encoding="utf-8")

    assert deploy.parse_coverage(coverage) is None
