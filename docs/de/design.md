# Wie es funktioniert

## Dem Supervisor gehört alles, die Kommandozeile ist ein Client

Ein Daemon hält die Laufzeitumgebungen, die Prozesse und die Konfiguration und
bietet auf der Loopback-Adresse eine per Token abgesicherte API an. `portable`
ist deren erster Client. Ein IDE-Plugin wird der zweite, und nichts muss dafür
nachgerüstet werden, weil es keine Fähigkeit gibt, die nur von der Kommandozeile
aus erreichbar wäre.

Diese Regel ist gegen die wiederkehrende Versuchung zu verteidigen, „dieses eine
Stück direkt“ zu erledigen, weil ein Umweg über den Daemon für eine Kleinigkeit
übertrieben wirkt. Jede Ausnahme wird später zu einer Lücke im Plugin.

Das Token ist keine Zeremonie. Die API startet Prozesse und ist für alles
erreichbar, was unter diesem Benutzer läuft, einschließlich eines
postinstall-Skripts aus `npm install`. Die Bindung an die Loopback-Adresse hält
andere Maschinen fern und tut gegen lokale nichts — deshalb berechtigt der Besitz
der Discovery-Datei zum Zugriff.

## Unter Windows gibt es kein php-fpm

FPM ist eine reine Unix-SAPI. Der Windows-Build von PHP liefert `php-cgi.exe`,
das FastCGI spricht, wenn man ihm eine Adresse gibt, und **eine Anfrage zur Zeit**
bedient. Eigene Kinder kann es nicht erzeugen: `PHP_FCGI_CHILDREN` braucht
`fork()`, das Windows nicht hat.

Nebenläufigkeit sind also N getrennte Prozesse auf N Ports, zwischen denen der
Router verteilt. Prozessaufsicht ist damit der Kern dieses Werkzeugs und nicht
Beiwerk darum herum.

Zwei Folgen, die sonst wie Fehler aussehen:

- **Dass ein Arbeiter endet, ist normal.** `PHP_FCGI_MAX_REQUESTS` lässt ihn nach
  einer festgelegten Zahl von Anfragen absichtlich abtreten, als Speicherhygiene.
  Ihn neu zu starten ist der Entwurf, keine Fehlerbehandlung.
- **Die Poolgröße ist die Grenze der Nebenläufigkeit.** Bei vier Arbeitern wartet
  die fünfte gleichzeitige Anfrage. In `php-cgi` gibt es keine Warteschlange, die
  das auffängt — deshalb wird eine Anfrage über den Pool wiederholt, statt zu
  scheitern, wenn sie einen gerade abtretenden Arbeiter trifft.

## Caddy, nicht nginx

Die Dokumentation von nginx sagt selbst, dass sein Windows-Build „nur die
Verbindungsverarbeitungsmethoden `select()` und `poll()`“ verwendet, „sodass hohe
Leistung und Skalierbarkeit nicht zu erwarten sind“, und „als Beta-Version gilt“.

Caddy ist eine gepflegte native Binärdatei mit einer Admin-API — eine Website
hinzuzufügen ist damit ein HTTP-Aufruf an einen laufenden Server statt eines
Neustarts, der jede Verbindung im Flug fallen lässt. Es bringt außerdem eine
lokale Zertifizierungsstelle mit, und genau die macht HTTPS möglich, ohne den
Vertrauensspeicher der Maschine anzufassen.

## `*.localhost`, nicht `.test`

Windows und macOS lösen alles unter `.localhost` von sich aus auf die
Loopback-Adresse auf. Nichts wird in die hosts-Datei geschrieben und kein
DNS-Server ist beteiligt — das hält das Versprechen „ohne Administrator“
unversehrt.

`.test` löst ohne hosts-Eintrag oder DNS-Server nirgendwohin auf, und beides
kostet Rechte, die dieses Werkzeug nicht hat.

## Laufzeitumgebungen kommen von ihren Herausgebern, geprüft

Versionen werden gegen den Index des Herausgebers aufgelöst — die
`releases.json` von php.net, Release-Listen auf GitHub — und Archive gegen die
Prüfsummen abgeglichen, die diese Herausgeber angeben. Eine Abweichung löscht die
Datei: Alles, was hier heruntergeladen wird, wird danach ausgeführt.

