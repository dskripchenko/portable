# portable

Eine lokale Entwicklungsumgebung für Windows — PHP, Caddy, PostgreSQL, MariaDB,
Redis, Node — die sich **neben** das System setzt statt hinein.

- [Erste Schritte](getting-started.md) — installieren, eine Website ausliefern, eine Datenbank hinzufügen
- [Befehle](commands.md) — jeder Befehl und wofür er da ist
- [Wie es funktioniert](design.md) — die Entscheidungen, die man kennen sollte
- [Wenn etwas schiefgeht](troubleshooting.md) — die wahrscheinlichsten Fehler und was sie bedeuten

## Was „neben dem System“ heißt

Jeder Punkt ist eine Bedingung, unter der das Werkzeug gebaut ist, kein Wunsch:

- **Keine Administratorrechte.** Nicht bei der Installation, nicht im Betrieb, nie.
- **Keine `hosts`-Datei.** Websites sind unter `*.localhost` erreichbar, das
  Windows von sich aus auf die Loopback-Adresse auflöst.
- **Keine Dienste, kein Autostart.** Der Supervisor ist ein Prozess, den Sie
  starten. Er überlebt das Schließen von Terminal und IDE; einen Neustart nicht.
- **Keine Registry, kein PATH, keine Systemverzeichnisse.** Alles liegt in einem
  Verzeichnis. Es zu löschen deinstalliert das Werkzeug vollständig.

Das Ergebnis läuft auf einem abgeriegelten Firmenrechner — also genau dort, wo
Werkzeuge dieser Art sich sonst gar nicht installieren lassen.

## Stand

Veröffentlicht und beinahe vollständig geprüft. Das Ausliefern von PHP auf
echtem Windows ist bestätigt, und **Port 80 als gewöhnlicher Benutzer zu
belegen** ebenfalls — die Voraussetzung, auf der der ganze Entwurf ruht, denn
ein lokaler Webserver, der erhöhte Rechte bräuchte, bräuchte sie täglich.

Eines nicht: das Überleben von Konsole und Job-Objekt, wenn ein Terminal oder
eine IDE geschlossen wird. Es steht in [Fehlerbehebung](troubleshooting.md).

macOS und Linux sind keine Ziele. Alle Kataloge lösen Windows-Archive auf; das
Werkzeug läuft dort zwar, installiert aber Binärdateien, die jene Maschine nicht
ausführen kann.
