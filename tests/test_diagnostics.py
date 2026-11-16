from __future__ import annotations

import stat

import pytest

from scanner.settings.diagnostics import (
    DiagnosticsError,
    DiagnosticsPreferences,
    debug_logging_enabled,
    load_diagnostics_preferences,
    read_log_tail,
    save_diagnostics_preferences,
)


def test_diagnostics_preferences_are_private_and_survive_round_trip(tmp_path):
    save_diagnostics_preferences(tmp_path, DiagnosticsPreferences(debug_enabled=True))

    preferences_path = tmp_path / "diagnostics.json"
    assert load_diagnostics_preferences(tmp_path) == DiagnosticsPreferences(debug_enabled=True)
    assert stat.S_IMODE(preferences_path.stat().st_mode) & 0o077 == 0


def test_invalid_diagnostics_preference_falls_back_without_blocking_startup(tmp_path):
    (tmp_path / "diagnostics.json").write_text("not json", encoding="utf-8")

    with pytest.raises(DiagnosticsError, match="invalid"):
        load_diagnostics_preferences(tmp_path)
    assert debug_logging_enabled(tmp_path) is False


def test_log_tail_only_reads_allowlisted_redacted_bounded_records(tmp_path):
    log_path = tmp_path / "worker.log"
    log_path.write_text(
        "first line\napi_key=not-for-users\nlast line\n",
        encoding="utf-8",
    )

    lines, truncated = read_log_tail(tmp_path, "worker", limit=2)

    assert lines == ["api_key=[REDACTED]", "last line"]
    assert truncated is True
    with pytest.raises(DiagnosticsError, match="Unknown"):
        read_log_tail(tmp_path, "auth-audit", limit=2)
    with pytest.raises(DiagnosticsError, match="between"):
        read_log_tail(tmp_path, "worker", limit=0)


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ('{"api_key": "k-1", "n": 2}', '{"api_key": [REDACTED], "n": 2}'),
        ('{"password":"a,b;c","user":"x"}', '{"password":[REDACTED],"user":"x"}'),
        ("{'session_token': 'k-2'}", "{'session_token': [REDACTED]}"),
        ('{"Authorization": "Bearer k-3"}', '{"Authorization": [REDACTED]}'),
        ('{"api_key": "esc\\"aped k-4"}', '{"api_key": [REDACTED]}'),
        ("api_key=k-5, next", "api_key=[REDACTED], next"),
    ],
)
def test_log_tail_redacts_quoted_structured_credentials(tmp_path, record, expected):
    (tmp_path / "application.log").write_text(record + "\n", encoding="utf-8")

    lines, _ = read_log_tail(tmp_path, "application", limit=1)

    assert lines == [expected]
