# Wenn etwas schiefgeht

Fangen Sie mit diesen beiden an. Zusammen beantworten sie die meisten Fragen.

```powershell
.\portable.cmd status        # was läuft, und warum nichts ausgeliefert wird, falls so
.\portable.cmd version       # dieser Build, der Interpreter, der laufende Daemon
```

Die Protokolle liegen im Ordner `logs` dort, wo `portable home` das
Datenverzeichnis nennt — eine Datei je Prozess.

## `php-cgi.exe` startet nicht

Die Meldung nennt PHP und zeigt das Ende des Protokolls dieses Arbeiters. Unter
Windows ist die übliche Ursache das **Visual C++ Redistributable**: Die Builds
von php.net binden dagegen, und sein Fehlen wird als `VCRUNTIME140.dll not found`
gemeldet.

Seine Installation braucht Administratorrechte, die dieses Werkzeug nicht
erfragt. Können Sie es nicht installieren, übernimmt
`portable install php --from C:\anderes\php` ein PHP, das auf der Maschine
bereits funktioniert.

## Es wird nichts ausgeliefert

`portable status` sagt warum. Ein Supervisor, der läuft, seine Websites auflistet,
seine Arbeiter betreibt und alles beantwortet außer dieser Frage, ist schlechtere
Gesellschaft als einer, der steht — deshalb wird der Grund aufbewahrt und dort
berichtet.

Meistens liegt es am Port.

## Caddy startet nicht auf Port 80 oder 8080

In der Reihenfolge, in der es sich herausstellt:

1. **Ein anderer lokaler Stack läuft** — Laragon, XAMPP, Docker Desktop. Die
   belegen 80 und 8080 zusammen, und genau so sieht das aus.
2. **IIS oder der WWW-Publishingdienst** hält 80.
3. **Der Port liegt in einem von Windows reservierten Bereich.** Niemand lauscht,
   und das Binden scheitert trotzdem.
   `netsh interface ipv4 show excludedportrange protocol=tcp` listet sie —
   Hyper-V und WSL reservieren Bereiche dynamisch.

```powershell
netstat -ano | findstr ":80 "     # nennt den Prozess, der ihn hält
.\portable.cmd port 8888          # oder einfach umziehen
```

Ein Port, der sich nicht binden lässt, wird nicht gespeichert: Der vorherige
kommt zurück, und Ihre Websites mit ihm.

### Es heißt, der Port sei belegt, und etwas anderes antwortet darauf

Wissenswert: Ein Programm kann einen Port über IPv4 halten, während Caddy IPv6
nimmt, oder umgekehrt. Beide „gelingen“, und Anfragen an `127.0.0.1` landen beim
anderen Programm. Das Werkzeug prüft, indem es den Port fragt, ob die Antwort
seine eigene ist — deshalb lehnt es manchmal einen Port ab, der sich scheinbar
binden ließ.

## Downloads scheitern — `10054`, `10060`, `record layer failure`

```
SSLError: [SSL] record layer failure (_ssl.c:2660)
URLError: <urlopen error [WinError 10054] ...>
```

Ein wiederholt mittendrin zurückgesetzter TLS-Handshake liegt gewöhnlich an
etwas zwischen Ihrer Maschine und dem Host, nicht an einem der Enden —
Verkehrsinspektion oder ein filternder Proxy. Er ist naturgemäß sporadisch:
Derselbe Befehl gelingt oft beim nächsten Versuch.

Alles wird fünfmal mit wachsenden Pausen wiederholt, und abgebrochene
Übertragungen setzen dort fort, wo sie stehen blieben, statt neu zu beginnen —
das ist es, was ein neunzig Megabyte großes Archiv über eine ständig abreißende
Verbindung überhaupt ankommen lässt.

Scheitert es weiterhin, führt die Meldung jeden Versuch auf. Fünf gleiche
Rücksetzungen und fünf verschiedene Fehler bedeuten Verschiedenes. `HTTPS_PROXY`
wird beachtet, falls Sie einen haben.

**`WinError 10060` gegen `downloads.mariadb.org`** ist eine Zeitüberschreitung
beim Verbindungsaufbau, keine Rücksetzung — dieser Host ist aus manchen Netzen
schlicht nicht erreichbar, und Wiederholen dauert nur länger bis zum Scheitern.
MariaDB wird stattdessen von `archive.mariadb.org` geholt, wo dieselben Ausgaben
mit Prüfsummen daneben liegen. `PORTABLE_MARIADB_ARCHIVE` zeigt auf einen eigenen
Spiegel.