Wo ein Herausgeber keine Prüfsumme anbietet, wird das festgehalten und berichtet
statt beschönigt. Die Windows-Builds von PostgreSQL, der Redis-Nachbau, die
Windows-Erweiterungen von PECL und die archivierten Ausgaben von php.net kommen
ungeprüft an, und das Werkzeug sagt es jedes Mal.

Die Testdaten sind von den Herausgebern abgenommen und nicht von Hand
geschrieben. Selbstgemachte Testdaten beweisen nur, dass der Parser mit seinem
Autor übereinstimmt, und die echten sind wiederholt abgewichen: Caddy
veröffentlicht **sha512** in einer Datei, die genau wie eine sha256-Liste
aussieht; MariaDB bewirbt einfaches HTTP und legt seine Prüfsumme unter
`sha256sum` ab; das Archiv von php.net schreibt denselben Compiler bei
verschiedenen Ausgaben desselben Zweigs als `vc15` und `VC15`.

## Heruntergeladene und vorgefundene Laufzeitumgebungen sind gleichrangig

Ein PHP, das dieses Werkzeug installiert hat, und ein PHP, das bereits auf der
Maschine war, sind für den Supervisor dasselbe. Das ist kein Notnagel: Genau so
bleibt handlungsfähig, wer eine Erweiterung braucht, die den fertigen Binärdateien
fehlt, und genau so erspart sich eine Maschine mit funktionierendem PHP ein
zweites.

Eine übernommene Laufzeitumgebung wird gelesen und nie beschrieben. Dieses
Werkzeug aktualisiert sie nicht, löscht sie nicht und installiert keine
Erweiterungen hinein.

## Ein Verzeichnis, und Sie wählen es

Laufzeitumgebungen, Konfiguration, Protokolle, Datenbankdateien, die
Zertifizierungsstelle von Caddy — alles in einem Verzeichnis. Es zu löschen
deinstalliert das Werkzeug vollständig.

Das wahr zu halten ist Arbeit, nicht Ordentlichkeit. Caddy schreibt von sich aus
seine Zertifizierungsstelle und seine automatisch gesicherte Konfiguration unter
`%AppData%`, also ganz außerhalb der Installation; beides ist umgeleitet, und die
automatische Sicherung ist abgeschaltet, weil sie außerdem der Weg ist, auf dem
eine veraltete Konfiguration zurückkommt.

Der Ort ist wählbar, weil die Voreinstellung nicht immer taugt: AppLocker ist
üblicherweise so eingerichtet, dass die Ausführung aus dem Benutzerprofil
verweigert wird, und alles hier Heruntergeladene ist eine ausführbare Datei.

## HTTPS ohne erhöhte Rechte

Caddy stellt Zertifikate aus einer lokalen Zertifizierungsstelle aus. Lässt man
es, installiert es deren Wurzelzertifikat selbst im Vertrauensspeicher der
Maschine und warnt dabei, es könne „nach einem Passwort fragen“ — eine
Systemänderung, die Administratorrechte braucht.

Das ist abgeschaltet. `portable trust` legt die Wurzel stattdessen in den
Speicher des **aktuellen Benutzers**, wofür keine Erhöhung nötig ist und was
genügt: Ein Zertifikat, dem die Person an der Maschine vertraut, ist genau der
Umfang, den das verdient.

HTTPS reißt HTTP nie mit. Der TLS-Port wird aus den freien gewählt, und ist
keiner frei, wird TLS schlicht nicht eingerichtet — ein TLS-Listener auf einem
belegten Port lässt Caddy gar nicht erst starten, und HTTP ist das Produkt,
während HTTPS eine Annehmlichkeit ist.

## Das Netz wird als schlecht angenommen

Jede Netzoperation wird bei vorübergehenden Fehlern wiederholt, und abgebrochene
Downloads setzen dort fort, wo sie stehen blieben. Das ist keine defensive
Verzierung: In einem Netz, in dem TLS-Handshakes mittendrin zurückgesetzt werden,
scheitert ein einzelner Versuch meistens, und ein neunzig Megabyte großes Archiv
von vorn zu beginnen heißt, es nie zu beenden.

Ein zu früh endender Datenstrom gilt ebenfalls nicht als fertiger Download. Der
Socket schließt, das Lesen liefert nichts, und ohne Längenprüfung endet die
Schleife zufrieden auf einer Datei, der die letzten dreißig Megabyte fehlen — was
weiter unten niemand bemerken würde, bei genau jenen drei Laufzeitumgebungen,
deren Herausgeber keine Prüfsumme anbieten.
