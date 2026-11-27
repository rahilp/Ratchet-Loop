#!/usr/bin/env python3
"""
Lane U guards: measured UI gate (geometry then pixel), modes, baselines.
    python3 tests/test_ui_measure.py
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import traceback
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UIC = ROOT / "skills" / "ratchet-ui-compare" / "scripts"

sys.path.insert(0, str(HERE))
import _tmp  # noqa: E402

if str(UIC) not in sys.path:
    sys.path.insert(0, str(UIC))

PASS = FAIL = 0
CASE_STATUS: dict = {}
CASE_MESSAGES: dict = {}
ID_RE = re.compile(r"\[(U-T\d+)\]")


def check(name, cond, detail=""):
    global PASS, FAIL
    cond = bool(cond)
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    m = ID_RE.search(name)
    if m:
        cid = m.group(1)
        CASE_STATUS[cid] = CASE_STATUS.get(cid, True) and cond
        if not cond:
            CASE_MESSAGES.setdefault(cid, []).append(f"{name}: {str(detail)[:200]}")
    print(f"  {'✓' if cond else '✗'} {name}" + ("" if cond else f"   {str(detail)[:400]}"))


def load_ui_measure():
    import ui_measure as um  # type: ignore

    return um


# ------------------------------------------------------------------------------------------------ PNG fixtures

def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def png_rgb(w: int, h: int, pixels: list[tuple[int, int, int]]) -> bytes:
    """Minimal 8-bit RGB PNG (no alpha). pixels row-major, length w*h."""
    assert len(pixels) == w * h
    raw = b"".join(b"\x00" + bytes(p) for p in pixels)
    comp = zlib.compress(raw, 9)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", comp) + _chunk(b"IEND", b"")


PNG_2X2_GRAY = png_rgb(2, 2, [(128, 128, 128)] * 4)


def png_8x8_fill(fill: tuple[int, int, int], *, at: tuple[int, int, tuple[int, int, int]] | None = None) -> bytes:
    w, h = 8, 8
    row = [fill] * w
    grid = [list(row) for _ in range(h)]
    if at is not None:
        x, y, color = at
        grid[y][x] = list(color)
    flat = [tuple(p) for r in grid for p in r]
    return png_rgb(w, h, flat)


def _write_png(path: Path, data: bytes) -> str:
    path.write_bytes(data)
    return str(path)


def _state_capture(
    shots: Path,
    name: str,
    *,
    ref_png: bytes,
    app_png: bytes,
    ref_geom: list[dict] | None,
    app_geom: list[dict] | None,
) -> dict:
    ref_path = _write_png(shots / f"{name}-ref.png", ref_png)
    app_path = _write_png(shots / f"{name}-app.png", app_png)
    ref = {"screenshot": ref_path}
    app = {"screenshot": app_path}
    if ref_geom is not None:
        ref["geometry"] = ref_geom
    if app_geom is not None:
        app["geometry"] = app_geom
    return {"name": name, "reference": ref, "app": app}


def _capture(states: list[dict]) -> dict:
    return {"checkpoint": "CP1", "states": states}


# ------------------------------------------------------------------------------------------------ cases

def u01_geometry_mismatch_fails():
    print("U-T01: geometry mismatch beyond tolerance → fail")
    um = load_ui_measure()
    shots = Path(_tmp.mkdtemp(prefix="ratchet-um-t01-"))
    box_a = [{"id": "main", "x": 0, "y": 0, "w": 40, "h": 20}]
    box_b = [{"id": "main", "x": 10, "y": 0, "w": 40, "h": 20}]
    st = _state_capture(shots, "home", ref_png=PNG_2X2_GRAY, app_png=PNG_2X2_GRAY,
                        ref_geom=box_a, app_geom=box_b)
    out = um.measure_capture(_capture([st]), geometry_tolerance_px=2)
    check("[U-T01] overall verdict fail", out.get("verdict") == "fail", out.get("verdict"))
    states = out.get("states") or []
    check("[U-T01] state verdict fail", states and states[0].get("verdict") == "fail", states)


def u02_identical_geometry_and_pixels_pass():
    print("U-T02: identical geometry + identical PNG bytes → pass")
    um = load_ui_measure()
    shots = Path(_tmp.mkdtemp(prefix="ratchet-um-t02-"))
    boxes = [{"id": "btn", "x": 4, "y": 4, "w": 12, "h": 8}]
    st = _state_capture(shots, "list", ref_png=PNG_2X2_GRAY, app_png=PNG_2X2_GRAY,
                        ref_geom=boxes, app_geom=[dict(boxes[0])])
    out = um.measure_capture(_capture([st]))
    check("[U-T02] overall pass", out.get("verdict") == "pass", out)
    check("[U-T02] state pass", (out.get("states") or [{}])[0].get("verdict") == "pass", out)


def u03_baseline_masks_pixel_diff():
    print("U-T03: pixel diff in baseline region → pass; same diff without baseline → fail")
    um = load_ui_measure()
    shots = Path(_tmp.mkdtemp(prefix="ratchet-um-t03-"))
    boxes = [{"id": "panel", "x": 0, "y": 0, "w": 8, "h": 8}]
    ref = png_8x8_fill((200, 200, 200))
    app = png_8x8_fill((200, 200, 200), at=(4, 4, (0, 0, 0)))
    region = {"x": 3, "y": 3, "w": 2, "h": 2}
    base = [{"checkpoint": "CP1", "state": "checkout", "region": region, "reason": "clock widget"}]
    st = _state_capture(shots, "checkout", ref_png=ref, app_png=app, ref_geom=boxes, app_geom=boxes)
    cap = _capture([st])
    with_base = um.measure_capture(cap, baselines=base, pixel_threshold=0.02)
    without = um.measure_capture(cap, baselines=None, pixel_threshold=0.02)
    check("[U-T03] with baseline region → pass", with_base.get("verdict") == "pass", with_base)
    check("[U-T03] without baseline → fail", without.get("verdict") == "fail", without)


def u04_missing_geometry_inconclusive():
    print("U-T04: missing geometry on both sides → inconclusive (not silent pass)")
    um = load_ui_measure()
    shots = Path(_tmp.mkdtemp(prefix="ratchet-um-t04-"))
    st = _state_capture(shots, "blank", ref_png=PNG_2X2_GRAY, app_png=PNG_2X2_GRAY,
                        ref_geom=None, app_geom=None)
    out = um.measure_capture(_capture([st]))
    check("[U-T04] not pass", out.get("verdict") != "pass", out)
    check("[U-T04] inconclusive", out.get("verdict") == "inconclusive", out)


def u05_mode_orchestration_helpers():
    print("U-T05: mode helpers — measure required, visual skip, gate decision")
    um = load_ui_measure()
    check("[U-T05] off → measure not required",
          um.should_run_measure("off") is False,
          getattr(um, "should_run_measure", None))
    check("[U-T05] enforce → measure required",
          um.should_run_measure("enforce") is True,
          getattr(um, "should_run_measure", None))
    check("[U-T05] report → measure required",
          um.should_run_measure("report") is True,
          getattr(um, "should_run_measure", None))
    check("[U-T05] enforce + pass → skip visual",
          um.should_run_visual("enforce", "pass") is False,
          um.should_run_visual("enforce", "pass"))
    check("[U-T05] enforce + fail → skip visual",
          um.should_run_visual("enforce", "fail") is False,
          getattr(um, "should_run_visual", None))
    check("[U-T05] enforce + inconclusive → run visual",
          um.should_run_visual("enforce", "inconclusive") is True,
          getattr(um, "should_run_visual", None))
    decide = um.ui_gate_decision
    check("[U-T05] report: measure fail, visual PASS → gate PASS (visual rules)",
          decide("report", "fail", "PASS", True) == "PASS",
          decide("report", "fail", "PASS", True))
    check("[U-T05] enforce: measure fail, visual PASS → gate FAIL",
          decide("enforce", "fail", "PASS", True) == "FAIL",
          decide("enforce", "fail", "PASS", True))
    check("[U-T05] enforce: measure pass, no visual JSON, behavior ok → PASS",
          decide("enforce", "pass", None, True) == "PASS",
          decide("enforce", "pass", None, True))
    check("[U-T05] enforce: measure pass but behavior not ok → FAIL",
          decide("enforce", "pass", None, False) == "FAIL",
          decide("enforce", "pass", None, False))


def u06_accept_baseline_scoped():
    print("U-T06: wrong-state baseline refused; matching accept → later measure pass")
    um = load_ui_measure()
    shots = Path(_tmp.mkdtemp(prefix="ratchet-um-t06-"))
    root = Path(_tmp.mkdtemp(prefix="ratchet-um-proj-"))
    (root / ".ratchet").mkdir(parents=True, exist_ok=True)
    region = {"x": 3, "y": 3, "w": 2, "h": 2}
    boxes = [{"id": "panel", "x": 0, "y": 0, "w": 8, "h": 8}]
    ref = png_8x8_fill((180, 180, 180))
    app = png_8x8_fill((180, 180, 180), at=(4, 4, (255, 0, 0)))
    st = _state_capture(shots, "cart", ref_png=ref, app_png=app, ref_geom=boxes, app_geom=boxes)
    measure_before = um.measure_capture(_capture([st]), pixel_threshold=0.02)
    check("[U-T06] diff present before accept", measure_before.get("verdict") == "fail", measure_before)
    wrong = um.accept_baseline(
        root, checkpoint="CP1", state="checkout", region=region, reason="wrong state", accepted_by="director",
    )
    check("[U-T06] wrong state refused",
          wrong.get("ok") is False or wrong.get("error"),
          wrong)
    good = um.accept_baseline(
        root, checkpoint="CP1", state="cart", region=region, reason="promo badge", accepted_by="director",
    )
    check("[U-T06] matching state accepted",
          good.get("ok") is True or good.get("id"),
          good)
    stored = um.load_baselines(root)
    check("[U-T06] baseline persisted", any(b.get("state") == "cart" for b in (stored or [])), stored)
    after = um.measure_capture(_capture([st]), baselines=stored, pixel_threshold=0.02)
    check("[U-T06] after accept → measure pass", after.get("verdict") == "pass", after)


TESTS = [
    ("U-T01", u01_geometry_mismatch_fails),
    ("U-T02", u02_identical_geometry_and_pixels_pass),
    ("U-T03", u03_baseline_masks_pixel_diff),
    ("U-T04", u04_missing_geometry_inconclusive),
    ("U-T05", u05_mode_orchestration_helpers),
    ("U-T06", u06_accept_baseline_scoped),
]


def write_report():
    path = os.environ.get("RATCHET_TEST_REPORT")
    if not path:
        return
    cases = []
    for cid, _fn in TESTS:
        ok = CASE_STATUS.get(cid, True)
        cases.append({
            "id": cid,
            "status": "passed" if ok else "failed",
            "message": "; ".join(CASE_MESSAGES.get(cid, [])),
        })
    Path(path).write_text(json.dumps({"cases": cases}, indent=2) + "\n")


def main():
    for cid, fn in TESTS:
        try:
            fn()
        except Exception:
            check(f"[{cid}] case raised", False, traceback.format_exc()[-500:])
    print(f"\n{PASS} passed, {FAIL} failed")
    write_report()
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())