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
