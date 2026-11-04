"""Conservative image-change candidate detector for inverter inspection.

This is NOT a trained computer-vision model, a defect classifier, or a
calibrated thermography device. Comparison requires manually matched views.
Nothing here assigns physical root cause, defect severity or failure probability.

Dependencies: Pillow, NumPy.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

METHOD_VERSION = "inverter-visual-change-starter-v1"
MAX_PIXELS = 20_000_000
MAX_ANALYSIS_SIDE = 1024


def _image(path: str | Path) -> Image.Image:
    try:
        with Image.open(path) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("Image exceeds the 20-megapixel input limit")
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Unsupported or unreadable image") from exc
    return image


def _regions(mask: np.ndarray, strength: np.ndarray, min_pixels: int, max_regions: int = 12) -> list[dict[str, Any]]:
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    components: list[dict[str, Any]] = []
    for yy, xx in np.argwhere(mask):
        y, x = int(yy), int(xx)
        if visited[y, x]:
            continue
        visited[y, x] = True
        pending = [(y, x)]
        count, max_score = 0, float("-inf")
        x0 = x1 = x
        y0 = y1 = y
        while pending:
            cy, cx = pending.pop()
            count += 1
            max_score = max(max_score, float(strength[cy, cx]))
            x0, x1 = min(x0, cx), max(x1, cx)
            y0, y1 = min(y0, cy), max(y1, cy)
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    pending.append((ny, nx))
        if count >= min_pixels:
            components.append({
                "bbox_xyxy": [x0, y0, x1 + 1, y1 + 1],
                "changed_pixels": count,
                "peak_difference": round(max_score, 3),
            })
    components.sort(key=lambda x: (x["changed_pixels"], x["peak_difference"]), reverse=True)
    return components[:max_regions]


def analyze_rgb_pair(
    reference: str | Path,
    current: str | Path,
    *,
    minimum_channel_difference: float = 30.0,
) -> dict[str, Any]:
    """Locate visual changes in matching RGB views, not classify defects."""
    if not 1 <= minimum_channel_difference <= 255:
        raise ValueError("minimum_channel_difference must be between 1 and 255")
    ref = _image(reference)
    now = _image(current)
    if ref.size != now.size:
        raise ValueError("Reference and current RGB images must share the same dimensions and registered view")
    original_w, original_h = now.size
    if max(original_w, original_h) > MAX_ANALYSIS_SIDE:
        ref.thumbnail((MAX_ANALYSIS_SIDE, MAX_ANALYSIS_SIDE), Image.Resampling.LANCZOS)
        now.thumbnail((MAX_ANALYSIS_SIDE, MAX_ANALYSIS_SIDE), Image.Resampling.LANCZOS)
    reference_pixels = np.asarray(ref, dtype=np.float32)
    current_pixels = np.asarray(now, dtype=np.float32)
    # Approximate constant scene-wide color/lighting drift. This does NOT
    # compensate for camera motion, shadows or differing viewing angles.
    drift = np.median((current_pixels - reference_pixels).reshape(-1, 3), axis=0)
    diff = np.abs(current_pixels - reference_pixels - drift).mean(axis=2)
    median = float(np.median(diff))
    mad = float(np.median(np.abs(diff - median)))
    threshold = max(float(minimum_channel_difference), median + 4 * 1.4826 * mad)
    mask = diff >= threshold
    changed_fraction = float(np.mean(mask))
    if changed_fraction > 0.40:
        status = "unreliable_comparison"
        regions: list[dict[str, Any]] = []
    else:
        status = "candidate_regions" if mask.any() else "no_change_detected"
        raw = _regions(mask, diff, max(8, round(mask.size * 0.00015)))
        scale_x, scale_y = original_w / now.width, original_h / now.height
        regions = [{
            "bbox_xyxy": [
                min(original_w, round(item["bbox_xyxy"][0] * scale_x)),
                min(original_h, round(item["bbox_xyxy"][1] * scale_y)),
                min(original_w, round(item["bbox_xyxy"][2] * scale_x)),
                min(original_h, round(item["bbox_xyxy"][3] * scale_y)),
            ],
            "changed_pixels_at_analysis_resolution": item["changed_pixels"],
            "peak_pixel_difference_0_255": item["peak_difference"],
            "candidate_type": "visual_change_unclassified",
        } for item in raw]
        if not regions:
            status = "no_change_detected"
    return {
        "method": METHOD_VERSION,
        "modality": "rgb_reference_comparison",
        "status": status,
        "image_size": [original_w, original_h],
        "analysis_size": [now.width, now.height],
        "candidate_regions": regions,
        "changed_fraction": round(changed_fraction, 5),
        "threshold_0_255": round(threshold, 2),
        "limitations": [
            "Not a trained inverter defect detector",
            "Requires same inverter, view, geometry and comparable lighting",
            "Changes may be due to reflections, shadows, scene motion or legitimate maintenance",
            "No burn, corrosion, cable damage or fault classification is inferred",
            "No change detected does not establish equipment health",
        ],
        "operator_review_required": True,
    }


def _temperature_csv(path: str | Path) -> np.ndarray:
    """Read an explicitly Celsius-valued, rectangular thermal measurement matrix."""
    data: list[list[float]] = []
    with open(path, newline="", encoding="utf-8-sig") as stream:
        for row in csv.reader(stream):
            if not row or not any(item.strip() for item in row):
                continue
            data.append([float(value.strip()) for value in row])
            if len(data) > 4096:
                raise ValueError("Thermal matrix has too many rows")
    if not data or not data[0] or any(len(row) != len(data[0]) for row in data):
        raise ValueError("Thermal matrix must be nonempty and rectangular")
    if len(data[0]) > 4096:
        raise ValueError("Thermal matrix has too many columns")
    matrix = np.asarray(data, dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("Thermal matrix must contain finite numeric Celsius measurements")
    return matrix


def analyze_thermal_measurements(
    reference_csv: str | Path,
    current_csv: str | Path,
    *,
    delta_threshold_c: float = 10.0,
    min_region_pixels: int = 4,
) -> dict[str, Any]:
    """Locate positive temperature changes using trusted upstream radiometry.

    Never infer Celsius values from a palette JPEG/PNG. Users must verify that
    both grids are calibrated, spatially aligned and load-comparable.
    """
    if not 0 < delta_threshold_c <= 100:
        raise ValueError("delta_threshold_c must be greater than 0 and at most 100")
    if min_region_pixels < 1:
        raise ValueError("min_region_pixels must be positive")
    ref = _temperature_csv(reference_csv)
    cur = _temperature_csv(current_csv)
    if ref.shape != cur.shape:
        raise ValueError("The two thermal matrices must use identical registered dimensions")
    rise = cur - ref
    raw = _regions(rise >= delta_threshold_c, rise, min_region_pixels)
    return {
        "method": METHOD_VERSION,
        "modality": "thermal_celsius_matrix_comparison",
        "status": "candidate_regions" if raw else "no_change_detected",
        "matrix_size": [int(cur.shape[1]), int(cur.shape[0])],
        "candidate_regions": [{
            "bbox_xyxy": item["bbox_xyxy"],
            "changed_pixels": item["changed_pixels"],
            "peak_temperature_rise_c": item["peak_difference"],
            "candidate_type": "relative_temperature_increase_unclassified",
        } for item in raw],
        "delta_threshold_c": delta_threshold_c,
        "limitations": [
            "Input values must be calibrated Celsius measurements, not RGB palette values",
            "Camera calibration, emissivity, reflected temperature and operating load are not validated by this script",
            "Reference and current frames must describe the same aligned component/view",
            "Temperature change is not a confirmed fault or an electrical-safety assessment",
            "No change detected does not establish equipment health",
        ],
        "operator_review_required": True,
    }


def annotate_rgb(current_image: str | Path, result: dict[str, Any], output: str | Path) -> None:
    image = _image(current_image)
    draw = ImageDraw.Draw(image)
    for candidate in result.get("candidate_regions", []):
        xyxy = candidate["bbox_xyxy"]
        draw.rectangle(xyxy, outline=(255, 172, 0), width=max(2, image.width // 500))
    image.save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Experimental inverter image comparison (NOT a trained defect classifier)")
    parser.add_argument("mode", choices=["rgb", "thermal-csv"])
    parser.add_argument("--reference", required=True, help="Healthy/reference view or Celsius matrix CSV")
    parser.add_argument("--current", required=True, help="Current view or Celsius matrix CSV")
    parser.add_argument("--output", help="Optional annotated RGB output (rgb mode only)")
    parser.add_argument("--delta-threshold-c", type=float, default=10.0, help="Exploratory numeric threshold, not a diagnostic threshold")
    args = parser.parse_args()
    if args.mode == "rgb":
        result = analyze_rgb_pair(args.reference, args.current)
        if args.output:
            annotate_rgb(args.current, result, args.output)
    else:
        if args.output:
            parser.error("--output is only supported for RGB image mode")
        result = analyze_thermal_measurements(args.reference, args.current, delta_threshold_c=args.delta_threshold_c)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