## `SSLCertVerificationError`, „das Zertifikat konnte nicht geprüft werden“

In einem verwalteten Netz heißt das meist, dass TLS an einem Proxy endet, dessen
Zertifizierungsstelle diese Maschine nicht kennt. Exportieren Sie deren
Wurzelzertifikat und zeigen Sie darauf:

```powershell
$env:PORTABLE_CA_BUNDLE = "C:\pfad\zu\firmen-root.pem"
```

Einen Schalter, der die Prüfung abschaltet, gibt es nicht, und es sollte ihn
nicht geben: Alles, was hier heruntergeladen wird, wird danach ausgeführt.

Sagt die Meldung, dieses Python habe **überhaupt keine** vertrauten Wurzeln, ist
das ein anderes Problem — der eigene Speicher des Interpreters ist leer, was
unter Windows nicht vorkommt, weil Python dort den Systemspeicher liest.

## GitHub meldet, das Anfragelimit sei erschöpft

Caddy, PostgreSQL und Redis werden über die API von GitHub aufgelöst, die sechzig
anonyme Anfragen pro Stunde **je Adresse** erlaubt. Hinter einem Firmen-NAT sind
das sechzig für das ganze Haus, und aufbrauchen können sie Leute, die dieses
Werkzeug nie gestartet haben.

```powershell
$env:PORTABLE_GITHUB_TOKEN = "ghp_..."
```

Jedes Token ganz ohne Berechtigungen genügt. Es braucht keine Rechte, nur eine
Identität. PHP ist nicht betroffen — es wird anderswo veröffentlicht.

## `https://` warnt in Firefox weiterhin

Firefox führt einen eigenen Zertifikatspeicher und liest weder den von Windows
noch irgendetwas, das dieses Werkzeug erreichen kann. Er liest den
Windows-Speicher, wenn `security.enterprise_roots.enabled` in `about:config`
eingeschaltet wird — eine Einstellung in Ihrem Profil, die zu ändern nicht Sache
dieses Werkzeugs ist.

Chrome, Edge und alles andere, das den Systemspeicher liest, deckt
`portable trust` ab.

## Der Supervisor starb, als ich das Terminal schloss

Dann hat dieses Terminal ihn in ein **Job-Objekt** gesteckt, das er nicht
verlassen durfte — und `portable up` hat das beim Start gesagt:

> This terminal put it in a job it could not leave, so closing this window may
> stop it.

Ein Job-Objekt ist die Art, wie ein Starter sicherstellt, dass alles von ihm
Gestartete beim Beenden aufgeräumt wird; manche Editoren nutzen eines für ihre
Startkonfigurationen. Windows lässt einen Prozess ein Job-Objekt nur verlassen,
wenn dieses es erlaubt — ein Flag, das sein Erzeuger setzt —, und gegen eines,
das es nicht erlaubt, entkommt auf Prozessebene nichts. Auf Windows in beide
Richtungen gemessen und bei jedem Lauf geprüft.

Starten Sie ihn aus einem gewöhnlichen PowerShell-Fenster, dann überlebt er
dessen Schließen.

Alles Übrige am Ablösen funktioniert: Konsole und Prozessgruppe bleiben in jedem
Fall zurück, und das ist es, was ein gewöhnliches Terminal beim Schließen
mitnimmt.

## Eine Erweiterung ist aktiviert, und PHP hat sie nicht

`portable ext list` markiert sie als **MISSING**: Die `php.ini` lädt etwas, das
dieser Build nicht mitbringt. PHP scheitert daran nicht — es warnt beim Start,
in ein Protokoll, und läuft ohne die Erweiterung — weshalb das Symptom sonst
Stunden später als eine nicht vorhandene Funktion ankommt.

`portable ext install <name>` holt das Richtige, passend zu diesem Build.

## Ich habe die Installation verschoben, und alles ist weg

Den Ablageort zu wechseln verschiebt nichts. Beim Wechseln wird das alte
Verzeichnis genannt, samt dem, was noch darin liegt — kopieren Sie es hinüber
oder installieren Sie neu und löschen Sie es. Nichts ist verloren.

## Etwas melden

`portable status --json`, `portable version --json` und die betreffende Datei aus
dem Ordner `logs`. Hat das Werkzeug Ihnen ein Protokollende in der Fehlermeldung
gezeigt, ist darin meist alles enthalten.
