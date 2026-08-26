"""
The face in the corner, and the figure the logo is drawn from.

One shape in two places. In a terminal it is seven characters by four rows; in
`docs/mark.svg` it is the same figure drawn with real lines, because the ears
are diagonals and a diagonal transcribed into a grid of blocks stops being one.
Kept here so that the terminal half has a single source, and so that changing a
face means changing one table rather than hunting through a layout.

**ASCII only, and that is a constraint rather than a preference.** The busy
indicator is built from `|/-\\` for the same reason: a terminal without the font
for box-drawing or braille shows squares, and a mascot made of squares makes the
whole screen look broken at the moment it is meant to be reassuring. Nothing
here is above codepoint 127, and a test keeps it that way.

The frame never changes. Only the two inner lines do — which is what makes this
cheap to redraw, and what stopped the ears from becoming a second thing to keep
in step with the first.
"""

from __future__ import annotations

#: The top, with the ears folded down, and the bottom.
TOP = "|\\---/|"
BOTTOM = "'-----'"

#: The inside of each face: what the eyes say, and what the mouth says.
#:
#: Exactly five characters each, because the frame is seven wide and takes one
#: at either end. A test measures every one of them — a face a character too
#: wide bends the box, and in a corner four rows tall that reads as a rendering
#: fault rather than as a typo.
FACES: dict[str, tuple[str, str]] = {
    #: Up, with nothing running. The prompt, which is what it is.
    "ready": (" o o ", "  >_ "),
    #: Ready, mid-blink. A still picture cannot tell "watching" from "wedged" —
    #: the same reason the busy indicator moves — so the idle face blinks now
    #: and then rather than staring.
    "blink": (" - - ", "  >_ "),
    #: A command is going. The mouth carries the same frames as the busy bar.
    "working": (" o o ", "  >_ "),
    #: A command finished and changed something.
    "done": (" ^ ^ ", " \\_/ "),
    #: A command is going and nothing is coming back. Most of an install is
    #: spent waiting on somebody else's host, and a spinner alone cannot tell
    #: that from work — which is exactly the moment a thirty-second connect
    #: timeout looks like a hang.
    "waiting": (" o o ", "  .. "),
    #: A process is down, or the last thing said no.
    "error": (" x x ", "  >! "),
    #: The daemon is not running. Not an error — nobody asked it to be.
    "stopped": (" - - ", "  zZ "),
    #: It was running a moment ago and nobody asked it to stop.
    "surprised": (" O O ", "  >o "),
    #: Not used by the dashboard. `portable version` prints it, where there is
    #: room and nothing to report.
    "wink": (" o - ", "  >_ "),
}

#: What the mouth cycles through while something is running.
#:
#: The same four characters the busy bar spins, and deliberately so: two
#: different spinners on one screen at the same speed read as two things
#: happening.
SPIN = "-\\|/"

WIDTH = len(TOP)
HEIGHT = 4


def face(state: str = "ready", frame: int = 0) -> list[str]:
    """
    The four rows, ready to print.

    An unknown state is `ready` rather than an exception: this is decoration on
    a screen whose job is to report other things, and a mascot that can take the
    dashboard down with it would be a poor trade.
    """
    eyes, mouth = FACES.get(state, FACES["ready"])

    if state == "working":
        mouth = f"  >{SPIN[frame % len(SPIN)]} "

    return [TOP, f"|{eyes}|", f"|{mouth}|", BOTTOM]


def rendered(state: str = "ready", frame: int = 0) -> str:
    return "\n".join(face(state, frame))


#: How long a face that reports an event stays up before the screen moves on.
#:
#: Long enough to catch the eye of somebody looking elsewhere when it happened,
#: short enough that it is not still claiming something that has stopped being
#: true. Applies to `done` and to `surprised`.
LINGER = 3.0

#: How long a command may say nothing before it counts as waiting rather than
#: working. Below this, ordinary gaps between lines of output would flicker the
#: face; above it, a stalled download looks busy for longer than it should.
SILENCE = 5.0

#: Seconds between blinks, and how long one lasts.
BLINK_EVERY = 25.0
BLINK_FOR = 0.4


def state_for(
    *,
    running: bool,
    failing: bool,
    busy: bool,
    silent_for: float = 0.0,
    finished_ago: float | None = None,
    vanished_ago: float | None = None,
    idle_for: float = 0.0,
) -> str:
    """
    Which face the corner should be wearing, from what is observably true.

    Ordered by what somebody needs to know first, and the order is the whole of
    the design. A supervisor that went away by itself outranks one that is
    merely down, because the first is news and the second is a state. Work in
    progress outranks a finished command, because the finished one is already
    history. A failure outlasts both: it stays until something goes right,
    since a face that returns to cheerful on its own would be reporting the
    passage of time rather than the state of anything.

    Everything here is a duration rather than a flag. The caller measures, this
    decides — which keeps the timing rules in one readable place instead of
    spread through a screen's event handlers.
    """
    if vanished_ago is not None and vanished_ago < LINGER:
        return "surprised"

    if not running:
        return "stopped"

    if busy:
        return "waiting" if silent_for >= SILENCE else "working"

    if failing:
        return "error"

    if finished_ago is not None and finished_ago < LINGER:
        return "done"

    # A blink, so that a screen with nothing to report still looks like one
    # that is watching. The same argument the busy indicator makes: a still
    # picture cannot tell attention from a wedged process.
    # After a full interval, not at zero: the first thing the corner does on
    # opening should be to look at you, not to blink.
    if idle_for > BLINK_EVERY and idle_for % BLINK_EVERY < BLINK_FOR:
        return "blink"

    return "ready"
