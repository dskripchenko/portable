# Befehle

Jeder Befehl kennt `--json` sowie `--home PFAD`, um einmalig gegen eine andere
Installation zu arbeiten. `portable help` gibt eine kürzere Fassung dieser Seite
aus.

## Der Supervisor

| | |
|---|---|
| `portable up` | Den Supervisor starten. Alles andere spricht mit ihm. |
| `portable down` | Ihn anhalten, und alles, was er betreibt. |
| `portable status` | Was läuft, auf welchem Port, und warum nichts ausgeliefert wird, falls nichts ausgeliefert wird. |
| `portable version` | Dieser Build, der Interpreter dahinter, wo die Daten liegen, und die Version des laufenden Daemons. |
| `portable help` | Alle Befehle, gruppiert, mit je einem Beispiel. |

`status` und `version` funktionieren auch ohne laufenden Daemon — also in genau
dem Zustand, in dem die Frage gewöhnlich aufkommt.

## Laufzeitumgebungen

| | |
|---|---|
| `portable available <name>` | Was der Herausgeber derzeit anbietet, mit Markierung des Installierten. |
| `portable available php 8.3` | Dieser Zweig, samt abgelöster Patches aus dem Archiv von php.net. |
| `portable install <name> [version]` | Ein Zweig (`8.4`), eine genaue Version (`8.4.24`) oder `latest`. |
| `portable install php --from C:\php` | Ein bereits vorhandenes PHP übernehmen. |
| `portable runtimes` | Was installiert ist. |
| `portable update [--install]` | Neuere Ausgaben auf derselben Linie wie das Installierte. |
| `portable uninstall <name> <version>` | Eine löschen und den Speicherplatz zurückgewinnen. |

Installierbar: `php`, `caddy`, `node`, `postgres`, `mariadb`, `redis`.

**Versionen werden nebeneinander installiert, nie übereinander.** Was an die
alte gebunden ist, läuft weiter — dafür gibt es `uninstall`.

**Aktualisierungen bleiben auf ihrer Linie** — das neueste `8.4.x` für ein 8.4,
niemals 8.5. Ein Wechsel des PHP-Zweigs bringt Veraltungen für jede Website, die
nichts festgelegt hat, und ein PostgreSQL-Datenverzeichnis gehört der Hauptversion,
die es angelegt hat: 17 startet nicht auf den Dateien von 18. Eine Linie
überschreitet man, indem man die Version nennt.

**Eine übernommene Laufzeitumgebung wird gelesen und nie beschrieben.** Das
Werkzeug aktualisiert sie nicht, löscht sie nicht und installiert keine
Erweiterungen hinein.

## Websites

| | |
|---|---|
| `portable site add <name> [pfad]` | Ein Verzeichnis unter `<name>.localhost` ausliefern. |
| `portable site add <name> <pfad> --exact` | Den Pfad wörtlich nehmen, ohne nach `public/` zu suchen. |
| `portable site add <name> <pfad> --php 8.2` | Eine Version festlegen. Voreingestellt ist die neueste installierte. |
| `portable site list` | Websites und ihre Adressen. |
| `portable site remove <name>` | Eine nicht mehr ausliefern. |
| `portable port 8888` | Der Port, auf dem ausgeliefert wird. `auto` kehrt zu 80, dann 8080 zurück. |
| `portable trust` | Der lokalen Zertifizierungsstelle vertrauen, damit `https://` nicht mehr warnt. `--forget` nimmt es zurück. |

Ein gewählter Port ist der **einzige**, der versucht wird. Nach einer Bitte um
8888 auf 8080 auszuweichen, würde die Website an eine Adresse legen, die Sie
nicht gewählt haben und von der Ihnen niemand erzählt hat — und man wählt einen
Port gerade deshalb, weil die Voreinstellungen nicht taugten.

## PHP-Erweiterungen

| | |
|---|---|
| `portable ext list` | Was dieser Build mitbringt und was davon geladen ist. |
| `portable ext enable <name>` | Eine laden, die der Build bereits mitbringt. |
| `portable ext disable <name>` | Sie nicht mehr laden. |
| `portable ext install <name> [version]` | Eine holen, die der Build nicht mitbringt — `xdebug`, `redis`, `imagick`. |

Alle kennen `--php 8.3`, um zu wählen, für welches installierte PHP sie gelten.

Windows-PHP liefert jede unterstützte Erweiterung als eigene DLL aus, alle
vorhanden und keine geladen — `enable` ist also eine Zeile in `php.ini` und kein
Download. `install` ist einer, passend zu PHP-Zweig, Thread-Sicherheit, Compiler
und Architektur des Builds; alle vier müssen übereinstimmen, sonst wird die
Erweiterung stillschweigend nicht geladen.

Eine Änderung ersetzt die Arbeiter, denn jedes `php-cgi` liest `php.ini` einmal
beim Start. Die `php.ini` selbst wird bei der Installation geschrieben und nie
neu erzeugt: Ihre Änderungen daran überleben alles hier Beschriebene.

Gibt es für Ihr PHP keinen Build der neuesten Ausgabe einer Erweiterung, nennen
Sie eine ältere. Xdebug 3 baut nicht für PHP 7.2 und wird es nie; xdebug 2.9.8
schon.

## Datenbanken

| | |
|---|---|
| `portable service add <art>` | `postgres`, `mariadb` oder `redis` starten. |
| `portable service list` | Was läuft und wie man es erreicht. |
| `portable service remove <name>` | Anhalten. **Die Daten bleiben.** |

Jede lauscht nur auf der Loopback-Adresse. `--port` und `--name` sind da, wenn
Sie eine zweite Instanz oder einen unüblichen Port wollen.

Das Datenverzeichnis wird einmal angelegt, bei PostgreSQL mit fester Sortierung
`C`: Eine Datenbank, deren Sortierung den Regionaleinstellungen der Maschine
folgt, sortiert anders als die Produktion — und das stellt sich in einem Test
heraus, der bei einer Person durchläuft.

## Alles Übrige

| | |
|---|---|
| `portable run <befehl>` | Etwas mit den installierten Laufzeitumgebungen im PATH ausführen, nur für diesen Befehl. |
| `portable env` | Die Einstellungen ausgeben, die eine Shell bräuchte, statt etwas zu ändern. |
| `portable home` | Wo alles liegt und was das entschieden hat. |
| `portable home set <pfad>` | Woanders ablegen. `--beside` legt es neben den Starter. |
| `portable home clear` | Zurück zur Voreinstellung. |

`portable run node --version` sagt Ihnen, wenn es ein Node ausführt, das dieses
Werkzeug nicht verwaltet — das der Maschine, im PATH gefunden. Damit lassen sich
sonst leicht zwanzig Minuten verbringen.
