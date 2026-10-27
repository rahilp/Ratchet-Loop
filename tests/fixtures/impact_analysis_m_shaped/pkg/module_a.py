"""CP7 fixture (M-shaped): the pre-extraction home of the helper that is about to move out to a
new sibling module. Shaped after lane M's own CP3-CP5 extraction (the real-world incidents B1,
CP5-X78 and lane M's CP6 that motivated this whole feature). The planned checkpoint moves the
function below to the sibling module; at the point this fixture is scanned, that sibling module does
not exist on disk yet -- this file is the "before" state, exactly as a builder would find it before
writing any code for the move."""


def _load_state(path):
    """The helper about to move out of this module. Deliberately leading-underscore (private-looking)
    like the real _write_record/_load_state-shaped helpers lane M has moved before."""
    return {"path": str(path), "loaded": True}


def other_existing_helper():
    """An unrelated helper that stays in this module; present so the fixture isn't a single-function
    file, matching a real module's shape."""
    return "unrelated"
