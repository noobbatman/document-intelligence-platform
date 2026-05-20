from __future__ import annotations


def test_guardian_ci_blocks_failing_prs() -> None:
    """Intentional failure used only to prove branch protection catches red CI."""
    assert False, "GuardianCI Phase 1 red-path proof"
