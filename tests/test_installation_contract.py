from __future__ import annotations

import configparser
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallationContractTest(unittest.TestCase):
    def test_legacy_installer_delegates_to_the_single_canonical_installer(self) -> None:
        legacy = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
        self.assertIn('exec "${SCRIPT_DIR}/../install.sh" "$@"', legacy)
        self.assertNotIn("apt-get", legacy)

    def test_legacy_root_service_matches_canonical_service(self) -> None:
        legacy = (ROOT / "laser-arcade.service").read_text(encoding="utf-8")
        canonical = (ROOT / "systemd" / "laser-arcade.service").read_text(
            encoding="utf-8"
        )
        self.assertEqual(legacy, canonical)

    def test_installer_reproduces_german_desktop_and_restart_support(self) -> None:
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        for required in (
            "locales",
            "de_DE.UTF-8",
            "xdg-user-dir DESKTOP",
            "systemd/schiesskino.desktop",
            'Schiesskino-starten.desktop',
            'LEGACY_DESKTOP_LAUNCHER="${DESKTOP_DIR}/Schiesskino.desktop"',
            "systemd/labwc-environment",
            "/etc/sudoers.d/laser-arcade",
            "/usr/bin/systemctl start laser-arcade.service",
            "visudo -cf",
            "xdotool",
            "unclutter-xfixes",
        ):
            with self.subTest(required=required):
                self.assertIn(required, script)

    def test_desktop_launcher_is_visible_and_uses_project_icon(self) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(ROOT / "systemd" / "schiesskino.desktop", encoding="utf-8")
        entry = parser["Desktop Entry"]
        self.assertEqual(entry["Type"], "Application")
        self.assertEqual(entry["Name"], "Schießkino starten")
        self.assertEqual(entry["Terminal"], "false")
        self.assertTrue(entry["Exec"].endswith("/scripts/start_laser_arcade.sh"))
        self.assertTrue(entry["Icon"].endswith("/assets/schiesskino.svg"))
        self.assertEqual(entry["Path"], "/home/pi/Schiesskino")
        self.assertTrue((ROOT / "assets" / "schiesskino.svg").is_file())

    def test_launcher_starts_only_the_expected_service_and_verifies_it(self) -> None:
        launcher = (ROOT / "scripts" / "start_laser_arcade.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('SERVICE="laser-arcade.service"', launcher)
        self.assertIn("sudo -n /usr/bin/systemctl start", launcher)
        self.assertIn("systemctl is-active --quiet", launcher)
        self.assertNotIn("restart \"${SERVICE}\"", launcher)

    def test_system_service_waits_for_graphical_session_and_preserves_user_home(self) -> None:
        service = (ROOT / "systemd" / "laser-arcade.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("User=pi", service)
        self.assertIn("Environment=HOME=/home/pi", service)
        self.assertIn("Environment=WAYLAND_DISPLAY=wayland-0", service)
        self.assertIn("ExecStartPre=", service)
        self.assertIn("Restart=on-failure", service)
        self.assertIn("WantedBy=graphical.target", service)


if __name__ == "__main__":
    unittest.main()
