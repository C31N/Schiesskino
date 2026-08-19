# Referenzen für die Kinder-Arcade

Die sechs Kinder-Spiele sind eigenständige Python-/Pygame-Implementierungen.
Die gewünschten Abläufe der HTML-/JavaScript-Vorlagen wurden funktional in
die zentrale Laser- und Pygame-Architektur übertragen; Browser-DOM-, Canvas-
und CSS-Code kann dort technisch nicht direkt ausgeführt werden. Folgende
Quellen dienten als konkrete Mechanikvorlage:

- `he-is-talha/html-css-javascript-games` – Insect Catch, Shape Clicker, Quiz, Whack-a-Mole und Simon Says. Das Repository steht unter MIT-Lizenz: https://github.com/he-is-talha/html-css-javascript-games
- Balloon Pop Game von Rudra563 – übernommen wurden der 60-Sekunden-Ablauf,
  aufsteigende Ballons, zufällige Geschwindigkeiten, Punkte, drei Leben,
  zunehmend kürzere Erzeugungsabstände und die kreisförmige Trefferidee. Die
  Umsetzung wurde für kurze Kameralaserimpulse, großzügige sichtbare Ränder,
  Pygame und die gemeinsame Bestenliste neu geschrieben:
  https://gist.github.com/Rudra563/ba2a6b322b0b959dca8b91ba64019ccf
- Maths Game von jclarkedb – übernommen wurden 60 Sekunden, zufällige
  Multiplikationsaufgaben, vier gemischte Antwortmöglichkeiten, unmittelbare
  Richtig-/Falsch-Auswertung und fortlaufende Punkte. DOM-Aufrufe und
  Browser-Timer wurden durch die zentrale Spieluhr und beschießbare
  Pygame-Flächen ersetzt:
  https://gist.github.com/jclarkedb/144c8dfcb44906428481a09e8c1fbfc7
- `jjestrada2/Find-the-Object-Game` – allgemeine Idee mehrerer Suchlevel: https://github.com/jjestrada2/Find-the-Object-Game

Grafiken, deutsche Texte, Laser-Trefferlogik, Punktezahlen, Bestenlisten und die
Einbindung in das Schießkino wurden speziell für dieses Projekt erstellt. Die
Gists enthalten keine eigene Lizenzdatei; deshalb wurde kein unveränderter
Quelltext in das Projekt kopiert, sondern die gewünschten Mechaniken wurden
nachvollziehbar neu implementiert.
