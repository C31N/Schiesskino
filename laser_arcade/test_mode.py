from __future__ import annotations

import logging
from typing import Optional, Tuple

import pygame

from .calibration import apply_homography
from .config import Settings
from .laser_tracker import LaserDetection
from .ui import make_font

LOGGER = logging.getLogger(__name__)


class TestMode:
    name = "Testmodus"

    def __init__(self, screen: pygame.Surface, settings: Settings, homography, last_point_provider):
        self.screen = screen
        self.font = make_font(26)
        self.info_font = make_font(20)
        self.settings = settings
        self.homography = homography
        self.last_point_provider = last_point_provider
        self.last_mapped = None
        self.last_detection: Optional[LaserDetection] = None

    def set_detection(self, detection: Optional[LaserDetection]) -> None:
        self.last_detection = detection

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "move":
            self.last_mapped = pos

    def update(self, dt: float) -> None:
        return

    def draw(self) -> None:
        self.screen.fill((15, 10, 15))
        detection = self.last_detection
        laser_pos = detection.point if detection and detection.point else self.last_point_provider()
        mapped = apply_homography(self.homography, laser_pos) if (self.homography is not None and laser_pos) else None

        preview_rect = None
        if detection and detection.frame_preview is not None:
            try:
                preview = detection.frame_preview
                surf = pygame.image.frombuffer(
                    preview.tobytes(),
                    preview.shape[1::-1],
                    "RGB",
                )
                preview_rect = surf.get_rect(topleft=(20, 80))
                pygame.draw.rect(self.screen, (220, 220, 220), preview_rect.inflate(6, 6), 1)
                self.screen.blit(surf, preview_rect)
            except Exception:
                LOGGER.debug("Konnte Kamera-Preview nicht anzeigen", exc_info=True)

        if detection and detection.mask_preview is not None:
            try:
                surf = pygame.image.frombuffer(
                    detection.mask_preview.tobytes(),
                    detection.mask_preview.shape[1::-1],
                    "RGB",
                )
                offset_x = preview_rect.right + 20 if preview_rect else 20
                mask_rect = surf.get_rect(topleft=(offset_x, 80))
                pygame.draw.rect(self.screen, (120, 180, 120), mask_rect.inflate(6, 6), 1)
                self.screen.blit(surf, mask_rect)
            except Exception:
                LOGGER.debug("Konnte Masken-Preview nicht anzeigen", exc_info=True)

        if laser_pos:
            pygame.draw.circle(self.screen, (255, 0, 0), laser_pos, 10)
        if mapped:
            pygame.draw.circle(self.screen, (0, 255, 0), mapped, 12, 3)
            cross = [
                (mapped[0] - 20, mapped[1]),
                (mapped[0] + 20, mapped[1]),
                (mapped[0], mapped[1] - 20),
                (mapped[0], mapped[1] + 20),
            ]
            pygame.draw.line(self.screen, (0, 255, 0), cross[0], cross[1], 2)
            pygame.draw.line(self.screen, (0, 255, 0), cross[2], cross[3], 2)

        txt = f"Laser: {laser_pos} | Mapped: {mapped}"
        text = self.font.render(txt, True, (255, 255, 255))
        self.screen.blit(text, (20, 20))

        camera = self.settings.camera
        info_lines = [
            "Kamera-Preview (USB Logitech C922 PRO empfohlen)",
            f"Gerät #{camera.device_index} @ {camera.width}x{camera.height} {camera.fps}fps",
            "Anpassen: ~/.laser_arcade/settings.json -> camera.device_index/width/height/fps",
            "Nutze das Bild zum Ausrichten und zur Belichtung (z.B. v4l2-ctl).",
        ]
        for idx, line in enumerate(info_lines):
            info = self.info_font.render(line, True, (230, 230, 230))
            self.screen.blit(info, (20, 420 + idx * 28))
