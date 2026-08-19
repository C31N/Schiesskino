from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import cv2
import numpy as np


@dataclass
class AlignmentResult:
    corners: np.ndarray
    confidence: float
    difference: np.ndarray
    mask: np.ndarray


@dataclass
class VerificationResult:
    camera_points: np.ndarray
    mapped_points: np.ndarray
    errors: np.ndarray
    mask: np.ndarray

    @property
    def max_error(self) -> float:
        return float(self.errors.max()) if len(self.errors) else float("inf")


@dataclass
class PrecisionAlignmentResult:
    homography: np.ndarray
    camera_points: np.ndarray
    screen_points: np.ndarray
    errors: np.ndarray
    mask: np.ndarray

    @property
    def max_error(self) -> float:
        return float(self.errors.max()) if len(self.errors) else float("inf")

    @property
    def mean_error(self) -> float:
        return float(self.errors.mean()) if len(self.errors) else float("inf")


@dataclass(frozen=True)
class StartupOpticalResult:
    active_filter_profile: str
    filter_confidence: float
    ambient_luma: float
    white_luma: float
    white_peak: float
    red_ratio: float
    laser_headroom: float


def startup_color_rects(screen_size: tuple[int, int]) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Geometrie der automatisch vermessenen Farbflächen."""

    width, height = screen_size
    margin_x = max(28, int(width * 0.035))
    top = max(80, int(height * 0.16))
    bottom = height - max(70, int(height * 0.13))
    gap = max(12, int(width * 0.014))
    usable = width - margin_x * 2 - gap * 5
    cell_width = usable // 6
    names = ("white", "red", "green", "blue", "cyan", "gray")
    return [
        (name, (margin_x + index * (cell_width + gap), top, cell_width, bottom - top))
        for index, name in enumerate(names)
    ]


def analyze_startup_color_response(
    dark_frames: Iterable[np.ndarray],
    color_frames: Iterable[np.ndarray],
    homography: np.ndarray,
    screen_size: tuple[int, int],
) -> StartupOpticalResult:
    """Bestimmt Umgebungslicht, Sensorreserve und einen roten Kamerafilter."""

    dark = _average_frames(dark_frames)
    color = _average_frames(color_frames)
    if dark.shape != color.shape:
        raise ValueError("Dunkel- und Farbtestbilder haben unterschiedliche Größen")
    width, height = screen_size
    warped_dark = cv2.warpPerspective(dark, homography, (width, height))
    warped_color = cv2.warpPerspective(color, homography, (width, height))
    samples: dict[str, np.ndarray] = {}
    for name, (x, y, cell_width, cell_height) in startup_color_rects(screen_size):
        inset_x = max(6, cell_width // 7)
        inset_y = max(6, cell_height // 9)
        samples[name] = warped_color[
            y + inset_y : y + cell_height - inset_y,
            x + inset_x : x + cell_width - inset_x,
        ]
    if any(sample.size == 0 for sample in samples.values()):
        raise RuntimeError("Farbtestflächen konnten nicht ausgewertet werden")

    # Die Kamerabilder liegen in dieser Pipeline in RGB vor.
    neutral = np.concatenate(
        (samples["white"].reshape(-1, 3), samples["gray"].reshape(-1, 3)), axis=0
    ).astype(np.float32)
    red = neutral[:, 0]
    other = np.maximum(neutral[:, 1], neutral[:, 2])
    valid = np.maximum(red, other) >= 24.0
    if int(valid.sum()) < 500:
        raise RuntimeError("Neutrale Farbflächen sind im Kamerabild zu dunkel")
    red = red[valid]
    other = other[valid]
    red_ratio = float(np.median(red) / max(1.0, float(np.median(other))))
    red_fraction = float(np.mean((red >= 45.0) & (red >= other * 1.55 + 7.0)))
    red_score = min(red_ratio / 1.58, red_fraction / 0.32)
    if red_ratio >= 1.58 and red_fraction >= 0.32:
        profile = "red_filter"
        confidence = max(0.55, min(1.0, red_score - 0.05))
    else:
        profile = "normal"
        normal_ratio_score = 1.0 - min(1.0, abs(red_ratio - 1.0) / 0.58)
        confidence = max(0.45, min(1.0, normal_ratio_score * (1.0 - red_fraction)))

    dark_gray = cv2.cvtColor(warped_dark, cv2.COLOR_RGB2GRAY)
    white_gray = cv2.cvtColor(samples["white"], cv2.COLOR_RGB2GRAY)
    ambient_luma = float(np.median(dark_gray))
    white_luma = float(np.median(white_gray))
    white_peak = float(np.percentile(white_gray, 99.5))
    return StartupOpticalResult(
        active_filter_profile=profile,
        filter_confidence=confidence,
        ambient_luma=ambient_luma,
        white_luma=white_luma,
        white_peak=white_peak,
        red_ratio=red_ratio,
        laser_headroom=max(0.0, 255.0 - white_peak),
    )


def order_corners(points: np.ndarray) -> np.ndarray:
    """Sortiert vier Punkte: oben-links, oben-rechts, unten-rechts, unten-links."""

    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    center = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    cyclic = pts[np.argsort(angles)]
    start = int(np.argmin(cyclic.sum(axis=1)))
    cyclic = np.roll(cyclic, -start, axis=0)
    if cyclic[1, 0] < cyclic[-1, 0]:
        cyclic = cyclic[[0, 3, 2, 1]]
    return cyclic.astype(np.float32)


def _average_frames(frames: Iterable[np.ndarray]) -> np.ndarray:
    materialized = list(frames)
    if not materialized:
        raise ValueError("Keine Kamerabilder für die automatische Ausrichtung vorhanden")
    return np.mean(np.stack(materialized).astype(np.float32), axis=0).astype(np.uint8)


def _quad_from_contour(contour: np.ndarray) -> Optional[np.ndarray]:
    hull = cv2.convexHull(contour)
    perimeter = cv2.arcLength(hull, True)
    for epsilon_factor in np.linspace(0.008, 0.09, 28):
        approx = cv2.approxPolyDP(hull, float(epsilon_factor) * perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return order_corners(approx.reshape(4, 2))

    rect = cv2.minAreaRect(hull)
    return order_corners(cv2.boxPoints(rect))


def detect_projection_quad(
    dark_frames: Iterable[np.ndarray],
    bright_frames: Iterable[np.ndarray],
    min_area_fraction: float = 0.12,
) -> AlignmentResult:
    """Findet die Projektionsfläche aus gemitteltem Schwarz-/Weißbild."""

    dark = _average_frames(dark_frames)
    bright = _average_frames(bright_frames)
    if dark.shape != bright.shape:
        raise ValueError("Schwarz- und Weißbilder haben unterschiedliche Größen")

    difference_bgr = cv2.absdiff(bright, dark)
    difference = cv2.cvtColor(difference_bgr, cv2.COLOR_BGR2GRAY)
    difference = cv2.GaussianBlur(difference, (9, 9), 0)
    otsu_threshold, mask = cv2.threshold(
        difference, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    # Otsu kann in einem sehr dunklen Raum zu großzügig werden.
    floor_threshold = max(12, int(otsu_threshold))
    _, mask = cv2.threshold(difference, floor_threshold, 255, cv2.THRESH_BINARY)

    height, width = mask.shape
    close_size = max(7, (min(height, width) // 45) | 1)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_size, close_size))
    open_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    frame_area = float(width * height)
    eligible = [
        contour
        for contour in contours
        if cv2.contourArea(contour) >= frame_area * min_area_fraction
    ]
    if not eligible:
        raise RuntimeError("Projektionsfläche nicht gefunden – Kamera auf die ganze Leinwand richten")

    contour = max(eligible, key=cv2.contourArea)
    corners = _quad_from_contour(contour)
    if corners is None:
        raise RuntimeError("Projektionsfläche hat keine vier stabilen Ecken")

    edge_margin = max(8, int(min(width, height) * 0.015))
    clipped_edges: list[str] = []
    if float(corners[:, 0].min()) <= edge_margin:
        clipped_edges.append("links")
    if float(corners[:, 0].max()) >= width - 1 - edge_margin:
        clipped_edges.append("rechts")
    if float(corners[:, 1].min()) <= edge_margin:
        clipped_edges.append("oben")
    if float(corners[:, 1].max()) >= height - 1 - edge_margin:
        clipped_edges.append("unten")
    if clipped_edges:
        edges = ", ".join(clipped_edges)
        raise RuntimeError(
            f"Leinwand am Kamerarand abgeschnitten ({edges}) – Kamera so ausrichten, "
            "dass rundherum ein schmaler Rand sichtbar ist"
        )

    quad_area = abs(float(cv2.contourArea(corners.reshape(-1, 1, 2))))
    area_score = min(1.0, quad_area / (frame_area * 0.45))
    contrast_score = min(1.0, float(difference[mask > 0].mean()) / 85.0)
    confidence = 0.65 * area_score + 0.35 * contrast_score
    return AlignmentResult(
        corners=corners,
        confidence=confidence,
        difference=difference,
        mask=mask,
    )


def detect_verification_markers(
    dark_frames: Iterable[np.ndarray],
    marker_frames: Iterable[np.ndarray],
    homography: np.ndarray,
    expected_screen_points: Iterable[tuple[int, int]],
    max_error_px: float = 55.0,
) -> VerificationResult:
    """Prüft vier projizierte Eckmarker unabhängig gegen die Homographie."""

    dark = _average_frames(dark_frames)
    markers = _average_frames(marker_frames)
    if dark.shape != markers.shape:
        raise ValueError("Prüfbilder haben unterschiedliche Größen")

    difference = cv2.cvtColor(cv2.absdiff(markers, dark), cv2.COLOR_BGR2GRAY)
    difference = cv2.GaussianBlur(difference, (5, 5), 0)
    otsu, mask = cv2.threshold(difference, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, mask = cv2.threshold(difference, max(16, int(otsu)), 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    frame_area = float(mask.shape[0] * mask.shape[1])
    candidates: list[tuple[float, tuple[float, float]]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < 4.0 or area > frame_area * 0.04:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        candidates.append(
            (
                area,
                (
                    moments["m10"] / moments["m00"],
                    moments["m01"] / moments["m00"],
                ),
            )
        )

    if len(candidates) < 4:
        raise RuntimeError(f"Eckprüfung unvollständig – nur {len(candidates)}/4 Marker erkannt")

    camera_points = np.array(
        [point for _, point in sorted(candidates, reverse=True)[:4]], dtype=np.float32
    )
    camera_points = order_corners(camera_points)
    expected = order_corners(np.asarray(list(expected_screen_points), dtype=np.float32))
    mapped = cv2.perspectiveTransform(camera_points.reshape(-1, 1, 2), homography)
    mapped = mapped.reshape(4, 2)
    errors = np.linalg.norm(mapped - expected, axis=1)
    result = VerificationResult(
        camera_points=camera_points,
        mapped_points=mapped,
        errors=errors,
        mask=mask,
    )
    if result.max_error > max_error_px:
        raise RuntimeError(
            f"Eckprüfung zu ungenau – größte Abweichung {result.max_error:.0f} px"
        )
    return result


def refine_homography_from_precision_markers(
    dark_frames: Iterable[np.ndarray],
    marker_frames: Iterable[np.ndarray],
    initial_homography: np.ndarray,
    expected_screen_points: Iterable[tuple[int, int]],
    max_match_distance_px: float = 80.0,
    minimum_matches: int = 8,
) -> PrecisionAlignmentResult:
    """Verfeinert die Abbildung mit Markern entlang des gesamten Leinwandrands.

    Die vorhandene Vierpunkt-Homographie dient nur zum Zuordnen der Marker. Die
    endgültige Matrix wird aus allen gefundenen Punkten robust neu berechnet.
    Damit beeinflusst nicht mehr nur je ein kleiner Punkt in jeder Ecke das
    Ergebnis; auch obere, untere und seitliche Kante fließen in die Ausrichtung
    ein.
    """

    dark = _average_frames(dark_frames)
    markers = _average_frames(marker_frames)
    if dark.shape != markers.shape:
        raise ValueError("Referenz- und Rahmenbilder haben unterschiedliche Größen")

    difference = cv2.cvtColor(cv2.absdiff(markers, dark), cv2.COLOR_BGR2GRAY)
    difference = cv2.GaussianBlur(difference, (3, 3), 0)
    otsu, mask = cv2.threshold(difference, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, mask = cv2.threshold(difference, max(18, int(otsu)), 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        iterations=1,
    )

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    frame_area = float(mask.shape[0] * mask.shape[1])
    camera_candidates: list[tuple[float, float]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < 5.0 or area > frame_area * 0.012:
            continue
        x, y, width, height = cv2.boundingRect(contour)
        if min(width, height) < 3 or max(width, height) / max(1, min(width, height)) > 1.8:
            continue
        perimeter = cv2.arcLength(contour, True)
        circularity = 4.0 * np.pi * area / max(1.0, perimeter * perimeter)
        if circularity < 0.45:
            continue
        moments = cv2.moments(contour)
        if moments["m00"]:
            camera_candidates.append(
                (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])
            )

    if len(camera_candidates) < minimum_matches:
        raise RuntimeError(
            f"Rahmenprüfung unvollständig – nur {len(camera_candidates)} Marker erkannt"
        )

    expected = np.asarray(list(expected_screen_points), dtype=np.float32).reshape(-1, 2)
    camera = np.asarray(camera_candidates, dtype=np.float32)
    mapped = cv2.perspectiveTransform(camera.reshape(-1, 1, 2), initial_homography).reshape(-1, 2)

    matches: list[tuple[float, int, int]] = []
    for camera_index, mapped_point in enumerate(mapped):
        distances = np.linalg.norm(expected - mapped_point, axis=1)
        expected_index = int(np.argmin(distances))
        distance = float(distances[expected_index])
        if distance <= max_match_distance_px:
            matches.append((distance, camera_index, expected_index))

    # Ein Marker darf höchstens einmal verwendet werden. Die kleinste Distanz
    # gewinnt, falls Reflexe einen zusätzlichen Kandidaten erzeugen.
    selected: list[tuple[int, int]] = []
    used_camera: set[int] = set()
    used_expected: set[int] = set()
    for _, camera_index, expected_index in sorted(matches):
        if camera_index in used_camera or expected_index in used_expected:
            continue
        used_camera.add(camera_index)
        used_expected.add(expected_index)
        selected.append((camera_index, expected_index))

    if len(selected) < minimum_matches:
        raise RuntimeError(
            f"Rahmenprüfung unvollständig – nur {len(selected)}/{len(expected)} Marker zugeordnet"
        )

    matched_camera = np.asarray([camera[c] for c, _ in selected], dtype=np.float32)
    matched_screen = np.asarray([expected[e] for _, e in selected], dtype=np.float32)
    refined, inliers = cv2.findHomography(
        matched_camera,
        matched_screen,
        method=cv2.RANSAC,
        ransacReprojThreshold=8.0,
    )
    if refined is None:
        raise RuntimeError("Rahmenprüfung konnte keine präzise Abbildung berechnen")
    if inliers is not None:
        keep = inliers.reshape(-1).astype(bool)
        matched_camera = matched_camera[keep]
        matched_screen = matched_screen[keep]
    if len(matched_camera) < minimum_matches:
        raise RuntimeError(
            f"Rahmenprüfung instabil – nur {len(matched_camera)} zuverlässige Marker"
        )

    remapped = cv2.perspectiveTransform(
        matched_camera.reshape(-1, 1, 2), refined
    ).reshape(-1, 2)
    errors = np.linalg.norm(remapped - matched_screen, axis=1)
    if float(errors.max()) > 28.0:
        raise RuntimeError(
            f"Rahmenprüfung zu ungenau – größte Abweichung {float(errors.max()):.0f} px"
        )
    return PrecisionAlignmentResult(
        homography=refined.astype(np.float64),
        camera_points=matched_camera,
        screen_points=matched_screen,
        errors=errors,
        mask=mask,
    )
