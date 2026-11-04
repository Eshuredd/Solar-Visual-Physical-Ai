"""Deterministic inverter visual/thermal comparison for human review.

This module is adapted from the bundled DeepDrishti inverter visual test kit.
It is not a trained defect classifier and never creates an operational alert.
"""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError


METHOD_VERSION = "inverter-visual-change-deterministic-v1"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_ANALYSIS_SIDE = 1024
MAX_THERMAL_BYTES = 2 * 1024 * 1024
MAX_THERMAL_CELLS = 2_000_000
SUPPORTED_IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}
PRIVATE_ROOT = Path(os.getenv("INVERTER_VISUAL_EVIDENCE_ROOT", Path(__file__).resolve().parent / "data" / "inverter_visual_evidence"))
PREVIEW_ROOT = PRIVATE_ROOT / "previews"
SAVED_ROOT = PRIVATE_ROOT / "saved"


class VisionValidationError(ValueError):
    pass


def ensure_storage() -> None:
    PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    SAVED_ROOT.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _decode_image(content: bytes, filename: str) -> Image.Image:
    if not content:
        raise VisionValidationError(f"{filename} is empty")
    if len(content) > MAX_IMAGE_BYTES:
        raise VisionValidationError(f"{filename} exceeds the 8 MB image limit")
    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in SUPPORTED_IMAGE_FORMATS:
                raise VisionValidationError("Only PNG, JPEG and WEBP images are supported")
            if source.width * source.height > MAX_PIXELS:
                raise VisionValidationError("Image exceeds the 20-megapixel input limit")
            return ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise VisionValidationError(f"{filename} is not a supported, readable image") from exc


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def _regions(mask: np.ndarray, strength: np.ndarray, minimum: int, maximum: int = 12) -> list[dict[str, Any]]:
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    result: list[dict[str, Any]] = []
    for yy, xx in np.argwhere(mask):
        y, x = int(yy), int(xx)
        if visited[y, x]:
            continue
        visited[y, x] = True
        pending = [(y, x)]
        count = 0
        peak = float("-inf")
        x0 = x1 = x
        y0 = y1 = y
        while pending:
            cy, cx = pending.pop()
            count += 1
            peak = max(peak, float(strength[cy, cx]))
            x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    pending.append((ny, nx))
        if count >= minimum:
            result.append({"bbox_xyxy": [x0, y0, x1 + 1, y1 + 1], "region_size_pixels": count, "peak": peak})
    result.sort(key=lambda item: (item["region_size_pixels"], item["peak"]), reverse=True)
    return result[:maximum]


