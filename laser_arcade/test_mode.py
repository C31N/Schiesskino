from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import cv2
import pygame

from .calibration import apply_homography
from .config import Settings, save_settings
from .laser_tracker import LaserDetection
from .ui import Button, make_font

LOGGER = logging.getLogger(__name__)


@dataclass
class CameraOption:
    index: int
    path: str
    available: bool
    width: int
    height: int
    fps: int

    def label(self) -> str:
        if not self.available:
            return f"/dev/video{self.index} (nicht verfügbar)"
        return f"/dev/video{self.index} ({self.width}x{self.height} @ {self.fps}fps)"


class TestMode:
    name = "Testmodus"

    def __init__(
        self,
        screen: pygame.Surface,
        settings: Settings,
        homography,
        last_point_provider,
        on_camera_change: Optional[Callable[[], Tuple[bool, Optional[str]]]] = None,
    ):
        self.screen = screen
        self.font = make_font(26)
        self.info_font = make_font(20)
        self.button_font = make_font(18)
        self.settings = settings
        self.homography = homography
        self.last_point_provider = last_point_provider
        self.last_mapped = None
        self.last_detection: Optional[LaserDetection] = None
        self.on_camera_change = on_camera_change
        self.camera_ok = True
        self.camera_error: Optional[str] = None

        self.camera_options: List[CameraOption] = self._discover_cameras()
        self.selected_option = self._find_selected_option()
        self.camera_buttons: List[Button] = []
        self.apply_button: Optional[Button] = None
        self.status_message: Optional[str] = None
        self._build_buttons()

    def set_detection(self, detection: Optional[LaserDetection]) -> None:
        self.last_detection = detection

    def set_camera_status(self, ok: bool, message: Optional[str]) -> None:
        self.camera_ok = ok
        self.camera_error = message

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "move":
            self.last_mapped = pos
            return
        if event_type == "click":
            for btn in self.camera_buttons:
                if btn.contains(pos):
                    btn.action()
                    return
            if self.apply_button and self.apply_button.contains(pos):
                self.apply_button.action()

    def update(self, dt: float) -> None:
        return

    def _discover_cameras(self) -> List[CameraOption]:
        options: List[CameraOption] = []
        for path in sorted(Path("/dev").glob("video*")):
            try:
                index = int(path.name.replace("video", ""))
            except ValueError:
                continue
            options.append(self._probe_camera(index, str(path)))
        if not options:
            # Fallback to a small default range if /dev/video* is empty
            for index in range(3):
                options.append(self._probe_camera(index, f"/dev/video{index}"))
        return options

    def _probe_camera(self, index: int, path: str) -> CameraOption:
        cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        available = cap.isOpened()
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if available else 0
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if available else 0
        fps = int(cap.get(cv2.CAP_PROP_FPS)) if available else 0
        if cap is not None:
            cap.release()
        return CameraOption(
            index=index,
            path=path,
            available=available,
            width=width or self.settings.camera.width,
            height=height or self.settings.camera.height,
            fps=fps or self.settings.camera.fps,
        )

    def _find_selected_option(self) -> Optional[CameraOption]:
        for opt in self.camera_options:
            if opt.index == self.settings.camera.device_index:
                return opt
        return self.camera_options[0] if self.camera_options else None

    def _build_buttons(self) -> None:
        self.camera_buttons = []
        panel_width = 380
        x = self.screen.get_width() - panel_width - 20
        y = 100
        spacing = 10
        button_height = 46
        for idx, opt in enumerate(self.camera_options):
            rect = pygame.Rect(x, y + idx * (button_height + spacing), panel_width, button_height)
            bg_color = (60, 120, 200) if opt.available else (90, 90, 90)
            if self.selected_option and opt.index == self.selected_option.index:
                bg_color = (50, 150, 90)
            self.camera_buttons.append(
                Button(
                    rect=rect,
                    label=opt.label(),
                    action=lambda o=opt: self._select_camera(o),
                    font=self.button_font,
                    bg_color=bg_color,
                )
            )
        apply_y = y + max(len(self.camera_options), 1) * (button_height + spacing) + 10
        self.apply_button = Button(
            rect=pygame.Rect(x, apply_y, panel_width, 50),
            label="Übernehmen/Speichern",
            action=self._apply_selection,
            font=self.button_font,
            bg_color=(40, 160, 80),
        )

    def _select_camera(self, option: CameraOption) -> None:
        self.selected_option = option
        self.status_message = None
        self._build_buttons()

    def _apply_selection(self) -> None:
        if not self.selected_option:
            self.status_message = "Kein Gerät ausgewählt."
            return
        if not self.selected_option.available:
            self.status_message = "Gerät nicht verfügbar."
            self.camera_ok = False
            self.camera_error = "Gerät nicht verfügbar."
            return
        cam_cfg = self.settings.camera
        cam_cfg.device_index = self.selected_option.index
        cam_cfg.width = self.selected_option.width
        cam_cfg.height = self.selected_option.height
        cam_cfg.fps = self.selected_option.fps
        save_settings(self.settings)
        if self.on_camera_change:
            ok, message = self.on_camera_change()
            self.camera_ok = ok
            self.camera_error = message
        self.status_message = "Gespeichert und Kamera neu gestartet." if self.camera_ok else self.camera_error

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
            f"Aktiv: /dev/video{camera.device_index} @ {camera.width}x{camera.height} {camera.fps}fps",
            "Nutze das Bild zum Ausrichten und zur Belichtung (z.B. v4l2-ctl).",
        ]
        for idx, line in enumerate(info_lines):
            info = self.info_font.render(line, True, (230, 230, 230))
            self.screen.blit(info, (20, 420 + idx * 28))

        self._draw_camera_panel()

    def _draw_camera_panel(self) -> None:
        panel_width = 380
        x = self.screen.get_width() - panel_width - 20
        panel_rect = pygame.Rect(x, 40, panel_width, self.screen.get_height() - 80)
        pygame.draw.rect(self.screen, (28, 28, 40), panel_rect, border_radius=10)
        pygame.draw.rect(self.screen, (80, 80, 120), panel_rect, 2, border_radius=10)

        title = self.font.render("Kamera-Auswahl", True, (255, 255, 255))
        self.screen.blit(title, (panel_rect.x + 16, panel_rect.y + 14))

        hint = self.info_font.render("Standard: Logitech C922 PRO", True, (210, 210, 210))
        self.screen.blit(hint, (panel_rect.x + 16, panel_rect.y + 46))

        status_y = panel_rect.y + 74
        status_lines = [
            f"Gewählt: {self.selected_option.label() if self.selected_option else '—'}",
        ]
        for idx, line in enumerate(status_lines):
            surf = self.info_font.render(line, True, (220, 220, 220))
            self.screen.blit(surf, (panel_rect.x + 16, status_y + idx * 22))

        if not self.camera_ok:
            error_txt = self.info_font.render(
                self.camera_error or "Gerät nicht verfügbar.", True, (240, 120, 120)
            )
            self.screen.blit(error_txt, (panel_rect.x + 16, status_y + 44))
        elif self.status_message:
            status_txt = self.info_font.render(self.status_message, True, (140, 220, 140))
            self.screen.blit(status_txt, (panel_rect.x + 16, status_y + 44))

        for btn in self.camera_buttons:
            btn.draw(self.screen)
        if self.apply_button:
            self.apply_button.draw(self.screen)
