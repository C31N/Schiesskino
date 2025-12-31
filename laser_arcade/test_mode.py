from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from math import gcd
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


@dataclass
class ResolutionOption:
    width: int
    height: int

    def label(self) -> str:
        divisor = gcd(self.width, self.height)
        ratio = f"{self.width // divisor}:{self.height // divisor}" if divisor else "-"
        return f"{self.width}x{self.height} ({ratio})"


@dataclass
class CameraFormatOption:
    width: int
    height: int
    fps: int

    def label(self) -> str:
        divisor = gcd(self.width, self.height)
        ratio = f"{self.width // divisor}:{self.height // divisor}" if divisor else "-"
        return f"{self.width}x{self.height} @ {self.fps}fps ({ratio})"


class TestMode:
    name = "Testmodus"

    def __init__(
        self,
        screen: pygame.Surface,
        settings: Settings,
        homography,
        last_point_provider,
        on_camera_change: Optional[Callable[[], Tuple[bool, Optional[str]]]] = None,
        on_resolution_change: Optional[Callable[[], Optional[str]]] = None,
    ):
        self.screen = screen
        self.font = make_font(22)
        self.info_font = make_font(17)
        self.button_font = make_font(16)
        self.settings = settings
        self.homography = homography
        self.last_point_provider = last_point_provider
        self.last_mapped = None
        self.last_detection: Optional[LaserDetection] = None
        self.on_camera_change = on_camera_change
        self.on_resolution_change = on_resolution_change
        self.camera_ok = True
        self.camera_error: Optional[str] = None

        self.camera_options: List[CameraOption] = self._discover_cameras()
        self.selected_option = self._find_selected_option()
        self.format_options: List[CameraFormatOption] = self._build_format_options()
        self.selected_format = self._find_selected_format()
        self.resolution_options: List[ResolutionOption] = [
            ResolutionOption(1024, 768),
            ResolutionOption(1280, 720),
            ResolutionOption(1600, 900),
            ResolutionOption(1920, 1080),
        ]
        self.selected_resolution = self._find_selected_resolution()
        self.camera_buttons: List[Button] = []
        self.format_buttons: List[Button] = []
        self.resolution_buttons: List[Button] = []
        self.apply_button: Optional[Button] = None
        self.reload_button: Optional[Button] = None
        self.status_message: Optional[str] = None
        self.last_frame_preview = None
        self.last_mask_preview = None
        self._build_buttons()

    def set_detection(self, detection: Optional[LaserDetection]) -> None:
        self.last_detection = detection
        if detection:
            if detection.frame_preview is not None:
                self.last_frame_preview = detection.frame_preview
            if detection.mask_preview is not None:
                self.last_mask_preview = detection.mask_preview

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
                return
            for btn in self.format_buttons:
                if btn.contains(pos):
                    btn.action()
                    return
            for btn in self.resolution_buttons:
                if btn.contains(pos):
                    btn.action()
                    return
            if self.reload_button and self.reload_button.contains(pos):
                self.reload_button.action()

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

    @contextmanager
    def _silence_opencv_logs(self):
        """Suppress noisy OpenCV warnings while probing unavailable cameras."""
        try:
            get_level = cv2.utils.logging.getLogLevel
            set_level = cv2.utils.logging.setLogLevel
            original_level = get_level()
            set_level(cv2.utils.logging.LOG_LEVEL_ERROR)
            try:
                yield
            finally:
                set_level(original_level)
        except AttributeError:
            # Older OpenCV builds might not have utils.logging
            yield

    def _probe_camera(self, index: int, path: str) -> CameraOption:
        with self._silence_opencv_logs():
            cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
            available = cap.isOpened()
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if available else 0
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if available else 0
            fps = int(cap.get(cv2.CAP_PROP_FPS)) if available else 0
            if cap is not None:
                cap.release()
        width = width if width > 0 else self.settings.camera.width
        height = height if height > 0 else self.settings.camera.height
        fps = fps if fps > 0 else self.settings.camera.fps
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

    def _build_format_options(self) -> List[CameraFormatOption]:
        defaults = [
            CameraFormatOption(640, 480, 30),
            CameraFormatOption(800, 600, 30),
            CameraFormatOption(1024, 768, 30),
            CameraFormatOption(1280, 720, 30),
        ]
        # Ensure current settings are always selectable
        current = CameraFormatOption(
            self.settings.camera.width,
            self.settings.camera.height,
            self.settings.camera.fps,
        )
        options = { (opt.width, opt.height, opt.fps): opt for opt in defaults }
        options[(current.width, current.height, current.fps)] = current
        return list(options.values())

    def _find_selected_format(self) -> Optional[CameraFormatOption]:
        for opt in self.format_options:
            if (
                opt.width == self.settings.camera.width
                and opt.height == self.settings.camera.height
                and opt.fps == self.settings.camera.fps
            ):
                return opt
        return self.format_options[0] if self.format_options else None

    def _find_selected_resolution(self) -> Optional[ResolutionOption]:
        for opt in self.resolution_options:
            if opt.width == self.settings.screen_width and opt.height == self.settings.screen_height:
                return opt
        return self.resolution_options[0] if self.resolution_options else None

    def _panel_metrics(self) -> dict:
        panel_width = 260
        panel_margin = 14
        panel_top = 32
        panel_height = self.screen.get_height() - 64
        panel_x = self.screen.get_width() - panel_width - panel_margin
        panel_rect = pygame.Rect(panel_x, panel_top, panel_width, panel_height)
        title_y = panel_rect.y + 10
        hint_y = title_y + self.font.get_linesize() + 4
        status_y = hint_y + self.info_font.get_linesize() + 6
        status_line_height = self.info_font.get_linesize() + 2
        camera_section_y = status_y + status_line_height * 3 + 12
        camera_buttons_top = camera_section_y + self.info_font.get_linesize() + 4
        inner_padding = 10
        button_width = panel_width - inner_padding * 2

        return {
            "panel_rect": panel_rect,
            "panel_width": panel_width,
            "status_y": status_y,
            "camera_section_y": camera_section_y,
            "camera_buttons_top": camera_buttons_top,
            "button_x": panel_rect.x + inner_padding,
            "button_width": button_width,
        }

    def _build_buttons(self) -> None:
        self.camera_buttons = []
        self.format_buttons = []
        self.resolution_buttons = []
        metrics = self._panel_metrics()
        panel_width = metrics["button_width"]
        x = metrics["button_x"]
        y = metrics["camera_buttons_top"]
        spacing = 8
        button_height = 38
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
        apply_y = y + max(len(self.camera_options), 1) * (button_height + spacing) + 4
        self.apply_button = Button(
            rect=pygame.Rect(x, apply_y, panel_width, 44),
            label="Übernehmen/Speichern",
            action=self._apply_selection,
            font=self.button_font,
            bg_color=(40, 160, 80),
        )

        format_section_y = apply_y + self.apply_button.rect.height + 12
        format_button_height = 34
        for idx, opt in enumerate(self.format_options):
            rect = pygame.Rect(
                x,
                format_section_y + idx * (format_button_height + spacing),
                panel_width,
                format_button_height,
            )
            bg_color = (100, 100, 150)
            if self.selected_format and (opt.width, opt.height, opt.fps) == (
                self.selected_format.width,
                self.selected_format.height,
                self.selected_format.fps,
            ):
                bg_color = (80, 140, 200)
            self.format_buttons.append(
                Button(
                    rect=rect,
                    label=opt.label(),
                    action=lambda o=opt: self._select_format(o),
                    font=self.button_font,
                    bg_color=bg_color,
                )
            )

        res_section_y = format_section_y + max(len(self.format_options), 1) * (format_button_height + spacing) + 16
        res_button_height = 36
        for idx, opt in enumerate(self.resolution_options):
            rect = pygame.Rect(x, res_section_y + idx * (res_button_height + spacing), panel_width, res_button_height)
            bg_color = (90, 110, 160)
            if self.selected_resolution and (opt.width, opt.height) == (
                self.selected_resolution.width,
                self.selected_resolution.height,
            ):
                bg_color = (110, 150, 210)
            self.resolution_buttons.append(
                Button(
                    rect=rect,
                    label=opt.label(),
                    action=lambda o=opt: self._select_resolution(o),
                    font=self.button_font,
                    bg_color=bg_color,
                )
            )

        reload_y = res_section_y + max(len(self.resolution_options), 1) * (res_button_height + spacing) + 4
        self.reload_button = Button(
            rect=pygame.Rect(x, reload_y, panel_width, 44),
            label="Übernehmen/Neu laden",
            action=self._apply_resolution,
            font=self.button_font,
            bg_color=(70, 170, 110),
        )

    def _select_camera(self, option: CameraOption) -> None:
        self.selected_option = option
        self.status_message = None
        self._build_buttons()

    def _select_format(self, option: CameraFormatOption) -> None:
        self.selected_format = option
        self.status_message = None
        self._build_buttons()

    def _select_resolution(self, option: ResolutionOption) -> None:
        self.selected_resolution = option
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
        if self.selected_format:
            cam_cfg.width = max(1, self.selected_format.width)
            cam_cfg.height = max(1, self.selected_format.height)
            cam_cfg.fps = max(1, self.selected_format.fps)
        else:
            cam_cfg.width = max(1, self.selected_option.width)
            cam_cfg.height = max(1, self.selected_option.height)
            cam_cfg.fps = max(1, self.selected_option.fps)
        save_settings(self.settings)
        if self.on_camera_change:
            ok, message = self.on_camera_change()
            self.camera_ok = ok
            self.camera_error = message
        self.status_message = "Gespeichert und Kamera neu gestartet." if self.camera_ok else self.camera_error

    def _apply_resolution(self) -> None:
        if not self.selected_resolution:
            self.status_message = "Keine Auflösung ausgewählt."
            return
        self.settings.screen_width = self.selected_resolution.width
        self.settings.screen_height = self.selected_resolution.height
        save_settings(self.settings)
        try:
            reload_message = self.on_resolution_change() if self.on_resolution_change else None
            self.status_message = reload_message or "Auflösung übernommen."
        except Exception:
            LOGGER.exception("Konnte Anzeige nicht neu initialisieren")
            self.status_message = "Fehler beim Neuaufbau der Anzeige."

    def draw(self) -> None:
        self.screen.fill((15, 10, 15))
        detection = self.last_detection
        laser_pos = detection.point if detection and detection.point else self.last_point_provider()
        mapped = apply_homography(self.homography, laser_pos) if (self.homography is not None and laser_pos) else None

        preview_rect = None
        frame_preview = None
        if detection and detection.frame_preview is not None:
            frame_preview = detection.frame_preview
        elif self.last_frame_preview is not None:
            frame_preview = self.last_frame_preview

        if frame_preview is not None:
            try:
                surf = pygame.image.frombuffer(
                    frame_preview.tobytes(),
                    frame_preview.shape[1::-1],
                    "RGB",
                )
                preview_rect = surf.get_rect(topleft=(20, 80))
                pygame.draw.rect(self.screen, (220, 220, 220), preview_rect.inflate(6, 6), 1)
                self.screen.blit(surf, preview_rect)
            except Exception:
                LOGGER.debug("Konnte Kamera-Preview nicht anzeigen", exc_info=True)

        mask_preview = None
        if detection and detection.mask_preview is not None:
            mask_preview = detection.mask_preview
        elif self.last_mask_preview is not None:
            mask_preview = self.last_mask_preview

        if mask_preview is not None:
            try:
                surf = pygame.image.frombuffer(
                    mask_preview.tobytes(),
                    mask_preview.shape[1::-1],
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

        mode_text = self.info_font.render(f"Aktiver Modus: {self.name}", True, (210, 210, 230))
        self.screen.blit(mode_text, (20, 52))

        txt = f"Laser: {laser_pos} | Mapped: {mapped}"
        text = self.font.render(txt, True, (255, 255, 255))
        self.screen.blit(text, (20, 20))

        camera = self.settings.camera
        info_lines = [
            "Kamera-Preview (USB Logitech C922 PRO empfohlen)",
            f"Aktiv: /dev/video{camera.device_index} @ {camera.width}x{camera.height} {camera.fps}fps",
            f"Auflösung: {self.settings.screen_width}x{self.settings.screen_height}",
            "Einstellungen werden nach Auswahl gespeichert und Kamera neu gestartet.",
            "Nutze das Bild zum Ausrichten und zur Belichtung (z.B. v4l2-ctl).",
        ]
        for idx, line in enumerate(info_lines):
            info = self.info_font.render(line, True, (230, 230, 230))
            self.screen.blit(info, (20, 420 + idx * 28))

        self._draw_camera_panel()

    def _draw_camera_panel(self) -> None:
        metrics = self._panel_metrics()
        panel_rect = metrics["panel_rect"]
        pygame.draw.rect(self.screen, (28, 28, 40), panel_rect, border_radius=10)
        pygame.draw.rect(self.screen, (80, 80, 120), panel_rect, 2, border_radius=10)

        title = self.font.render("Kamera- & Anzeige", True, (255, 255, 255))
        self.screen.blit(title, (panel_rect.x + 12, panel_rect.y + 10))

        hint = self.info_font.render("Standard: Logitech C922 PRO", True, (210, 210, 210))
        self.screen.blit(hint, (panel_rect.x + 12, panel_rect.y + 32))

        status_y = metrics["status_y"]
        status_lines = [
            f"Kamera: {self.selected_option.label() if self.selected_option else '—'}",
            f"Format: {self.selected_format.label() if self.selected_format else '—'}",
            f"Anzeige: {self.selected_resolution.label() if self.selected_resolution else '—'}",
        ]
        for idx, line in enumerate(status_lines):
            surf = self.info_font.render(line, True, (220, 220, 220))
            self.screen.blit(surf, (panel_rect.x + 12, status_y + idx * 20))

        if not self.camera_ok:
            error_txt = self.info_font.render(
                self.camera_error or "Gerät nicht verfügbar.", True, (240, 120, 120)
            )
            self.screen.blit(error_txt, (panel_rect.x + 12, status_y + 44))
        elif self.status_message:
            status_txt = self.info_font.render(self.status_message, True, (140, 220, 140))
            self.screen.blit(status_txt, (panel_rect.x + 12, status_y + 44))

        camera_section_y = metrics["camera_section_y"]
        cam_title = self.info_font.render("Kamera-Geräte", True, (230, 230, 250))
        self.screen.blit(cam_title, (panel_rect.x + 12, camera_section_y))

        for btn in self.camera_buttons:
            btn.draw(self.screen)
        if self.apply_button:
            self.apply_button.draw(self.screen)

        format_section_y = self.apply_button.rect.bottom + 12 if self.apply_button else camera_section_y + 80
        format_title = self.info_font.render("Kamera-Format", True, (230, 230, 250))
        self.screen.blit(format_title, (panel_rect.x + 12, format_section_y))

        for btn in self.format_buttons:
            btn.draw(self.screen)

        res_section_y = (
            self.format_buttons[-1].rect.bottom + 16
            if self.format_buttons
            else format_section_y + 80
        )
        res_title = self.info_font.render("Anzeige-Auflösung", True, (230, 230, 250))
        self.screen.blit(res_title, (panel_rect.x + 12, res_section_y))

        for btn in self.resolution_buttons:
            btn.draw(self.screen)
        if self.reload_button:
            self.reload_button.draw(self.screen)

    def update_context(self, screen: pygame.Surface, homography) -> None:
        self.screen = screen
        self.homography = homography
        self._build_buttons()
