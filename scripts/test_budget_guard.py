"""Smoke tests for budget_guard.py.

Run: python scripts/test_budget_guard.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock


def _import_with_isolated_state():
    """Import budget_guard with state files redirected to a temp dir."""
    sys.path.insert(0, str(Path(__file__).parent))
    import budget_guard
    tmp = tempfile.mkdtemp(prefix="budget-guard-test-")
    budget_guard.DISABLED_FLAG = Path(tmp) / ".compiler-disabled.flag"
    budget_guard.COMBINED_BUDGET_FILE = Path(tmp) / "combined-budget.json"
    budget_guard.SILENT_ZERO_COUNTER = Path(tmp) / ".silent-zero-counter.json"
    return budget_guard, tmp


def test_disable_enable_roundtrip():
    bg, _ = _import_with_isolated_state()
    assert bg.is_disabled() == (False, "")
    bg.disable("test reason")
    disabled, reason = bg.is_disabled()
    assert disabled is True
    assert "test reason" in reason
    assert bg.enable() is True
    assert bg.is_disabled() == (False, "")
    assert bg.enable() is False  # already removed
    print("PASS test_disable_enable_roundtrip")


def test_monthly_cap_detection():
    bg, _ = _import_with_isolated_state()
    remaining, soft, hard = bg.check_remaining()
    assert remaining == bg.MONTHLY_HARD_CAP_USD
    assert soft is False and hard is False
    bg.record_run("test", 3.10, ["a.md"])
    remaining, soft, hard = bg.check_remaining()
    assert soft is True and hard is False
    bg.record_run("test", 1.00, ["b.md"])
    remaining, soft, hard = bg.check_remaining()
    assert hard is True
    print("PASS test_monthly_cap_detection")


def test_silent_zero_counter():
    bg, _ = _import_with_isolated_state()
    assert bg.silent_zero_count() == 0
    assert bg.record_silent_zero() == 1
    assert bg.record_silent_zero() == 2
    bg.reset_silent_zero()
    assert bg.silent_zero_count() == 0
    print("PASS test_silent_zero_counter")


def test_telegram_alert_no_creds_returns_false():
    bg, tmp = _import_with_isolated_state()
    bg.ROOT_DIR = Path(tmp)  # .env won't exist
    assert bg.telegram_alert("test") is False
    print("PASS test_telegram_alert_no_creds_returns_false")


def test_disabled_flag_survives_module_reload():
    bg, tmp = _import_with_isolated_state()
    bg.disable("persistence test")
    # Re-read the flag from disk to confirm it actually persisted
    assert (Path(tmp) / ".compiler-disabled.flag").exists()
    assert "persistence test" in (Path(tmp) / ".compiler-disabled.flag").read_text()
    print("PASS test_disabled_flag_survives_module_reload")


if __name__ == "__main__":
    test_disable_enable_roundtrip()
    test_monthly_cap_detection()
    test_silent_zero_counter()
    test_telegram_alert_no_creds_returns_false()
    test_disabled_flag_survives_module_reload()
    print("\nAll tests passed.")
