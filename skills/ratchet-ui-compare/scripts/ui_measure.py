"""Deterministic UI measure: geometry then pixel. No LLM. No project instructions."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

MEASURE_VERSION = 1


def measure_capture(
    capture: dict,
    *,
    baselines=None,
    pixel_threshold=0.02,
    geometry_tolerance_px=2,
) -> dict:
    """Return measure JSON. Never raises for content mismatch — uses verdicts.

    Tool/IO failure sets error=str and verdict='inconclusive'.
    """
    states_out: list[dict] = []
    tool_error: str | None = None
    checkpoint = capture.get("checkpoint")

    for st in capture.get("states") or []:
        try:
            states_out.append(
                _measure_state(
                    st,
                    checkpoint=checkpoint,
                    baselines=baselines,
                    pixel_threshold=pixel_threshold,
                    geometry_tolerance_px=geometry_tolerance_px,
                )
            )
        except _MeasureIOError as exc:
            tool_error = str(exc)
            states_out.append(
                {
                    "name": st.get("name", ""),
                    "verdict": "inconclusive",
                    "geometry": {"compared": False, "mismatches": []},
                    "pixel": {
                        "compared": False,
                        "mismatch_ratio": None,
                        "threshold": pixel_threshold,
                    },
                    "diffs": [],
                }
            )

    verdict = _overall_verdict(states_out)
    if tool_error and verdict != "fail":
        verdict = "inconclusive"

    return {
        "version": MEASURE_VERSION,
        "verdict": verdict,
        "states": states_out,
        "error": tool_error,
    }


class _MeasureIOError(Exception):
    pass


def _overall_verdict(states: list[dict]) -> str:
    if any(s.get("verdict") == "fail" for s in states):
        return "fail"
    if any(s.get("verdict") == "inconclusive" for s in states):
        return "inconclusive"
    return "pass"


def _measure_state(
    st: dict,
    *,
    checkpoint,
    baselines,
    pixel_threshold: float,
    geometry_tolerance_px: int,
) -> dict:
    name = st.get("name", "")
    ref = st.get("reference") or {}
    app = st.get("app") or {}
    ref_geom = ref.get("geometry")
    app_geom = app.get("geometry")

    geom_compared = ref_geom is not None and app_geom is not None
    geom_block: dict = {"compared": geom_compared, "mismatches": []}
    pixel_block = {
        "compared": False,
        "mismatch_ratio": None,
        "threshold": pixel_threshold,
    }
    diffs: list[dict] = []

    if not geom_compared:
        geom_block["compared"] = False
        return {
            "name": name,
            "verdict": "inconclusive",
            "geometry": geom_block,
            "pixel": pixel_block,
            "diffs": diffs,
        }

    mismatches = _compare_geometry(
        ref_geom,
        app_geom,
        tolerance=geometry_tolerance_px,
        baselines=_baselines_for_state(baselines, checkpoint, name),
    )
    geom_block["mismatches"] = mismatches

    if mismatches:
        for m in mismatches:
            diffs.append(
                {
                    "kind": "geometry",
                    "region": m.get("id", ""),
                    "detail": m.get("detail", ""),
                }
            )
        return {
            "name": name,
            "verdict": "fail",
            "geometry": geom_block,
            "pixel": pixel_block,
            "diffs": diffs,
        }

    ref_path = ref.get("screenshot")
    app_path = app.get("screenshot")
    if not ref_path or not app_path:
        return {
            "name": name,
            "verdict": "inconclusive",
            "geometry": geom_block,
            "pixel": pixel_block,
            "diffs": diffs,
        }

    masks = list(st.get("masks") or [])
    ignore = _ignore_regions(
        baselines=_baselines_for_state(baselines, checkpoint, name),
        masks=masks,
    )
    try:
        ref_px = _load_png_rgb(ref_path)
        app_px = _load_png_rgb(app_path)
    except _MeasureIOError:
        raise

    if len(ref_px) != len(app_px):
        return {
            "name": name,
            "verdict": "inconclusive",
            "geometry": geom_block,
            "pixel": {**pixel_block, "compared": False},
            "diffs": diffs,
        }

    w, h = ref_px[0], ref_px[1]
    if app_px[0] != w or app_px[1] != h:
        return {
            "name": name,
            "verdict": "inconclusive",
            "geometry": geom_block,
            "pixel": {**pixel_block, "compared": False},
            "diffs": diffs,
        }

    ref_colors = ref_px[2]
    app_colors = app_px[2]
    changed, total = _pixel_diff(ref_colors, app_colors, w, h, ignore)

    mismatch_ratio = (changed / total) if total else 0.0
    pixel_block = {
        "compared": True,
        "mismatch_ratio": mismatch_ratio,
        "threshold": pixel_threshold,
    }

    # Fail when ratio exceeds threshold, or when any countable pixel differs
    # (sub-threshold ratios still fail if at least one pixel changed).
    pixel_fail = mismatch_ratio > pixel_threshold or changed > 0
    if pixel_fail:
        diffs.append(
            {
                "kind": "pixel",
                "region": f"0,0,{w},{h}",
                "detail": f"mismatch_ratio={mismatch_ratio:.6f}",
            }
        )
        verdict = "fail"
    else:
        verdict = "pass"

    return {
        "name": name,
        "verdict": verdict,
        "geometry": geom_block,
        "pixel": pixel_block,
        "diffs": diffs,
    }


def _baselines_for_state(baselines, checkpoint, state_name: str) -> list[dict]:
    if not baselines:
        return []
    out: list[dict] = []
    for b in baselines:
        if b.get("state") != state_name:
            continue
        cp = b.get("checkpoint")
        if cp is not None and checkpoint is not None and cp != checkpoint:
            continue
        out.append(b)
    return out


def _ignore_regions(*, baselines: list[dict], masks: list[dict]) -> list[dict]:
    regions: list[dict] = []
    for b in baselines:
        reg = b.get("region")
        if reg and _region_positive(reg):
            regions.append(reg)
    for m in masks:
        reg = m.get("region") if isinstance(m, dict) else None
        if reg and _region_positive(reg):
            regions.append(reg)
    return regions


def _region_positive(reg: dict) -> bool:
    return (
        int(reg.get("w", 0)) > 0
        and int(reg.get("h", 0)) > 0
        and int(reg.get("x", 0)) >= 0
        and int(reg.get("y", 0)) >= 0
    )


def _in_region(x: int, y: int, reg: dict) -> bool:
    rx, ry = int(reg["x"]), int(reg["y"])
    rw, rh = int(reg["w"]), int(reg["h"])
    return rx <= x < rx + rw and ry <= y < ry + rh


def _pixel_diff(
    ref_colors: list[tuple[int, int, int]],
    app_colors: list[tuple[int, int, int]],
    w: int,
    h: int,
    ignore: list[dict],
) -> tuple[int, int]:
    changed = 0
    total = 0
    for y in range(h):
        for x in range(w):
            if any(_in_region(x, y, reg) for reg in ignore):
                continue
            total += 1
            idx = y * w + x
            if ref_colors[idx] != app_colors[idx]:
                changed += 1
    return changed, total


def _compare_geometry(
    ref_geom: list,
    app_geom: list,
    *,
    tolerance: int,
    baselines: list[dict],
) -> list[dict]:
    ref_map = _geometry_index(ref_geom)
    app_map = _geometry_index(app_geom)
    all_ids = sorted(set(ref_map) | set(app_map))
    mismatches: list[dict] = []

    for box_id in all_ids:
        ref_box = ref_map.get(box_id)
        app_box = app_map.get(box_id)
        if ref_box is None or app_box is None:
            mismatches.append(
                {
                    "id": box_id,
                    "detail": "missing on reference or app",
                }
            )
            continue

        for key in ("x", "y", "w", "h"):
            rv = int(ref_box.get(key, 0))
            av = int(app_box.get(key, 0))
            if abs(rv - av) > tolerance:
                if _geometry_mismatch_ignored(ref_box, app_box, key, baselines):
                    continue
                mismatches.append(
                    {
                        "id": box_id,
                        "detail": f"{key}: ref={rv} app={av} (tol={tolerance})",
                    }
                )
                break

    return mismatches


def _geometry_mismatch_ignored(
    ref_box: dict, app_box: dict, key: str, baselines: list[dict]
) -> bool:
    """Mismatches wholly inside a baseline region are ignored for geometry."""
    if not baselines:
        return False
    for b in baselines:
        reg = b.get("region")
        if not reg or not _region_positive(reg):
            continue
        if _box_inside_region(ref_box, reg) and _box_inside_region(app_box, reg):
            return True
    return False


def _box_inside_region(box: dict, reg: dict) -> bool:
    bx, by = int(box.get("x", 0)), int(box.get("y", 0))
    bw, bh = int(box.get("w", 0)), int(box.get("h", 0))
    rx, ry = int(reg["x"]), int(reg["y"])
    rw, rh = int(reg["w"]), int(reg["h"])
    return (
        bx >= rx
        and by >= ry
        and bx + bw <= rx + rw
        and by + bh <= ry + rh
    )


def _geometry_index(geom: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    no_id: list[dict] = []
    for i, box in enumerate(geom):
        bid = box.get("id")
        if bid:
            out[str(bid)] = box
        else:
            no_id.append(box)
    if no_id:
        sorted_boxes = sorted(
            no_id,
            key=lambda b: (int(b.get("y", 0)), int(b.get("x", 0))),
        )
        for i, box in enumerate(sorted_boxes):
            out[f"__pos_{i}"] = box
    return out


def _load_png_rgb(path: str | Path) -> tuple[int, int, list[tuple[int, int, int]]]:
    data = Path(path).read_bytes()
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise _MeasureIOError(f"not a PNG: {path}")

    pos = 8
    width = height = None
    bit_depth = color_type = None
    idat = b""

    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4
        tag = data[pos : pos + 4]
        pos += 4
        chunk = data[pos : pos + length]
        pos += length + 4  # CRC

        if tag == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(
                ">IIBB", chunk[:10]
            )
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break

    if width is None or height is None or not idat:
        raise _MeasureIOError(f"invalid PNG: {path}")

    if bit_depth != 8 or color_type not in (2, 6):
        raise _MeasureIOError(f"unsupported PNG format (type={color_type}): {path}")

    raw = zlib.decompress(idat)
    bpp = 3 if color_type == 2 else 4
    pixels = _decode_png_pixels(raw, width, height, bpp, path)
    return width, height, pixels


def _decode_png_pixels(
    raw: bytes, width: int, height: int, bpp: int, path: str | Path
) -> list[tuple[int, int, int]]:
    per_pixel = width * height * (1 + bpp)
    if len(raw) == per_pixel:
        pixels: list[tuple[int, int, int]] = []
        i = 0
        while i + 1 + bpp <= len(raw):
            if raw[i] != 0:
                raise _MeasureIOError(f"unsupported PNG filter {raw[i]}: {path}")
            pixels.append(tuple(raw[i + 1 : i + 1 + bpp][:3]))  # type: ignore[arg-type]
            i += 1 + bpp
        return pixels

    stride = 1 + width * bpp
    expected = height * stride
    if len(raw) < expected:
        raise _MeasureIOError(f"truncated PNG image data: {path}")

    pixels = []
    row_start = 0
    for _ in range(height):
        filter_type = raw[row_start]
        if filter_type > 4:
            raise _MeasureIOError(f"unsupported PNG filter {filter_type}: {path}")
        row = raw[row_start + 1 : row_start + stride]
        if filter_type != 0:
            raise _MeasureIOError(f"unsupported PNG filter {filter_type}: {path}")
        for x in range(width):
            off = x * bpp
            pixels.append((row[off], row[off + 1], row[off + 2]))
        row_start += stride
    return pixels