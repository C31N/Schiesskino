from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np


@dataclass
class DetectionConfig:
    """Parameter für einen kurzen sichtbaren 620-nm-Laserimpuls."""

    hue_max: int = 25
    hue_min_high: int = 165
    min_saturation: int = 12
    min_value: int = 60
    min_red_excess: int = 8
    min_frame_delta: int = 5
    min_area: float = 1.0
    max_area: float = 1400.0
    morph_kernel: int = 3
    debounce_ms: int = 160
    background_alpha: float = 0.035
    strict_temporal: bool = False


@dataclass
class PulseResult:
    point: Optional[Tuple[int, int]]
    area: float
    confidence: float
    shot: bool
    mask: np.ndarray
    peak_red_excess: int
    peak_delta: int
    red_threshold: int
    delta_threshold: int
    observed_point: Optional[Tuple[int, int]] = None
    observed_area: float = 0.0
    observed_peak_red: int = 0
    observed_peak_delta: int = 0
    observed_peak_value: int = 0


class PulseShotDetector:
    """Erkennt die steigende Flanke eines kurzen roten Laserimpulses.

    Ein statischer HSV-Filter allein verwechselt rote Projektorflächen mit dem
    Laser. Deshalb muss ein Kandidat gleichzeitig rot, hell und gegenüber dem
    laufenden Hintergrund neu erschienen sein.
    """

    MIN_SHOT_CONFIDENCE = 0.42
    ILLUMINATION_SETTLE_MS = 420.0

    def __init__(self, config: DetectionConfig):
        self.config = config
        self.signature_filter: Optional[Tuple[int, int, int]] = None
        self.background: Optional[np.ndarray] = None
        self.previous_frame: Optional[np.ndarray] = None
        self.previous_active = False
        self.armed = True
        self.rearm_floor_red = 0
        self.last_shot_peak_red = 0
        self.last_shot_point: Optional[Tuple[int, int]] = None
        self.last_shot_ms = -1e12
        self.illumination_hold_until_ms = -1e12

    def reset(self) -> None:
        self.background = None
        self.previous_frame = None
        self.previous_active = False
        self.armed = True
        self.rearm_floor_red = 0
        self.last_shot_peak_red = 0
        self.last_shot_point = None
        self.last_shot_ms = -1e12
        self.illumination_hold_until_ms = -1e12

    def set_signature_filter(
        self,
        enabled: bool,
        *,
        red_excess: int = 150,
        fallback_red_excess: int = 55,
        fallback_delta: int = 125,
    ) -> None:
        """Filtert bekannte rote Bildanimationen vor der Flankenerkennung."""

        self.signature_filter = (
            (red_excess, fallback_red_excess, fallback_delta) if enabled else None
        )
        self.reset()

    def process(
        self,
        frame_bgr: np.ndarray,
        now_ms: float,
        region_mask: Optional[np.ndarray] = None,
    ) -> PulseResult:
        if self.background is None or self.previous_frame is None:
            self.background = frame_bgr.astype(np.float32)
            self.previous_frame = frame_bgr.copy()
            return self._empty(frame_bgr.shape[:2])

        background_u8 = cv2.convertScaleAbs(self.background)
        previous_u8 = self.previous_frame
        # Ein Treffer ist eine kurze Flanke gegenüber dem direkt vorherigen
        # Kamerabild. Dadurch erhöhen stehenbleibende Trefferkreuze, helle
        # Dosenflächen oder andere Spielgrafiken den Änderungs-Schwellwert
        # nicht über viele Folgebilder hinweg.
        delta = cv2.absdiff(frame_bgr, previous_u8)
        delta_max = delta.max(axis=2)

        blue, green, red = cv2.split(frame_bgr)
        other_max = np.maximum(blue, green).astype(np.int16)
        red_excess = red.astype(np.int16) - other_max
        previous_blue, previous_green, previous_red = cv2.split(previous_u8)
        previous_other_max = np.maximum(previous_blue, previous_green).astype(np.int16)
        previous_red_excess = previous_red.astype(np.int16) - previous_other_max
        blue_rise = blue.astype(np.int16) - previous_blue.astype(np.int16)
        green_rise = green.astype(np.int16) - previous_green.astype(np.int16)
        red_rise = red.astype(np.int16) - previous_red.astype(np.int16)
        relative_red_rise = red_excess - previous_red_excess
        previous_value = np.maximum(
            previous_red,
            np.maximum(previous_blue, previous_green),
        )
        red_headroom = np.maximum(4, 255 - previous_red.astype(np.int16))
        normalized_red_rise = np.clip(
            np.maximum(red_rise, 0).astype(np.float32) * 255.0 / red_headroom,
            0.0,
            255.0,
        ).astype(np.int16)
        background_blue, background_green, background_red = cv2.split(background_u8)
        background_other_max = np.maximum(
            background_blue, background_green
        ).astype(np.int16)
        background_red_excess = (
            background_red.astype(np.int16) - background_other_max
        )
        background_red_rise = red.astype(np.int16) - background_red.astype(np.int16)
        background_relative_red_rise = red_excess - background_red_excess
        background_value = np.maximum(
            background_red,
            np.maximum(background_blue, background_green),
        )
        background_red_headroom = np.maximum(
            4, 255 - background_red.astype(np.int16)
        )
        background_normalized_red_rise = np.clip(
            np.maximum(background_red_rise, 0).astype(np.float32)
            * 255.0
            / background_red_headroom,
            0.0,
            255.0,
        ).astype(np.int16)
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)

        cfg = self.config
        if region_mask is not None and region_mask.shape == red_excess.shape:
            valid_region = region_mask.astype(bool)
            if int(valid_region.sum()) < 100:
                valid_region = np.ones(red_excess.shape, dtype=bool)
        else:
            valid_region = np.ones(red_excess.shape, dtype=bool)

        # Ein eingeschaltetes Raumlicht, eine Belichtungsnachregelung oder
        # Netzlichtflimmern verändert viele Kamerapixel gleichzeitig und darf
        # nie wie ein punktförmiger Laserimpuls behandelt werden. Entscheidend
        # ist dabei die räumliche Ausdehnung und gleiche Änderungsrichtung;
        # ein echter Laser belegt nur einen winzigen Bruchteil der Leinwand.
        current_luma = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY).astype(np.int16)
        previous_luma = cv2.cvtColor(previous_u8, cv2.COLOR_BGR2GRAY).astype(np.int16)
        luma_change = current_luma - previous_luma
        region_change = luma_change[valid_region]
        substantial = np.abs(region_change) >= 10
        changed_fraction = float(np.mean(substantial)) if region_change.size else 0.0
        global_illumination_change = False
        if bool(substantial.any()):
            substantial_change = region_change[substantial]
            same_direction = max(
                float(np.mean(substantial_change > 0)),
                float(np.mean(substantial_change < 0)),
            )
            median_shift = abs(float(np.median(region_change)))
            global_illumination_change = (
                changed_fraction >= 0.18
                and same_direction >= 0.78
                and (median_shift >= 5.0 or changed_fraction >= 0.42)
            )
        if global_illumination_change:
            self.illumination_hold_until_ms = max(
                self.illumination_hold_until_ms,
                now_ms + self.ILLUMINATION_SETTLE_MS,
            )
        illumination_settling = now_ms < self.illumination_hold_until_ms

        # Sonnenlicht und Kamerarauschen verändern die Rohwerte erheblich.
        # Deshalb liegen die Grenzwerte automatisch oberhalb des gemessenen
        # 99,7-%-Rauschpegels innerhalb der Leinwand.
        red_noise = max(
            0.0,
            float(np.percentile(red_excess[valid_region], 99.7)),
        )
        delta_noise = max(
            0.0,
            float(np.percentile(delta_max[valid_region], 99.7)),
        )
        red_threshold = max(cfg.min_red_excess, int(round(red_noise + 6.0)))
        delta_threshold = max(cfg.min_frame_delta, int(round(delta_noise + 4.0)))
        temporal_red_noise = max(
            0.0,
            float(np.percentile(relative_red_rise[valid_region], 99.7)),
        )
        temporal_red_threshold = max(3, int(round(temporal_red_noise + 2.0)))
        # Netzlicht- und Rolling-Shutter-Flimmern lässt an roten/hellen Kanten
        # häufig Blau oder Grün abfallen. Der Laser dagegen erhöht gezielt den
        # Rotkanal. Bei einem schwachen Rotanstieg dürfen die anderen Kanäle
        # deshalb höchstens um ein Rauschquant fallen; ein kräftiger Anstieg
        # bleibt auch auf schwierigen Hintergründen zulässig.
        laser_red_rise = (
            (red_rise >= temporal_red_threshold)
            & (
                ((blue_rise >= -1) & (green_rise >= -1))
                | (red_rise >= 15)
            )
        )
        hue_ok = (hue <= cfg.hue_max) | (hue >= cfg.hue_min_high)
        classic_red = (
            hue_ok
            & (saturation >= cfg.min_saturation)
            & (red_excess >= red_threshold)
        )
        # Auf hellen Flächen sinkt die Sättigung, obwohl der Laser fast die
        # gesamte noch verfügbare Rotreserve nutzt. Der zeitliche Rotanstieg
        # darf deshalb die Sättigungsgrenze ersetzen, niemals aber die
        # Rotdominanz: Weißes oder andersfarbiges Aufblitzen ist kein Schuss.
        # Auf nahezu weißen Flächen darf die allgemeine Rotüberschuss-Schwelle
        # nicht proportional als zweite Hürde wirken. Dort ist nur wenig
        # statische Farbdifferenz möglich, während der normierte zeitliche
        # Rotanstieg den Laser weiterhin eindeutig beschreibt. Die Kappe hält
        # diese helle-Flächen-Regel auch bei strengem Profil erreichbar.
        minimum_visible_red = max(3, min(12, cfg.min_red_excess // 2))
        bright_temporal_red = (
            (previous_value >= 120)
            & (red_excess >= minimum_visible_red)
            & laser_red_rise
            & (relative_red_rise >= temporal_red_threshold)
            & (normalized_red_rise >= max(45, red_threshold + 6))
        )
        persistent_bright_red = (
            (background_value >= 120)
            & (red_excess >= minimum_visible_red)
            & (background_red_rise >= 3)
            & (background_relative_red_rise >= 3)
            & (background_normalized_red_rise >= 45)
        )
        background_delta_max = cv2.absdiff(frame_bgr, background_u8).max(axis=2)
        persistent_candidate = (
            valid_region
            & (value >= cfg.min_value)
            & (background_delta_max >= cfg.min_frame_delta)
            & (classic_red | persistent_bright_red)
        )
        candidate = (
            valid_region
            & (value >= cfg.min_value)
            & (delta_max >= delta_threshold)
            & ((classic_red & laser_red_rise) | bright_temporal_red)
        )
        if cfg.strict_temporal:
            # Ein roter optischer Vorsatz lässt auch weiße DLP-Teilbilder rot
            # erscheinen. In diesem Profil reicht deshalb selbst kräftige rote
            # Farbe nie allein: Es muss ein kompakter, steiler Laseranstieg sein.
            strict_rise = max(10, temporal_red_threshold)
            candidate &= (
                (red_rise >= strict_rise)
                & (relative_red_rise >= strict_rise)
                & (normalized_red_rise >= 72)
            )
        if self.signature_filter is not None:
            strong_red, fallback_red, strong_delta = self.signature_filter
            signature_ok = (red_excess >= strong_red) | (
                (red_excess >= fallback_red) & (delta_max >= strong_delta)
            )
            candidate &= signature_ok
        raw_mask = candidate.astype(np.uint8) * 255
        effective_red = np.maximum(
            np.clip(red_excess, 0, 255),
            np.where(relative_red_rise > 0, normalized_red_rise, 0),
        ).astype(np.int16)
        (
            observed_point,
            observed_area,
            observed_peak_red,
            observed_peak_delta,
            observed_peak_value,
        ) = self._measure_probe_peak(
            valid_region,
            value,
            delta_max,
            red_excess,
            relative_red_rise,
            normalized_red_rise,
            effective_red,
            laser_red_rise,
        )

        # Eine leichte Dilatation verbindet auch einen nur 1–2 Pixel großen
        # Punkt bzw. eine Rolling-Shutter-Spur, ohne ihn durch Opening zu löschen.
        kernel_size = max(1, int(cfg.morph_kernel))
        if kernel_size % 2 == 0:
            kernel_size += 1
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
        )
        mask = cv2.dilate(raw_mask, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_contour = None
        best_score = -1.0
        best_area = 0.0
        for contour in contours:
            area = cv2.contourArea(contour)
            if not (cfg.min_area <= area <= cfg.max_area):
                continue
            component_mask = np.zeros(mask.shape, dtype=np.uint8)
            cv2.drawContours(component_mask, [contour], -1, 255, thickness=-1)
            # Für die Position nur die echten Farbkandidaten verwenden. Die
            # Dilatation dient ausschließlich zum Verbinden winziger Spuren;
            # ihre künstlichen Randpixel dürfen den Treffpunkt nicht verschieben.
            component = component_mask.astype(bool) & candidate
            if not component.any():
                component = component_mask.astype(bool)
            peak_red = int(effective_red[component].max()) if component.any() else 0
            peak_change = int(delta_max[component].max()) if component.any() else 0
            score = peak_red * 1.5 + peak_change + min(area, 80.0)
            if score > best_score:
                best_score = score
                best_contour = contour
                best_area = area

        point: Optional[Tuple[int, int]] = None
        peak_red = 0
        peak_change = 0
        confidence = 0.0
        if best_contour is not None:
            component_mask = np.zeros(mask.shape, dtype=np.uint8)
            cv2.drawContours(component_mask, [best_contour], -1, 255, thickness=-1)
            component = component_mask.astype(bool) & candidate
            if not component.any():
                component = component_mask.astype(bool)
            ys, xs = np.nonzero(component)
            weights = np.maximum(effective_red[component], 1).astype(np.float64)
            if len(xs):
                point = (
                    int(round(float(np.average(xs, weights=weights)))),
                    int(round(float(np.average(ys, weights=weights)))),
                )
            peak_red = int(effective_red[component].max()) if component.any() else 0
            peak_change = int(delta_max[component].max()) if component.any() else 0
            color_score = min(1.0, peak_red / 110.0)
            change_score = min(1.0, peak_change / 100.0)
            area_score = min(1.0, best_area / 18.0)
            confidence = 0.45 * color_score + 0.4 * change_score + 0.15 * area_score

        active = point is not None
        state_active = active
        state_peak_red = peak_red
        if not self.armed and self.last_shot_point is not None:
            last_x, last_y = self.last_shot_point
            radius = 5
            y0 = max(0, last_y - radius)
            y1 = min(persistent_candidate.shape[0], last_y + radius + 1)
            x0 = max(0, last_x - radius)
            x1 = min(persistent_candidate.shape[1], last_x + radius + 1)
            local_presence = persistent_candidate[y0:y1, x0:x1]
            if local_presence.any():
                state_active = True
                persistent_effective_red = np.maximum(
                    np.clip(red_excess[y0:y1, x0:x1], 0, 255),
                    np.where(
                        background_relative_red_rise[y0:y1, x0:x1] > 0,
                        background_normalized_red_rise[y0:y1, x0:x1],
                        0,
                    ),
                )
                state_peak_red = max(
                    state_peak_red,
                    int(persistent_effective_red[local_presence].max()),
                )
        can_fire = now_ms - self.last_shot_ms >= cfg.debounce_ms
        if not state_active:
            self.armed = True
            self.rearm_floor_red = 0
        elif (
            not self.armed
            and self.last_shot_peak_red > 0
            and state_peak_red <= max(
                red_threshold,
                int(round(self.last_shot_peak_red * 0.58)),
            )
        ):
            # Ein schwacher Restpunkt kann nach einem Pistolenimpuls noch eine
            # oder zwei Kamerafolgen sichtbar bleiben. Der deutliche Abfall der
            # Rotenergie schärft den Detektor wieder, ohne einen dauerhaft
            # gehaltenen Laser als mehrere Schüsse zu zählen.
            self.armed = True
            self.rearm_floor_red = state_peak_red
        rising_pulse = (
            not self.previous_active
            or self.rearm_floor_red == 0
            or peak_red
            >= max(
                self.rearm_floor_red + 10,
                int(round(self.rearm_floor_red * 1.35)),
            )
        )
        # Einzelne flimmernde Sensor-/Projektorpixel liegen gelegentlich exakt
        # an beiden Untergrenzen. Ein echter Laser besitzt entweder deutlich
        # mehr Rotenergie, mehr Änderung oder eine größere kompakte Spur. Die
        # gemeinsame Konfidenz verhindert deshalb Grenzwert-Fehlauslösungen,
        # ohne schwache Laserpunkte auf hellen Flächen auszusortieren.
        shot = (
            active
            and self.armed
            and rising_pulse
            and can_fire
            and not illumination_settling
            and confidence >= self.MIN_SHOT_CONFIDENCE
        )
        if shot:
            self.last_shot_ms = now_ms
            self.last_shot_peak_red = peak_red
            self.last_shot_point = point
            self.armed = False
            self.rearm_floor_red = 0
        self.previous_active = state_active

        # Bei einem Kandidaten nur sehr langsam adaptieren, damit ein kurzer
        # Impuls nicht sofort in den Hintergrund eingerechnet wird.
        alpha = cfg.background_alpha * (0.12 if active else 1.0)
        cv2.accumulateWeighted(frame_bgr, self.background, alpha)
        self.previous_frame = frame_bgr.copy()

        return PulseResult(
            point=point,
            area=best_area,
            confidence=confidence,
            shot=shot,
            mask=mask,
            peak_red_excess=peak_red,
            peak_delta=peak_change,
            red_threshold=red_threshold,
            delta_threshold=delta_threshold,
            observed_point=observed_point,
            observed_area=observed_area,
            observed_peak_red=observed_peak_red,
            observed_peak_delta=observed_peak_delta,
            observed_peak_value=observed_peak_value,
        )

    @staticmethod
    def _measure_probe_peak(
        valid_region: np.ndarray,
        value: np.ndarray,
        delta_max: np.ndarray,
        red_excess: np.ndarray,
        relative_red_rise: np.ndarray,
        normalized_red_rise: np.ndarray,
        effective_red: np.ndarray,
        laser_red_rise: np.ndarray,
    ) -> tuple[Optional[Tuple[int, int]], float, int, int, int]:
        """Misst einen roten Impuls unabhängig von der eingestellten Schwelle.

        Der Messkanal wertet nichts als Schuss. Er liefert nur den stärksten
        kompakten roten Anstieg, damit die Einstellhilfe auch bei einer momentan
        zu strengen Erkennung aussagekräftige Peakwerte anzeigen kann.
        """

        probe = (
            valid_region
            & (value >= 20)
            & (delta_max >= 3)
            & (red_excess >= 1)
            & laser_red_rise
            & ((relative_red_rise >= 2) | (normalized_red_rise >= 24))
        )
        if not bool(probe.any()):
            return None, 0.0, 0, 0, 0
        strength = effective_red.astype(np.float32) * 1.5 + delta_max.astype(np.float32)
        strength = np.where(probe, strength, -1.0)
        flat_index = int(np.argmax(strength))
        y, x = np.unravel_index(flat_index, strength.shape)
        peak_red = int(max(0, effective_red[y, x]))
        peak_delta = int(delta_max[y, x])
        peak_value = int(value[y, x])
        support = (
            valid_region
            & (value >= 20)
            & (delta_max >= max(3, peak_delta // 3))
            & (effective_red >= max(2, peak_red // 3))
        ).astype(np.uint8)
        _, labels = cv2.connectedComponents(support, connectivity=8)
        component = int(labels[y, x])
        area = float(np.count_nonzero(labels == component)) if component else 1.0
        return (int(x), int(y)), area, peak_red, peak_delta, peak_value

    @staticmethod
    def _empty(shape: Tuple[int, int]) -> PulseResult:
        return PulseResult(
            point=None,
            area=0.0,
            confidence=0.0,
            shot=False,
            mask=np.zeros(shape, dtype=np.uint8),
            peak_red_excess=0,
            peak_delta=0,
            red_threshold=0,
            delta_threshold=0,
        )
