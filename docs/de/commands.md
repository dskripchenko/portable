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
| `portable dash` | Vollbildansicht: Prozesse, Websites, Datenbanken und laufende Protokolle zusammen. |
| `portable shell` | Befehle nacheinander ausführen, ohne jedes Mal `portable` zu tippen. |
| `portable logs [name] [-f]` | Was die beaufsichtigten Prozesse schreiben. Ein Name oder dessen Anfang. |
| `portable upgrade [--check]` | Dieses Werkzeug durch die neueste Ausgabe ersetzen. |

`status` und `version` funktionieren auch ohne laufenden Daemon — also in genau
dem Zustand, in dem die Frage gewöhnlich aufkommt.

`upgrade` lädt die neueste Ausgabe, prüft sie gegen die daneben veröffentlichte
Prüfsumme und startet sie einmal, bevor irgendetwas Vorhandenes angefasst wird.
Erst dann wird die alte Installation beiseitegeschoben und die neue an ihre
Stelle gesetzt — von der Shell des Systems, aus einem Skript außerhalb beider,
denn Windows benennt kein Verzeichnis um, in dem ein Programm läuft.

Die vorherige Version bleibt neben der neuen liegen, bis Sie sie löschen, und
scheitert der Tausch, kommt die alte zurück. Ein Werkzeug, das bloß veraltet
ist, ist weit besser als eines, das nicht da ist.

`logs php -f` folgt allen Arbeitern aller PHP-Versionen zugleich, denn ein Name
trifft sowohl ein ganzes Protokoll als auch dessen Anfang — so denkt man über
einen Pool, nicht als `php-8.4.24-1` bis `-4`. Zeilen sind mit ihrem Urheber
beschriftet und danach eingefärbt, wie beunruhigend sie klingen.

Die Dateien werden direkt gelesen, das funktioniert also auch bei angehaltenem
Daemon — und dann wird die Frage meist gestellt.

`shell` hat keine Tab-Vervollständigung. `readline` ist eine Unix-Erweiterung,
die Windows-Builds von CPython nicht mitbringen; Vervollständigung bräuchte
also eine Bibliothek, und das Bündel hat bewusst keine Abhängigkeiten. Verlauf
und Pfeiltasten liefert die Konsole.

`dash` zeigt alles auf einmal, weil die Antworten meist zusammen gebraucht
werden: welcher Arbeiter starb, ist eine Frage ans Protokoll; ob er zurückkam,
eine an die Prozesstabelle — und sie nacheinander zu lesen heißt, zwischen zwei
Befehlen zu wechseln, während sich das Beobachtete bewegt. `q` beendet, `r`
aktualisiert, `f` pausiert das Protokoll. `dash php` folgt unten nur PHP.

Es ist der einzige Teil des Werkzeugs mit Bibliotheken außerhalb der
Standardbibliothek. Vier davon, ins Bündel eingelegt: `textual` nennt sechs
weitere, nach denen hier nichts greift, und sie mitzuführen hieße, viereinhalb
Megabyte Syntax-Lexer für einen Bildschirm mitzunehmen, der nichts hervorhebt.
Ein Test blockiert deren Importe und startet die Ansicht trotzdem, damit das so
bleibt.

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

## Mehrere Versionen gleichzeitig

Jede installierte PHP-Version kann gleichzeitig ausliefern. Jede bekommt einen
eigenen Arbeiter-Pool und eine eigene `php.ini`; Websites wählen mit `--php`
zwischen ihnen, und eine Website ohne Festlegung folgt der jeweils neuesten.

Bei Datenbanken gilt dasselbe, nur je Instanz statt je Version: `--name` gibt
einer zweiten ein eigenes Datenverzeichnis und einen eigenen Port, `--version`
legt fest, welchen installierten Build sie ausführt.

`purge` macht „den Ordner löschen und es ist weg" wieder wahr. Vier Dinge können
außerhalb landen — das Datenverzeichnis, ein PATH-Eintrag, ein vertrautes
Zertifikat und die Kopie, die ein Upgrade behalten hat — und an drei davon
erinnert sich niemand. Der Befehl findet, was wirklich da ist, listet es mit
Größen auf, fragt nach und entfernt es.

Den Ordner selbst entfernt er nicht: Dies läuft aus ihm heraus, und Windows
löscht kein Verzeichnis, in dem ein Programm läuft. Danach ist dieser Ordner das
Einzige, was übrig ist.

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
| `portable path` | Ob dies im PATH steht. |
| `portable purge` | Alles entfernen, was außerhalb des eigenen Ordners abgelegt wurde. |
| `portable path add` | Eintragen — für **Sie**, ohne Administrator. `remove` nimmt es zurück. |

`path add` schreibt nach `HKEY_CURRENT_USER` — in Ihre eigene Umgebung, wofür
keine Administratorrechte nötig sind. Der PATH der Maschine liegt woanders und
wird nie angefasst; eine Option dafür gibt es nicht.

Es ist das Einzige, was dieses Werkzeug außerhalb seines eigenen Verzeichnisses
schreibt — deshalb tun Sie es und nicht die Installation, und deshalb stellt
`path remove` genau den vorherigen Zustand wieder her.

`portable run node --version` sagt Ihnen, wenn es ein Node ausführt, das dieses
Werkzeug nicht verwaltet — das der Maschine, im PATH gefunden. Damit lassen sich
sonst leicht zwanzig Minuten verbringen.
