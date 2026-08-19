from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, Tuple

import numpy as np

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeaponCalibration:
    """Dauerhafte Korrektur der Treffpunktlage einer Waffe."""

    offset_x: float = 0.0
    offset_y: float = 0.0
    residual_px: float = 0.0
    sample_count: int = 0
    active: bool = False

    def apply(
        self, point: Tuple[int, int], screen_size: Tuple[int, int]
    ) -> Tuple[int, int]:
        width, height = screen_size
        x = int(round(point[0] + self.offset_x))
        y = int(round(point[1] + self.offset_y))
        return max(0, min(width - 1, x)), max(0, min(height - 1, y))


def fit_weapon_calibration(
    groups: Sequence[Sequence[Tuple[int, int]]],
    targets: Sequence[Tuple[int, int]],
    screen_size: Tuple[int, int],
) -> WeaponCalibration:
    """Ermittelt eine robuste Verschiebung aus den fünf Einschießgruppen."""

    if len(groups) != len(targets) or len(groups) < 3:
        raise ValueError("Für die Waffenkalibrierung werden alle Zielgruppen benötigt")

    group_offsets: list[Tuple[float, float]] = []
    sample_count = 0
    for points, target in zip(groups, targets):
        if not points:
            raise ValueError("Eine Einschießgruppe enthält keine gültigen Treffer")
        samples = np.asarray(points, dtype=np.float64)
        mean = samples.mean(axis=0)
        group_offsets.append((float(target[0] - mean[0]), float(target[1] - mean[1])))
        sample_count += len(points)

    offsets = np.asarray(group_offsets, dtype=np.float64)
    correction = np.median(offsets, axis=0)
    residual = float(
        math.sqrt(np.mean(np.sum((offsets - correction) ** 2, axis=1)))
    )
    width, height = screen_size
    if abs(correction[0]) > width * 0.35 or abs(correction[1]) > height * 0.35:
        raise ValueError("Die gemessene Waffenabweichung ist unplausibel groß")
    maximum_residual = max(50.0, min(width, height) * 0.10)
    if residual > maximum_residual:
        raise ValueError(
            "Treffpunktlagen sind zu unterschiedlich – Einschießen wiederholen"
        )

    return WeaponCalibration(
        offset_x=float(correction[0]),
        offset_y=float(correction[1]),
        residual_px=residual,
        sample_count=sample_count,
        active=True,
    )


def load_weapon_calibration(
    path: Path, screen_size: Tuple[int, int]
) -> WeaponCalibration:
    if not path.exists():
        return WeaponCalibration()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        stored_size = (int(data["screen_width"]), int(data["screen_height"]))
        if stored_size != screen_size:
            LOGGER.warning(
                "Waffenkalibrierung für %sx%s passt nicht zu %sx%s und wird ignoriert",
                *stored_size,
                *screen_size,
            )
            return WeaponCalibration()
        return WeaponCalibration(
            offset_x=float(data["offset_x"]),
            offset_y=float(data["offset_y"]),
            residual_px=float(data.get("residual_px", 0.0)),
            sample_count=int(data.get("sample_count", 0)),
            active=True,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Waffenkalibrierung konnte nicht geladen werden: %s", exc)
        return WeaponCalibration()


def save_weapon_calibration(
    path: Path,
    calibration: WeaponCalibration,
    screen_size: Tuple[int, int],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = screen_size
    data = {
        "version": 1,
        "offset_x": calibration.offset_x,
        "offset_y": calibration.offset_y,
        "residual_px": calibration.residual_px,
        "sample_count": calibration.sample_count,
        "screen_width": width,
        "screen_height": height,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    temporary.replace(path)
