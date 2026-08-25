# Erste Schritte

## Installation

Laden Sie `portable-x86_64-pc-windows-msvc.zip` von den
[Releases](https://github.com/dskripchenko/portable/releases) und entpacken Sie
es irgendwohin — einen Ordner auf dem Desktop, eine zweite Festplatte, einen
USB-Stick. Es gibt nichts zu installieren und kein Installationsprogramm.

Das Bündel bringt sein eigenes Python mit. Ein Werkzeug, dessen Aufgabe es ist,
Laufzeitumgebungen auf eine Maschine zu bringen, die keine hat, kann
vernünftigerweise nicht zuerst eine davon verlangen — und unter Windows gibt es
standardmäßig gar keines: Was wie `python` aussieht, ist eine Verknüpfung, die
den Microsoft Store öffnet.

```powershell
cd C:\portable
.\portable.cmd version
```

Führen Sie es in einem gewöhnlichen PowerShell-Fenster aus. Sollte je etwas nach
Administratorrechten fragen, ist das ein Fehler — bitte melden Sie ihn.

## Wählen Sie, wo es seine Daten ablegt

```powershell
.\portable.cmd home                    # wo, und was das entschieden hat
.\portable.cmd home set D:\portable    # ab jetzt dorthin
.\portable.cmd home set --beside       # neben den Starter, damit alles mitreist
```

Voreingestellt ist `%LOCALAPPDATA%\portable`. Auf einem verwalteten Rechner kann
diese Voreinstellung nicht bloß unwillkommen, sondern unbrauchbar sein: AppLocker
ist üblicherweise so eingerichtet, dass die Ausführung aus dem Benutzerprofil
verweigert wird — dort landet ohne Administratorrechte installierte Software, was
der Sinn der Regel ist — und alles, was hier heruntergeladen wird, ist eine
ausführbare Datei. Wo das gilt, startet nichts, bevor dies umgestellt wurde.

`--beside` ist für einen USB-Stick gedacht. Es merkt sich das Wort statt des
heutigen Pfades, sodass das Bündel weiter funktioniert, wenn sich der
Laufwerksbuchstabe ändert.

## Starten

```powershell
.\portable.cmd up
```

Das startet den Supervisor, dem alles andere gehört. Er überlebt das Schließen
von Terminal und IDE. Einen Neustart überlebt er nicht — absichtlich, denn dafür
bräuchte es einen Autostart-Eintrag, und die legt dieses Werkzeug nicht an.

## Eine Website ausliefern

```powershell
.\portable.cmd install php
.\portable.cmd install caddy
.\portable.cmd site add demo C:\projects\demo
```

Öffnen Sie `http://demo.localhost`. Keine hosts-Datei wurde bearbeitet und kein
DNS-Server ist beteiligt: Windows löst alles unter `.localhost` selbst auf die
Loopback-Adresse auf.

Liegt der Front-Controller des Projekts in `public/` — Laravel, Symfony und die
meisten anderen — wird genau das ausgeliefert, und das Werkzeug sagt es. Sonst
würde das Ausliefern des Repository-Wurzelverzeichnisses den Quelltext der
Anwendung über HTTP preisgeben, `.env` eingeschlossen, und dabei bloß aussehen,
als funktioniere es nicht. `--exact` nimmt den Pfad wörtlich.

Eine PHP-Version binden Sie je Website mit `--php 8.2`; ohne das folgt eine
Website der jeweils neuesten. Mehrere Versionen laufen nebeneinander, jede mit
eigenem Arbeiter-Pool.

## HTTPS

```powershell
.\portable.cmd trust
```

Websites werden auch über TLS ausgeliefert, von einer Zertifizierungsstelle, die
Caddy lokal betreibt. `trust` legt deren Wurzelzertifikat in **Ihren**
Zertifikatspeicher — nicht den der Maschine, wofür Administratorrechte nötig
wären.

Windows zeigt einen Bestätigungsdialog. Das fragt Windows, nicht dieses Werkzeug,
und einen Weg daran vorbei sollte es nicht geben.

Firefox führt einen eigenen Speicher und warnt weiterhin. Er liest den
Windows-Speicher nur, wenn `security.enterprise_roots.enabled` in `about:config`
eingeschaltet ist — eine Einstellung in Ihrem Profil, die zu ändern nicht Sache
dieses Werkzeugs ist.

## Eine Datenbank hinzufügen

```powershell
.\portable.cmd install postgres
.\portable.cmd service add postgres
```

Sie startet auf `127.0.0.1:5432`, Benutzer `postgres`, ohne Passwort — und nur
auf der Loopback-Adresse, denn Trust-Authentifizierung auf einem über das Netz
erreichbaren Port ist die Art, wie ein Laptop im Konferenz-WLAN jemand anderem
gehört.

`service remove` hält sie an und **behält die Daten**. Erneutes Hinzufügen macht
dort weiter, wo es aufgehört hat.

`mariadb` und `redis` funktionieren genauso, auf 3306 und 6379.

## Node und andere Werkzeuge

```powershell
.\portable.cmd install node
.\portable.cmd run npm install
```

`portable run` setzt die installierten Laufzeitumgebungen nur für diesen einen
Befehl in den PATH. Am PATH der Maschine ändert sich nichts. Wollen Sie die
Einstellungen für eine ganze Shell-Sitzung, gibt `portable env` sie aus.

## Anhalten

```powershell
.\portable.cmd down
```

Alles hält an: der Router, die PHP-Arbeiter, die Datenbanken. Wenn der Befehl
zurückkehrt, sind die Ports frei.

Um das Werkzeug vollständig zu entfernen, löschen Sie das Verzeichnis, in dem es
seine Daten hält — `portable home` sagt welches — und den entpackten Ordner. Mehr
gibt es nicht: keine Registry-Schlüssel, keine Dienste, keine PATH-Änderungen.
