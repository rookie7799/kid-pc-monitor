"""Unit tests for the pre-lock countdown warning decision logic.

Pure and dependency-free: run with `python tests/test_warning_logic.py` (or
via pytest). No tkinter / Windows needed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from warning_logic import (  # noqa: E402
    warnings_to_send,
    initial_remaining_notice,
    format_remaining_message,
)

INTERVALS = [15, 5, 1]


def check(name, got, want):
    assert got == want, f"{name}: expected {want!r}, got {got!r}"
    print(f"  ok: {name}")


def simulate(grant, intervals=INTERVALS, step=1 / 60.0):
    """Tick remaining time down from ``grant`` to 0, collecting fired warnings.

    Mirrors run_monitor's loop: first observation has no previous value, then
    remaining decreases by one second (1/60 min) each tick.
    """
    sent = set()
    fired = []
    previous = None
    t = grant
    while t >= -1e-9:
        current = max(t, 0.0)
        for w in warnings_to_send(previous, current, intervals, sent):
            sent.add(w)
            fired.append(w)
        previous = current
        t -= step
    return fired


def test_warnings_to_send():
    # No previous reading (first tick after a reset): never fire.
    check("first tick -> nothing", warnings_to_send(None, 10, INTERVALS, set()), [])
    # No active limit (current is None): never fire.
    check("no limit -> nothing", warnings_to_send(10, None, INTERVALS, set()), [])

    # A single downward crossing fires exactly that threshold.
    check("cross 5 -> [5]", warnings_to_send(5.01, 4.99, INTERVALS, set()), [5])
    # Already-sent thresholds are not repeated.
    check("cross 5 already sent -> []",
          warnings_to_send(5.01, 4.99, INTERVALS, {5}), [])
    # No crossing (both above the threshold): nothing.
    check("no crossing -> []", warnings_to_send(20, 16, INTERVALS, set()), [])
    # A big jump down can cross several thresholds at once.
    check("jump past 15 and 5 -> [15, 5]",
          warnings_to_send(20, 3, INTERVALS, set()), [15, 5])


def test_simulated_grants():
    # A full grant announces every threshold in order.
    check("60 min", simulate(60), [15, 5, 1])
    check("16 min", simulate(16), [15, 5, 1])
    # The reported bug: a 10-minute grant must NOT announce "15 minutes".
    check("10 min (no false 15)", simulate(10), [5, 1])
    check("4 min", simulate(4), [1])
    # The 1-minute "save your work" warning still fires near the end.
    assert 1 in simulate(4), "1-minute save warning must fire"
    # Sub-minute grant: nothing was ever crossed from above.
    check("30 sec", simulate(0.5), [])


def test_initial_remaining_notice():
    # Only on the first reading (previous is None).
    check("not first reading -> none",
          initial_remaining_notice(10, 8, INTERVALS), None)
    # No active limit.
    check("no limit -> none", initial_remaining_notice(None, None, INTERVALS), None)
    # Already expired.
    check("expired -> none", initial_remaining_notice(None, 0, INTERVALS), None)
    # Above the largest interval: let the normal crossings handle it.
    check("above top interval -> none",
          initial_remaining_notice(None, 20, INTERVALS), None)
    # Logging in below the top interval: announce the actual time left now.
    check("login at 12 -> 12", initial_remaining_notice(None, 12, INTERVALS), 12)
    check("login at exactly 15 -> 15",
          initial_remaining_notice(None, 15, INTERVALS), 15)
    check("login under a minute -> 0.5",
          initial_remaining_notice(None, 0.5, INTERVALS), 0.5)


def test_format_remaining_message():
    check("12 min", format_remaining_message(12), "⚠️ You have 12 minutes left!")
    check("1 min singular", format_remaining_message(1), "⚠️ You have 1 minute left!")
    # Floors rather than over-stating.
    check("7.9 min floors to 7",
          format_remaining_message(7.9), "⚠️ You have 7 minutes left!")
    # Under a minute switches to a seconds-based save prompt.
    check("30 sec", format_remaining_message(0.5),
          "⚠️ Attention: 30 seconds left, save!")
    check("never zero seconds", format_remaining_message(0.005),
          "⚠️ Attention: 1 seconds left, save!")


if __name__ == "__main__":
    test_warnings_to_send()
    test_simulated_grants()
    test_initial_remaining_notice()
    test_format_remaining_message()
    print("\nAll warning_logic tests passed.")
