from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageStat


@dataclass
class ThermalFinding:
    x: float
    y: float
    strength: float
    area: int
    delta_t: float
    anomaly_type: str
    confidence: float


def _heat_score(pixel: tuple[int, ...]) -> float:
    if len(pixel) == 4:
        r, g, b, _ = pixel
    elif len(pixel) == 3:
        r, g, b = pixel
    else:
        r = g = b = pixel[0]
    # A deliberately simple radiometric proxy for the runnable demo. It rewards
    # bright/warm colors and remains deterministic for any uploaded image.
    return 0.52 * r + 0.34 * g - 0.16 * b


def _components(mask: list[list[bool]], scores: list[list[float]]) -> list[tuple[int, float, float, float]]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    seen = [[False] * width for _ in range(height)]
    comps: list[tuple[int, float, float, float]] = []
    for y in range(height):
        for x in range(width):
            if not mask[y][x] or seen[y][x]:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            points: list[tuple[int, int]] = []
            peak = scores[y][x]
            while stack:
                cx, cy = stack.pop()
                points.append((cx, cy))
                peak = max(peak, scores[cy][cx])
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < width and 0 <= ny < height and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if len(points) >= 2:
                cx = sum(p[0] for p in points) / len(points)
                cy = sum(p[1] for p in points) / len(points)
                comps.append((len(points), cx, cy, peak))
    return comps


def analyze_thermal_image(path: Path, max_findings: int = 6) -> list[ThermalFinding]:
    image = Image.open(path).convert("RGB")
    image.thumbnail((144, 108))
    width, height = image.size
    pixels = list(image.get_flattened_data())
    values = [_heat_score(px) for px in pixels]
    mean = sum(values) / max(1, len(values))
    variance = sum((v - mean) ** 2 for v in values) / max(1, len(values))
    std = math.sqrt(variance)
    threshold = mean + max(10.0, 1.45 * std)
    score_rows = [values[y * width : (y + 1) * width] for y in range(height)]
    mask = [[score_rows[y][x] >= threshold for x in range(width)] for y in range(height)]
    components = sorted(_components(mask, score_rows), key=lambda item: (item[3], item[0]), reverse=True)

    if not components:
        # Fall back to separated local maxima so every valid upload produces a
        # useful, inspectable demo result instead of an empty screen.
        ranked = sorted(enumerate(values), key=lambda item: item[1], reverse=True)
        selected: list[tuple[int, float, float, float]] = []
        for index, score in ranked:
            x = index % width
            y = index // width
            if all((x - sx) ** 2 + (y - sy) ** 2 > 16 ** 2 for _, sx, sy, _ in selected):
                selected.append((3, float(x), float(y), score))
            if len(selected) >= 3:
                break
        components = selected

    peak_all = max(values) if values else threshold + 1
    findings: list[ThermalFinding] = []
    for area, cx, cy, peak in components[:max_findings]:
        relative = max(0.0, min(1.0, (peak - threshold) / max(1.0, peak_all - threshold)))
        delta_t = round(7.5 + 18.5 * relative + min(6.0, area / 4), 1)
        if area >= 22:
            anomaly_type = "Multi-cell Hotspot"
        elif area <= 5 and relative > 0.72:
            anomaly_type = "Diode Hotspot"
        else:
            anomaly_type = "Cell Hotspot"
        confidence = round(min(0.98, 0.78 + 0.18 * relative + min(0.04, area / 500)), 2)
        findings.append(
            ThermalFinding(
                x=round(cx / max(1, width - 1) * 100, 2),
                y=round(cy / max(1, height - 1) * 100, 2),
                strength=round(relative, 3),
                area=area,
                delta_t=delta_t,
                anomaly_type=anomaly_type,
                confidence=confidence,
            )
        )
    return findings
