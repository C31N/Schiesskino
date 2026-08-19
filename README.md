# Laser-Erkennung für das Schießkino

Der aktuelle Ausbauzustand umfasst fünf Kernaufgaben:

1. Projektionsfläche automatisch zur Kamera ausrichten.
2. Den kurzen sichtbaren Laserimpuls der Pistole zuverlässig als einzelnen Schuss erkennen.
3. Alle vier Ecken mit unabhängigen Prüfmarken zurückmessen.
4. Ein bildschirmfüllendes Einschießbild zeigen und jeden Treffer darin markieren.
5. Alle 18 regulären Spiele auf drei Menüseiten sowie die drei versteckten
   Spielpfade vollständig mit der Pistole bedienen.

## Hardwareprofil

- Raspberry Pi 5 mit Raspberry Pi OS 64-bit / Debian 13
- Logitech C922 über V4L2
- Epson-Projektor mit `1024x768@60Hz`
- C922-Aufnahme: YUYV, `640x360@30fps`
- Pistolen-Laser laut [Hersteller](https://www.qiangzhanquan.com/#/hardware):
  sichtbares Licht mit 620 nm

Der Hersteller nennt für das Sendesystem außerdem 0,09 W. Den Laser niemals auf
Augen, Personen oder direkt in das Kameraobjektiv richten; geschossen wird nur
auf die diffuse Projektionsfläche.

## Ablauf beim Start

Die Anwendung startet automatisch und zeigt nacheinander:

1. Eine helle Wartefläche, bis der Beamer wirklich sichtbar ist und mindestens
   drei Sekunden lang ein stabiles Bild liefert. Eine feste Wartezeit gibt es
   nicht; ein langsamer oder noch warmer Beamer wird passend berücksichtigt.
2. Schwarz- und Weißbilder zur Erkennung der Projektionsfläche.
3. Vier unabhängige Eckmarker.
4. Einen vollständigen Rahmen mit zwölf Messpunkten entlang aller Kanten zur
   Verfeinerung der Perspektivkorrektur.
5. Sechs Farbflächen für Umgebungslicht, Sensorreserve und automatische
   Erkennung eines montierten Rotfilters.
6. Das Hauptmenü, sobald alle Prüfungen bestanden sind.

Während der Messfolge sind Belichtungsautomatik und Schusserkennung gesperrt.
Die neue Homographie wird erst nach dem vollständigen Licht- und Filtertest
atomar in `~/.laser_arcade/calibration.json` gespeichert. Ein ausgeschalteter,
noch kalter oder flackernder Beamer kann dadurch eine vorhandene gute
Kalibrierung nicht überschreiben. Manuell gespeicherte Ecken werden nach dem
Beamer-Warmstart geprüft, aber nicht automatisch ersetzt.

## Schusserkennung

Ein Schusskandidat muss gleichzeitig:

- im 620-nm-tauglichen rot bis rot-orangen Farbbereich liegen,
- gegenüber Grün und Blau einen deutlichen Rotüberschuss haben,
- hell genug sein,
- neu gegenüber dem laufenden Kamerahintergrund erscheinen und
- innerhalb plausibler Flächengrenzen liegen.

Erkannt wird die steigende Flanke des Impulses. Es gibt keine 300-ms-Verweildauer
mehr. Dadurch kann ein kurzer Pistolenimpuls bereits in einem einzelnen
Kamerabild einen Treffer auslösen, ohne dass statische rote Projektorflächen
fortlaufend als Schuss gelten.

Der 60-fps-Modus der C922 benötigt 1280×720-MJPEG. Auf dem Pi sinkt die reale
Auswerterate durch Dekodierung und Filterung auf etwa 15 fps. Der gewählte
640×360-YUYV-Modus liefert dagegen tatsächlich rund 30 ausgewertete Bilder pro
Sekunde, behält das breite Sichtfeld und ist für kurze Impulse zuverlässiger.

## Anzeige und Bedienung

Nach erfolgreicher Ausrichtung erscheint automatisch das Hauptmenü. Alle
Spielkarten sind mit der Pistole auswählbar:

- Der eigene untere Button **Einschießen** startet den vollständigen,
  geführten Einschießablauf.
- Alle 18 Spielkarten sind vollständig spielbar. Die Übersicht verzichtet
  deshalb auf redundante **Bereit**-Plaketten.
- Die Reihenfolge der Übersicht beginnt mit **Wasser-Alarm** auf Platz 01;
  **Tontaubenschießen** befindet sich auf Platz 04.
- Seite 2 enthält **Ballonjagd**, **Alien-Alarm**, **Sternejagd**,
  **Rechenduell**, **Farbenspiel** und **Schatzsuche**.
- Seite 3 ist klar als Zweispieler-Arena gekennzeichnet und enthält
  **Tic-Tac-Toe**, **4 Gewinnt**, **Käsekästchen**, **Memory-Duell**,
  **Nim-Duell** und **Reversi Light**. Diese Duelle besitzen absichtlich keine
  Bestenliste und zeigen den Gewinnweg drei Sekunden lang auf dem Spielfeld.
- Zusätzlich bleiben **Moorhuhn**, **Annas Meeresmission** und
  **Tobias Blitzduell** über ihre vorgesehenen versteckten Zugänge erreichbar.

### Gemeinsame Spieloptik

- Hauptmenü und jedes Spiel besitzen eine eigene realistische 4:3-Spielwelt:
  Arcade-Halle, Dosenstand, Tontaubenanlage, Zeit-Arena, Reaktionslabor,
  Wasserwelt und Präzisionsschießstand.
- Karten, Bereitschaftsfenster, Ergebnisanzeigen und Schaltflächen sind dunkel
  halbtransparent und folgen dem klaren cyan-grünen Erscheinungsbild von
  **Wasser-Alarm**. Die jeweilige Spielwelt bleibt dadurch sichtbar. Kartentitel und
  Beschreibungen stehen ohne zusätzliche Text-Hintergrundflächen in einem
  eigenen, von den Spielmotiven getrennten Bereich.
- Alle Hintergrundbilder sind auf `1024x768` vorbereitet und rechnerisch von
  roten Farbüberschüssen bereinigt. Dadurch kann keine Kulisse selbst als roter
  Lasertreffer erkannt werden.
- Ziele und interaktive Flächen bleiben als Programmcode gezeichnet. Ihre
  sichtbare Position, Größe und beschießbare Trefferfläche stimmen deshalb auch
  bei den Größenstufen exakt überein.

### Tontaubenschießen

- Zwanzig Tontauben werden innerhalb von 45 Sekunden abwechselnd von links und
  rechts geworfen.
- Parabolische Flugbahnen, Schwerkraft, wechselnde Geschwindigkeiten und
  sichtbare Bruchstücke ergeben einen schlüssigen Wurfscheibenablauf.
- Hohe, schnelle Treffer und Trefferfolgen bringen zusätzliche Punkte.
- Nach der letzten gelandeten Tontaube oder nach Zeitablauf erscheint
  automatisch die Gesamtauswertung.

### Dosenschießen

- Ein Schuss auf die grün markierte Spielkarte öffnet das Spiel.
- Das große Bereitschaftsfenster startet per Schuss einen Drei-Sekunden-Countdown.
- Drei zunehmend große Dosenpyramiden enthalten zusammen 31 Dosen; dafür stehen
  60 Sekunden zur Verfügung.
- Treffer, Trefferserien, Präzision, Restzeit und Punkte werden live angezeigt.
- Die Dosen stehen physikalisch schlüssig aufeinander. Wird eine tragende Dose
  getroffen, kippen nicht mehr abgestützte Dosen mit Schwerkraft und Rotation um.
- Die Dosen besitzen eine dreidimensionale Metalloptik mit gewölbten Reflexen,
  Deckel, Zuglasche, Bodenfalz, Etikett und Kontaktschatten auf einer Stahlbühne.
- Auch ein Streifschuss an der Dosenkante zählt; jede getroffene Dose beginnt
  ohne Aufwärtsbewegung sofort zu fallen.
- Fallende Dosen bleiben während ihrer gesamten sichtbaren Flugphase treffbar.
  Ein Flugtreffer bringt mindestens 250 Extrapunkte; weitere Flugtreffer auf
  dieselbe Dose erhöhen den Bonus. Treffer links oder rechts lenken die Dose
  zur gegenüberliegenden Seite ab und ändern passend ihre Drehung. Treffer oben
  beschleunigen den Fall, Treffer unten bremsen ihn ab, ohne die Dose schweben zu lassen.
- Kurze Signale begleiten Countdown, Schuss, Metalltreffer, Rundenwechsel und
  Ergebnis. Es gibt bewusst keinen dauerhaften Hintergrundton.
- Nach Zeitablauf oder der dritten Pyramide erscheint automatisch die Auswertung.
  Das große Ergebnisfenster startet eine neue Runde; **MENÜ** führt zurück.
- Jeder sichtbare Bedienbereich ist mit der Pistole nutzbar.

### Zeitschießen

- Zwanzig Ziele erscheinen nacheinander für jeweils höchstens 1,75 Sekunden.
- Trefferzeit, Serie, Präzision, Restzeit und Punkte werden live angezeigt.
- Schnelle Treffer erzeugen einen Zeitbonus; verpasste Ziele beenden die Serie.
- Nach dem zwanzigsten Ziel oder nach 40 Sekunden folgt automatisch die
  Gesamtauswertung mit durchschnittlicher Reaktionszeit.

### Wasserfreunde Dalum – Wasser-Alarm

- Die Runde startet sofort ohne Namenseingabe. Nur bei einem Ergebnis unter den
  besten zehn kann danach ein Name mit höchstens acht Buchstaben über die
  vollständig beschießbare Bildschirmtastatur eingetragen oder die Eingabe
  übersprungen werden. Ziffern werden bei Namen grundsätzlich nicht angeboten.
- Nach dem Countdown läuft eine Runde genau 60 Sekunden: zunächst werden große
  Wasserlecks geschlossen, danach folgen bewegte Wasserbälle, Badeenten,
  Rettungsringe, seltene goldene Ringe und der springende Vereinsdelphin als
  Bonusziel.
- Alle beschießbaren Motive besitzen eine dunkle, deutlich umrandete Trefferfläche,
  damit der rote Laser auch vor hellen oder kontrastarmen Bildbereichen zuverlässig
  erkannt wird. Die Badeenten schwimmen getrennt voneinander auf sichtbaren
  Wasserbahnen und hinterlassen eine Kielwelle.
- Bonus- und Wertungsringe verwenden schmale, vollständig rotfreie
  cyan-grüne Konturen. Ihr Fangrand folgt enger der sichtbaren Ringfläche,
  damit weder das Einblenden der Grafik noch Schüsse auf benachbarte Ziele als
  Ringtreffer gewertet werden.
- Das offizielle Vereinslogo ist ein Schutzziel und kostet bei einem Treffer
  500 Punkte. Sein Ein- und Ausblenden wird für die Laserprüfung gesperrt, damit
  die roten Logoanteile niemals selbst einen Schuss vortäuschen.
- Drei, fünf und zehn Treffer in Folge erhöhen den Multiplikator auf ×1,5, ×2
  und ×3. Ein Fehlschuss setzt die Combo zurück.
- In den letzten 15 Sekunden erscheint das bewegliche Hauptrohr mit vier
  Wertungszonen von 100 bis 1.000 Punkten.
- Die Auswertung zeigt Punkte, Treffer, Schüsse, Genauigkeit und beste Combo.
  Die Top 10 bleiben dauerhaft in
  `~/.laser_arcade/water_alarm_leaderboard.json` gespeichert.
- Ein unauffälliger Drei-Punkte-Button unten rechts in der Bestenliste öffnet
  den geschützten Rücksetzdialog; die einheitliche Admin-PIN lautet `1919`.
- Hintergrund, Ziele und Effekte verwenden eine laserfreundliche Wasserpalette;
  es gibt nur kurze Ereignisklänge und keinen dauerhaften Ton.

### Eigene Bestenlisten für alle Spiele

- Dosenschießen, Tontaubenschießen, Zeitschießen und Reaktion besitzen jeweils
  eine eigene dauerhafte Top 10. Bei Punktgleichheit entscheiden die für das
  Spiel passenden Werte wie Trefferzahl, Präzision, Serie oder Reaktionszeit.
- Die Zielscheibe führt getrennte Top-10-Listen für **Ganze Ringe**,
  **Zehntelringe** und **Teiler** sowie für jede eingestellte Schusszahl. Bei
  Teilerwertung ist der kleinere Wert besser.
- Die Namenseingabe erscheint ausschließlich bei einer Top-10-Platzierung,
  akzeptiert höchstens acht Buchstaben einschließlich **Ä**, **Ö** und **Ü**
  und kann vollständig mit der Pistole bedient oder übersprungen werden.
- Die Listen werden in `~/.laser_arcade/arcade_leaderboards.json` gespeichert.
  Jede Ergebnisansicht besitzt wie Wasser-Alarm einen unauffälligen
  Drei-Punkte-Zugang zum geschützten Zurücksetzen. Der PIN lautet überall
  `1919`.

### Reaktion

- Neun Zielpositionen bleiben zunächst dunkel; nach einer zufälligen Wartezeit
  wird genau ein Ziel freigegeben.
- Zwölf Signale messen Durchschnitts- und Bestzeit.
- Ein Schuss vor dem Signal wird als Frühstart erkannt, kostet Punkte und
  verzögert das nächste Signal.

### Zielscheibe

- Eine große Zehnring-Zielscheibe beginnt sofort eine neue Serie.
- Ein Treffer auf die Schusszahl oben rechts wechselt zwischen 3, 5 und
  10 Schüssen.
- Ein Treffer auf die Genauigkeit oben links wechselt zwischen **Ganze Ringe**,
  **Zehntel** und **Teiler**. Die Ergebnisliste unten rechts folgt automatisch
  der gewählten Wertungsart.
- Nach der eingestellten Schusszahl bleibt zunächst die ausführliche
  Gesamtauswertung mit allen Einzeltreffern garantiert drei Sekunden sichtbar.
  Erst danach öffnet sich die passende Top 10. Je nach Rang kann anschließend
  ein Name eingetragen, übersprungen, eine neue Serie gestartet oder zum Menü
  gewechselt werden.
- Für jede Wertungsart werden die letzten fünf Ergebnisse dauerhaft in
  `~/.laser_arcade/target_history.json` gespeichert.
- **Kleiner** und **Größer** verändern die Scheibengröße; **Menü** führt unten
  links zurück.

Nach einem Treffer auf den unteren Button **Einschießen** erscheint das
Einschießbild:

- Der umlaufende grüne Rand nutzt die gesamte Beamerfläche.
- Vier grüne Eckanzeigen bestätigen die vollständig erkannte Leinwand.
- Schritt 1 fordert fünf Schüsse auf die Bildschirmmitte an.
- Danach zeigt eine Auswertung Treffpunktlage und Streukreis.
- Die Schritte 2 bis 5 heben nacheinander oben links, oben rechts, unten rechts
  und unten links hervor; jede Ecke verlangt drei Schüsse.
- Zwischen den Schritten geht es bewusst mit **WEITER** oder der Leertaste weiter.
- Direkt nach dem 17. Schuss erscheint automatisch die Gesamtauswertung; sie
  fasst alle Treffer getrennt nach Zielposition zusammen.
- Aus den mittleren Treffpunkten aller fünf Gruppen wird die horizontale und
  vertikale Waffenabweichung robust berechnet. Diese Korrektur wird dauerhaft in
  `~/.laser_arcade/weapon_calibration.json` gespeichert und anschließend auf
  jeden Treffer in Menüs und Spielen angewendet. Das Einschießbild zeigt weiter
  die unkorrigierten Rohdaten, damit ein erneutes Einschießen sauber messen kann.
- Widersprechen sich die fünf gemessenen Treffpunktlagen zu stark, wird keine
  unsichere Korrektur gespeichert; eine vorhandene gültige Kalibrierung bleibt
  erhalten und die Gesamtauswertung fordert zum erneuten Einschießen auf.
- Das Zielbild enthält bewusst keine roten oder weißen Grafiken, damit ein
  DLP-Farbblitz nicht als roter Laserimpuls fehlinterpretiert wird.

Mit `L` oder **KAMERABILD** kann jederzeit zwischen Einschießbild und Diagnose
gewechselt werden. Im Diagnosebild gilt:

Die Pistole ist das primäre Eingabegerät: Jede sichtbare Schaltfläche besitzt
links einen kleinen Zielpunkt und kann direkt beschossen werden. Der Schuss
löst nur die Schaltfläche aus und wird nicht als Wertungsschuss gezählt. Damit
lassen sich **WEITER**, **EINSCHIEßEN WIEDERHOLEN**, **KAMERABILD**,
**ZIELBILD**, **NEU AUSRICHTEN** und **ABLAUF NEU** ohne Maus oder Tastatur
bedienen.
Während einer Auswertung ist zusätzlich das gesamte große Auswertungsfenster
beschießbar: Ein Treffer darin bedeutet **WEITER**. In der Gesamtauswertung
startet ein Treffer auf das große Fenster den Einschießablauf erneut.
Der Mauszeiger wird nach 1,5 Sekunden ohne Mausbewegung automatisch ausgeblendet
und erscheint erst wieder bei einer Mausbewegung oder einem Mausklick.
Zusätzlich verwendet Labwc bereits beim Sitzungsstart die transparente Theme
`SchiesskinoInvisible`. Die Anwendung blendet den Zeiger außerdem per XFixes
direkt auf ihrem Xwayland-Fenster aus und startet `unclutter-xfixes` mit
`--start-hidden`. Dadurch bleibt der Startzeiger auch ohne angeschlossene oder
jemals bewegte Maus unsichtbar.

- Blauer Kreis: aktueller optischer Kandidat.
- Cyanfarbenes Fadenkreuz: bestätigter Schuss.
- Grüne Linie: automatisch erkannte Projektionsfläche.
- Rechts: Impulsmaske, Messwerte und Leinwandposition.
- Unten: letzte fünf bestätigte Schüsse.
- **MANUELL** öffnet die Seiten **AUSRICHTUNG** und **SCHUSSERKENNUNG**.
  Dort lassen sich die vier Ecken mit der Maus ziehen oder per Pistole auswählen
  und verschieben. Filterprofil, Empfindlichkeit und erweiterte Schwellwerte
  werden zunächst nur als Vorschau angewendet und erst mit **ÜBERNEHMEN** global
  für alle Spiele gespeichert.
- **AUTOMATIK**, **OHNE FILTER** und **ROTFILTER** besitzen getrennte Werte. Im
  Automatikmodus wechselt das Profil erst nach drei Sekunden stabiler Messung.
  Die Anzeige nennt Basiswert und aktuell wirksamen adaptiven Schwellwert.
- Die normale Kameravorschau ist laserneutral eingefärbt. **ORIGINAL 5 S** zeigt
  die Originalfarbe kurz an und sperrt währenddessen die Schusserkennung.
- `A` oder **AUTO AUSRICHTEN**: neue Schwarz-/Weißausrichtung.
- `C` oder **ABLAUF NEU**: Einschießablauf und Treffer zurücksetzen.
- `L` oder **KAMERABILD**: Diagnosebild ein-/ausblenden.
- Schuss auf **WEITER**, `Leertaste`, `Enter` oder `N`: nach einer Auswertung
  fortfahren.
- `Esc`: Anwendung beenden; der Dienst startet bei normalem Beenden nicht neu.

Debugbilder der letzten Ausrichtung liegen unter:

- `~/.laser_arcade/alignment_difference.png`
- `~/.laser_arcade/alignment_mask.png`
- `~/.laser_arcade/alignment_verification_mask.png`
- `~/.laser_arcade/alignment_precision_mask.png`

## Installation

```bash
./install.sh
sudo systemctl restart laser-arcade.service
```

Der Installer richtet Python, OpenCV, Pygame, V4L2, die C922-Geräteregel und den
grafischen systemd-Autostart ein. Betriebsdaten des installierten Pi stehen in
[`PI_SETUP.md`](PI_SETUP.md).

## Test

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Die Tests prüfen die vollständigen Abläufe aller 21 Spielpfade: Wertungen,
Zeitabläufe, Zielwechsel, Frühstarts, getrennte Bestenlisten, Duellregeln,
versteckte Zugänge, Pistolenbedienung, sichtbare Zielränder, Laserneutralität,
Darstellung und das realistische Umfallen nicht mehr abgestützter Dosen.
