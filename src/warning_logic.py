"""Pure decision logic for the pre-lock countdown warnings.

Dependency-free (no tkinter / Windows): given the previous and current
remaining minutes, decide which warning thresholds to announce this tick.
Kept separate from PCTimeControl so it can be unit-tested in isolation.
"""


def warnings_to_send(previous, current, intervals, already_sent):
    """Return the warning thresholds (minutes) to fire this tick, in order.

    A threshold fires only when the remaining time *crosses down* through it
    -- the previous tick was strictly above it and the current tick is at or
    below it -- and it has not already been sent. This avoids over-stating the
    time: a threshold larger than the time the kid ever had is never crossed,
    so a 10-minute grant never announces "15 minutes left".

    ``previous`` is None on the first observation after a (re)set, in which
    case nothing fires (there is no point to have crossed from yet).
    """
    if previous is None or current is None:
        return []
    return [w for w in intervals
            if previous > w >= current and w not in already_sent]


def initial_remaining_notice(previous, current, intervals):
    """Return the minutes to announce as an immediate one-off notice, or None.

    On the first reading after a (re)set (previous is None) a kid who logs in
    late would otherwise hear nothing until the time crosses the next interval
    (e.g. log in with 12 minutes left and stay silent until 5). When the
    remaining time is already at or below the largest interval, announce the
    actual time left right away. Returns None when a notice isn't warranted --
    no active limit, already expired, or still above the largest interval
    (where the normal crossing warnings will cover it).
    """
    if previous is not None or current is None:
        return None
    if current <= 0 or current > max(intervals):
        return None
    return current


def format_remaining_message(remaining_minutes):
    """Human-readable countdown text for a given remaining time in minutes.

    Under a minute is rendered in seconds with a save prompt so the kid knows
    to act fast; otherwise whole minutes (floored, never over-stating).
    """
    if remaining_minutes < 1:
        seconds = max(1, int(round(remaining_minutes * 60)))
        return f"⚠️ Attention: {seconds} seconds left, save!"
    minutes = int(remaining_minutes)
    unit = "minute" if minutes == 1 else "minutes"
    return f"⚠️ You have {minutes} {unit} left!"
