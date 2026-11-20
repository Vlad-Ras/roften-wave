"""Debounced monitor behavior must never alarm on normal DJ release."""
from roften_wave.air_monitor import AirAlertState


def test_alert_after_three_misses_then_restoration():
    monitor = AirAlertState()
    assert monitor.evaluate(expected=True, healthy=False) is None
    assert monitor.evaluate(expected=True, healthy=False) is None
    assert monitor.evaluate(expected=True, healthy=False) == "lost"
    assert monitor.evaluate(expected=True, healthy=False) is None
    assert monitor.evaluate(expected=True, healthy=True) == "restored"
    assert monitor.evaluate(expected=True, healthy=True) is None
    assert monitor.evaluate(expected=True, healthy=False) is None


def test_pause_clears_outage_state_without_recovery_alert():
    monitor = AirAlertState(threshold=1)
    assert monitor.evaluate(expected=True, healthy=False) == "lost"
    assert monitor.evaluate(expected=False, healthy=False) is None
    assert monitor.evaluate(expected=True, healthy=True) is None


def test_invalid_threshold():
    import pytest
    with pytest.raises(ValueError):
        AirAlertState(threshold=0)
