# Raspberry-Pi-Installation

Stand: 12.08.2026

## Installiertes Zielsystem

- Hostname: `Schiesskino`
- Adresse: `192.168.178.183`
- Benutzer: `pi`
- Betriebssystem: Raspberry Pi OS 64-bit / Debian 13 (Trixie)
- Projektpfad: `/home/pi/Schiesskino`
- Desktop: Labwc/Wayland mit automatischer Anmeldung
- Ausgabe: Epson-Projektor, `1024x768@60Hz`
- Kamera: Logitech C922, Capture-Alias `/dev/video-c922`

## SSH-Zugang vom Entwicklungs-PC

Auf diesem PC wurde ein eigener SSH-Schlüssel unter
`$env:USERPROFILE\.ssh\id_ed25519_schiesskino` eingerichtet. Verbindung aus
PowerShell:

```powershell
ssh -i "$env:USERPROFILE\.ssh\id_ed25519_schiesskino" pi@192.168.178.183
```

Das Kennwort ist nicht im Repository gespeichert. Für `sudo` wird weiterhin
das Kennwort des Benutzers `pi` abgefragt.

## Betrieb

Die Anwendung startet nach dem Booten als systemweiter Dienst, wartet dabei
auf die grafische Sitzung und öffnet genau eine Vollbildinstanz.

```bash
sudo systemctl status laser-arcade.service
sudo systemctl restart laser-arcade.service
sudo systemctl stop laser-arcade.service
journalctl -u laser-arcade.service -n 100 --no-pager
tail -f ~/.laser_arcade/logs/laser_arcade.log
```

Die dauerhafte Beamer-Konfiguration liegt in
`~/.config/kanshi/config`. Einstellungen und Kalibrierung der Anwendung liegen
unter `~/.laser_arcade/`.

## Kamera, Filter und automatische Ausrichtung

Die Anwendung unterstützt den Betrieb ohne Vorsatz sowie mit rotem optischen
Filter. Unter **Kamerabild → Manuell → Schusserkennung** stehen **Automatik**,
**Ohne Filter** und **Rotfilter** zur Verfügung. Beide Betriebsarten speichern
getrennte Empfindlichkeits- und Schwellwerte. Im Automatikmodus wird erst nach
drei Sekunden stabiler, flächiger Rotdominanz gewechselt; einzelne rote Motive
reichen dafür nicht aus. Das Rotfilterprofil verlangt zusätzlich einen kompakten
zeitlichen Helligkeitsanstieg, damit weiße Projektorschrift nicht als Schuss gilt.

Die Kamera muss die komplette Projektionsfläche mit einem schmalen Rand auf
allen vier Seiten sehen. Beim Start misst die Anwendung automatisch ein
Schwarz- und ein Weißbild. Anschließend werden vier projizierte Eckmarker mit
der Kamera zurückgemessen. Nur bei `4/4` bestätigten Ecken wechselt die Anzeige
automatisch zum bildschirmfüllenden Einschießbild. Wird eine abgeschnittene Kante
oder ein fehlender Marker erkannt, Kamera leicht neu ausrichten und `A` drücken.
Eine erfolgreiche Homographie wird in `~/.laser_arcade/calibration.json`
gespeichert.

Unter **Kamerabild → Manuell → Ausrichtung** können alle vier erkannten Ecken
direkt im Livebild gezogen werden. Alternativ werden sie mit der Pistole
ausgewählt und über große Pfeiltasten verschoben. Änderungen bleiben bis
**Übernehmen** eine Vorschau. Manuell gespeicherte Ecken werden beim nächsten
Start geprüft und bleiben aktiv, bis bewusst **Neu ausrichten** gewählt wird.
Nach einer Eckänderung weist das Hauptmenü auf eine erneute Kontrolle des
Einschießens hin.

Der geführte Einschießablauf beginnt mit fünf Schüssen in die Mitte. Nach der
Auswertung folgen die vier Ecken im Uhrzeigersinn mit jeweils drei Schüssen.
Jeder Abschnitt zeigt Treffpunktlage und Streukreis; nach insgesamt 17 Schüssen
erscheint die Gesamtauswertung automatisch und ohne weitere Bestätigung.

Nach jeder erfolgreichen Ausrichtung öffnet sich zuerst das pistolenbedienbare
Hauptmenü. **Einschießen** besitzt unten einen eigenen aktiven Button.
Alle 18 regulären Spiele sind auf drei Seiten vollständig spielbar. Seite 3 ist
die reine Zweispieler-Arena; dort gibt es absichtlich keine Bestenlisten. Die
drei versteckten Spiele Moorhuhn, Annas Meeresmission und Tobias Blitzduell
bleiben über ihre vorgesehenen Geheimzugänge erreichbar. Die Übersicht zeigt
keine überflüssigen **Bereit**-Kennzeichnungen. Die Zielscheibe speichert ihre
nach Wertungsart getrennten Ergebnislisten in
`~/.laser_arcade/target_history.json`.

**Programm beenden** liegt im bisherigen Kamerabild-/Diagnosebereich und ist
mit der PIN `1919` geschützt. Der sichtbare Desktop-Starter
**Schießkino starten** startet danach ausschließlich den vorhandenen Dienst.
Dieselbe PIN `1919` schützt auch das Zurücksetzen aller Bestenlisten.

Alle sichtbaren Schaltflächen sind direkt mit der Pistole bedienbar. Der kleine
Zielpunkt links in einer Schaltfläche kennzeichnet sie als beschießbar. Solche
Steuerschüsse werden nicht in die Einschießauswertung aufgenommen; Maus und
Tastatur sind nur alternative Eingabemöglichkeiten.
Auch das gesamte große Auswertungsfenster ist eine Schaltfläche: Ein Treffer
bedeutet **WEITER**, beziehungsweise in der Gesamtauswertung **WIEDERHOLEN**.
Nach 1,5 Sekunden ohne Mausaktivität wird der Mauszeiger automatisch verborgen.
Labwc verwendet bereits beim Sitzungsstart die transparente Cursor-Theme
`SchiesskinoInvisible`. Unter Xwayland wird der Zeiger zusätzlich direkt per
XFixes auf dem Anwendungsfenster verborgen und `unclutter-xfixes` läuft mit
`--start-hidden`. Eine Maus muss weder angeschlossen noch zuvor bewegt worden
sein. Nach dem Beenden stellt die Anwendung den normalen Desktop-Mauszeiger
wieder her; bei echter Maus- oder Touchpadbewegung ist er im Spiel sofort
sichtbar und verschwindet erst nach erneuter Inaktivität.

- `A`: automatische Ausrichtung wiederholen
- `C`: Einschießablauf neu beginnen
- `L`: zwischen Einschießbild und Kamera-Diagnose wechseln
- `Leertaste`, `Enter` oder `N`: nach einer Auswertung fortfahren
- Cyanfarbenes Fadenkreuz: bestätigter Schuss
- Blauer Kreis: aktueller optischer Kandidat
