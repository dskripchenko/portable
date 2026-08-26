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
    #: A command is going. The mouth carries the same frames as the busy bar.
    "working": (" o o ", "  >_ "),
    #: A command finished and changed something.
    "done": (" ^ ^ ", " \\_/ "),
    #: Waiting on something that has not answered yet.
    "waiting": (" o o ", "  .. "),
    #: A process is down, or the last thing said no.
    "error": (" x x ", "  >! "),
    #: The daemon is not running. Not an error — nobody asked it to be.
    "stopped": (" - - ", "  zZ "),
    "surprised": (" O O ", "  >o "),
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


def state_for(*, running: bool, failing: bool, busy: bool) -> str:
    """
    Which face answers the screen's own question: is anything wrong.

    Ordered by what somebody needs to know first. A supervisor that is down
    explains everything else, so it wins over a failure; a failure outranks work
    in progress, because work in progress is the ordinary case and the one the
    busy bar is already reporting.
    """
    if not running:
        return "stopped"

    if failing:
        return "error"

    if busy:
        return "working"

    return "ready"
