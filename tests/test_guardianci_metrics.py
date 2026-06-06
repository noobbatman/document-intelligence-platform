from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "guardianci_metrics.py"
SPEC = importlib.util.spec_from_file_location("guardianci_metrics", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
metrics = importlib.util.module_from_spec(SPEC)
sys.modules["guardianci_metrics"] = metrics
SPEC.loader.exec_module(metrics)


def test_build_summary_aggregates_scores_findings_and_frameworks() -> None:
    reviews = [
        {
            "timestamp": "2026-06-06T10:00:00Z",
            "pr_number": 1,
            "sha": "abc",
            "score": 75,
            "total_critical": 1,
            "total_warn": 1,
            "findings": [
                {
                    "file": "app/api.py",
                    "severity": "CRITICAL",
                    "frameworks": ["SOC 2 CC6.1"],
                },
                {
                    "file": "app/api.py",
                    "severity": "WARN",
                    "frameworks": ["GDPR Art. 32"],
                },
            ],
        },
        {
            "timestamp": "2026-06-06T11:00:00Z",
            "pr_number": 2,
            "sha": "def",
            "score": 100,
            "total_critical": 0,
            "total_warn": 0,
            "findings": [],
        },
    ]

    summary = metrics.build_summary(reviews)

    assert summary["rolling_30_day_score"] == 87.5
    assert summary["total_prs_reviewed"] == 2
    assert summary["total_findings"] == 2
    assert summary["severity_breakdown"] == {"CRITICAL": 1, "WARN": 1}
    assert summary["top_vulnerable_modules"][0] == {"file": "app/api.py", "findings": 2}
    assert summary["top_frameworks"][0] == {"framework": "SOC 2 CC6.1", "findings": 1}


def test_render_badge_uses_score_color() -> None:
    badge = metrics.render_badge({"rolling_30_day_score": 68})

    assert "GuardianCI-68%2F100-red" in badge


def test_render_badge_color_thresholds() -> None:
    assert "brightgreen" in metrics.render_badge({"rolling_30_day_score": 90})
    assert "yellow" in metrics.render_badge({"rolling_30_day_score": 89})
    assert "yellow" in metrics.render_badge({"rolling_30_day_score": 70})
    assert "red" in metrics.render_badge({"rolling_30_day_score": 69})


def test_render_dashboard_contains_summary_values() -> None:
    html = metrics.render_dashboard(
        {
            "generated_at": "2026-06-06T10:00:00Z",
            "rolling_30_day_score": 92,
            "total_prs_reviewed": 3,
            "total_findings": 1,
            "severity_breakdown": {"WARN": 1},
            "top_vulnerable_modules": [{"file": "app/api.py", "findings": 1}],
        }
    )

    assert "GuardianCI Security Dashboard" in html
    assert "92/100" in html
    assert "app/api.py" in html


def test_render_dashboard_escapes_ai_controlled_values() -> None:
    html = metrics.render_dashboard(
        {
            "generated_at": "<script>alert(1)</script>",
            "rolling_30_day_score": 92,
            "total_prs_reviewed": 3,
            "total_findings": 1,
            "severity_breakdown": {"<img src=x onerror=alert(1)>": 1},
            "top_vulnerable_modules": [
                {"file": "<script>alert(1)</script>", "findings": "<b>1</b>"}
            ],
        }
    )

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;1&lt;/b&gt;" in html


def test_safe_slug_normalizes_file_name_parts() -> None:
    assert metrics.safe_slug("2026-06-06T10:00:00Z") == "2026-06-06t10-00-00z"
