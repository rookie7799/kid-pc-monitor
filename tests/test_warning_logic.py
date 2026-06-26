"""Unit tests for the pre-lock countdown warning decision logic.

Pure and dependency-free: run with `python tests/test_warning_logic.py` (or
via pytest). No tkinter / Windows needed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from warning_logic import warnings_to_send  # noqa: E402

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


if __name__ == "__main__":
    test_warnings_to_send()
    test_simulated_grants()
    print("\nAll warning_logic tests passed.")