def analyze_rgb_bytes(reference: bytes, current: bytes, *, reference_name: str, current_name: str,
                      minimum_channel_difference: float = 30.0) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not 1 <= minimum_channel_difference <= 255:
        raise VisionValidationError("Minimum channel difference must be between 1 and 255")
    ref = _decode_image(reference, reference_name)
    now = _decode_image(current, current_name)
    if ref.size != now.size:
        raise VisionValidationError("Reference and current RGB images must share identical dimensions and a registered view")
    original_w, original_h = now.size
    analysis_ref, analysis_now = ref.copy(), now.copy()
    if max(original_w, original_h) > MAX_ANALYSIS_SIDE:
        analysis_ref.thumbnail((MAX_ANALYSIS_SIDE, MAX_ANALYSIS_SIDE), Image.Resampling.LANCZOS)
        analysis_now.thumbnail((MAX_ANALYSIS_SIDE, MAX_ANALYSIS_SIDE), Image.Resampling.LANCZOS)
    reference_pixels = np.asarray(analysis_ref, dtype=np.float32)
    current_pixels = np.asarray(analysis_now, dtype=np.float32)
    drift = np.median((current_pixels - reference_pixels).reshape(-1, 3), axis=0)
    difference = np.abs(current_pixels - reference_pixels - drift).mean(axis=2)
    median = float(np.median(difference))
    mad = float(np.median(np.abs(difference - median)))
    threshold = max(float(minimum_channel_difference), median + 4 * 1.4826 * mad)
    raw_mask = difference >= threshold
    # Median filtering removes isolated compression/sensor speckle without
    # inventing or classifying a region.
    mask = np.asarray(Image.fromarray(raw_mask.astype(np.uint8) * 255).filter(ImageFilter.MedianFilter(3))) > 0
    changed_fraction = float(np.mean(mask))
    if changed_fraction > 0.40:
        status, raw = "unreliable_comparison", []
    else:
        raw = _regions(mask, difference, max(8, round(mask.size * 0.00015)))
        status = "candidate_regions" if raw else "no_change_detected"
    scale_x, scale_y = original_w / analysis_now.width, original_h / analysis_now.height
    candidates = []
    for index, item in enumerate(raw, start=1):
        bbox = [
            min(original_w, round(item["bbox_xyxy"][0] * scale_x)),
            min(original_h, round(item["bbox_xyxy"][1] * scale_y)),
            min(original_w, round(item["bbox_xyxy"][2] * scale_x)),
            min(original_h, round(item["bbox_xyxy"][3] * scale_y)),
        ]
        candidates.append({
            "candidate_index": index, "candidate_type": "visual_change_unclassified",
            "bbox_xyxy": bbox,
            "region_size_pixels": round(item["region_size_pixels"] * scale_x * scale_y),
            "relative_change_score": round(min(1.0, item["peak"] / 255.0), 4),
            "peak_pixel_difference_0_255": round(item["peak"], 3),
            "detection_method": METHOD_VERSION, "review_state": "Pending Review",
        })
    annotated = now.copy()
    draw = ImageDraw.Draw(annotated)
    width = max(3, annotated.width // 300)
    for item in candidates:
        draw.rectangle(item["bbox_xyxy"], outline=(255, 172, 0), width=width)
        draw.text((item["bbox_xyxy"][0] + width, item["bbox_xyxy"][1] + width), f"C{item['candidate_index']}", fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
    result = {
        "method": METHOD_VERSION, "modality": "rgb", "status": status,
        "image_size": [original_w, original_h], "analysis_size": [analysis_now.width, analysis_now.height],
        "candidate_count": len(candidates), "candidate_regions": candidates,
        "changed_fraction": round(changed_fraction, 5), "threshold_0_255": round(threshold, 2),
        "provenance": "pixel comparison of operator-supplied matched RGB views",
        "limitations": [
            "Deterministic visual comparison, not a trained inverter defect classifier",
            "Requires the same inverter, viewpoint, geometry and comparable lighting",
            "Reflections, shadows, motion and maintenance may create false candidates",
            "No defect type, root cause, severity or equipment health is inferred",
        ],
        "operator_review_required": True,
    }
    return result, {"reference.png": _png(ref), "current.png": _png(now), "annotated.png": _png(annotated)}


def _thermal_matrix(content: bytes, filename: str, unit: str) -> tuple[np.ndarray, int]:
    if not content:
        raise VisionValidationError(f"{filename} is empty")
    if len(content) > MAX_THERMAL_BYTES:
        raise VisionValidationError(f"{filename} exceeds the 2 MB CSV limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise VisionValidationError("Thermal CSV must be UTF-8 encoded") from exc
    rows: list[list[float]] = []
    missing = 0
    for row_number, row in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not row or not any(value.strip() for value in row):
            continue
        parsed: list[float] = []
        for column, value in enumerate(row, start=1):
            if not value.strip():
                parsed.append(float("nan")); missing += 1; continue
            try:
                number = float(value)
            except ValueError as exc:
                raise VisionValidationError(f"{filename} row {row_number}, column {column} is not numeric") from exc
            if not np.isfinite(number):
                raise VisionValidationError(f"{filename} contains a non-finite temperature")
            parsed.append(number)
        rows.append(parsed)
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise VisionValidationError("Thermal matrices must be nonempty and rectangular")
    if len(rows) * len(rows[0]) > MAX_THERMAL_CELLS:
        raise VisionValidationError("Thermal matrix exceeds the 2,000,000-cell limit")
    matrix = np.asarray(rows, dtype=float)
    normalized_unit = unit.upper()
    if normalized_unit in {"C", "CELSIUS", "°C"}:
        pass
    elif normalized_unit in {"F", "FAHRENHEIT", "°F"}:
        matrix = (matrix - 32) * 5 / 9
    elif normalized_unit in {"K", "KELVIN"}:
        matrix = matrix - 273.15
    else:
        raise VisionValidationError("Temperature unit must be Celsius, Fahrenheit or Kelvin")
    valid = matrix[np.isfinite(matrix)]
    if not valid.size:
        raise VisionValidationError("Thermal matrix contains no measured temperatures")
    if valid.min() < -100 or valid.max() > 500:
        raise VisionValidationError("Thermal measurements fall outside the supported -100 to 500 Celsius range")
    return matrix, missing


def _heatmap(matrix: np.ndarray, low: float, high: float, boxes: list[list[int]] | None = None) -> bytes:
    valid = np.where(np.isfinite(matrix), matrix, low)
    normalized = np.clip((valid - low) / max(1e-9, high - low), 0, 1)
    rgb = np.stack((np.clip(normalized * 2, 0, 1), np.clip(1 - np.abs(normalized - .5) * 2, 0, 1), np.clip((1 - normalized) * 2, 0, 1)), axis=2)
    image = Image.fromarray((rgb * 255).astype(np.uint8), "RGB")
    scale = max(1, min(16, 720 // max(image.size)))
    image = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
    if boxes:
        draw = ImageDraw.Draw(image)
        for index, box in enumerate(boxes, start=1):
            scaled = [value * scale for value in box]
            draw.rectangle(scaled, outline=(255, 255, 255), width=max(2, scale // 2))
            draw.text((scaled[0] + 2, scaled[1] + 2), f"C{index}", fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))
    return _png(image)


def analyze_thermal_bytes(reference: bytes, current: bytes, *, reference_name: str, current_name: str,
                          unit: str = "Celsius", delta_threshold_c: float = 10.0,
                          min_region_pixels: int = 4) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not 0 < delta_threshold_c <= 100:
        raise VisionValidationError("Thermal threshold must be greater than 0 and at most 100 Celsius")
    if not 1 <= min_region_pixels <= 100_000:
        raise VisionValidationError("Minimum thermal region size is outside the supported range")
    ref, missing_ref = _thermal_matrix(reference, reference_name, unit)
    now, missing_now = _thermal_matrix(current, current_name, unit)
    if ref.shape != now.shape:
        raise VisionValidationError("Reference and current thermal matrices must have identical registered dimensions")
    valid = np.isfinite(ref) & np.isfinite(now)
    rise = np.where(valid, now - ref, float("-inf"))
    raw = _regions(valid & (rise >= delta_threshold_c), rise, min_region_pixels)
    candidates = [{
        "candidate_index": index, "candidate_type": "relative_temperature_increase_unclassified",
        "bbox_xyxy": item["bbox_xyxy"], "region_size_cells": item["region_size_pixels"],
        "peak_temperature_rise_c": round(item["peak"], 3), "threshold_c": delta_threshold_c,
        "detection_method": METHOD_VERSION, "review_state": "Pending Review",
        "data_provenance": "calibrated numeric matrix asserted by uploader",
    } for index, item in enumerate(raw, start=1)]
    all_valid = np.concatenate((ref[np.isfinite(ref)], now[np.isfinite(now)]))
    thermal_low, thermal_high = float(all_valid.min()), float(all_valid.max())
    diff_display = np.where(valid, np.maximum(0, rise), 0)
    diff_high = max(delta_threshold_c, float(diff_display.max()))
    boxes = [item["bbox_xyxy"] for item in candidates]
    result = {
        "method": METHOD_VERSION, "modality": "thermal", "status": "candidate_regions" if candidates else "no_change_detected",
        "matrix_size": [int(now.shape[1]), int(now.shape[0])], "candidate_count": len(candidates),
        "candidate_regions": candidates, "delta_threshold_c": delta_threshold_c,
        "missing_measurements": {"reference": missing_ref, "current": missing_now, "comparable_cells": int(valid.sum())},
        "temperature_unit_received": unit, "temperature_unit_normalized": "Celsius",
        "provenance": "numeric temperature matrices; calibration and registration asserted by uploader",
        "limitations": [
            "CSV values must be calibrated radiometric measurements; colorized images are not temperatures",
            "Calibration, emissivity, reflected temperature and operating load are not independently validated",
            "Temperature rise is review evidence, not a confirmed fault or safety assessment",
            "No change detected does not establish equipment health",
        ],
        "operator_review_required": True,
    }
    return result, {
        "reference.csv": reference, "current.csv": current,
        "reference_heatmap.png": _heatmap(ref, thermal_low, thermal_high),
        "current_heatmap.png": _heatmap(now, thermal_low, thermal_high),
        "difference_heatmap.png": _heatmap(diff_display, 0, diff_high, boxes),
    }


def create_preview(*, site_id: str, inverter_id: str, modality: str, result: dict[str, Any],
                   evidence: dict[str, bytes], provenance: str, dataset_id: str | None = None) -> dict[str, Any]:
    ensure_storage()
    token = secrets.token_urlsafe(24)
    folder = PREVIEW_ROOT / token
    folder.mkdir(mode=0o700)
    for name, content in evidence.items():
        (folder / Path(name).name).write_bytes(content)
    metadata = {
        "preview_token": token, "site_id": site_id, "inverter_id": inverter_id, "modality": modality,
        "result": result, "provenance": provenance, "dataset_id": dataset_id, "created_at": _utc_now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "evidence_names": sorted(evidence),
    }
    (folder / "preview.json").write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


def read_preview(token: str) -> dict[str, Any]:
    if not token or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in token):
        raise VisionValidationError("Invalid preview token")
    path = PREVIEW_ROOT / token / "preview.json"
    if not path.is_file():
        raise VisionValidationError("Visual inspection preview was not found or has expired")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    expires = datetime.fromisoformat(metadata["expires_at"].replace("Z", "+00:00"))
    if expires < datetime.now(timezone.utc):
        raise VisionValidationError("Visual inspection preview has expired")
    return metadata


def preview_evidence_path(token: str, name: str) -> Path:
    metadata = read_preview(token)
    safe_name = Path(name).name
    if safe_name != name or safe_name not in metadata["evidence_names"]:
        raise VisionValidationError("Preview evidence was not found")
    return PREVIEW_ROOT / token / safe_name


def persist_preview(token: str, inspection_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    metadata = read_preview(token)
    destination = SAVED_ROOT / inspection_id
    destination.mkdir(mode=0o700)
    locations: dict[str, str] = {}
    for name in metadata["evidence_names"]:
        source = preview_evidence_path(token, name)
        target = destination / name
        target.write_bytes(source.read_bytes())
        locations[name] = f"{inspection_id}/{name}"
    return metadata, locations


def saved_evidence_path(inspection_id: str, name: str, allowed: list[str]) -> Path:
    safe_name = Path(name).name
    if safe_name != name or safe_name not in allowed:
        raise VisionValidationError("Saved evidence was not found")
    return SAVED_ROOT / inspection_id / safe_name
