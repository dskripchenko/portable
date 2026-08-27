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
Erst dann werden die alten Dateien beiseitegeschoben und die neuen an ihre
Stelle gesetzt.

Der Ordner selbst wird nie umbenannt, und das mit Absicht: Windows weigert sich,
ein Verzeichnis umzubenennen, das für irgendeinen Prozess das aktuelle ist — und
beim Aktualisieren ist es das gewöhnlich, denn die Shell, in die Sie den Befehl
getippt haben, steht darin. Nur der Inhalt zieht um. Was im Ordner nicht zum
Bündel gehört, bleibt genau dort, auch das Datenverzeichnis, wenn
`home set --beside` es dorthin gelegt hat.

Die vorherige Version bleibt neben der neuen liegen, bis Sie sie löschen, und
scheitert der Tausch, kommt alles zurück. Ein Werkzeug, das bloß veraltet ist,
ist weit besser als eines, das nicht da ist.

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
Befehlen zu wechseln, während sich das Beobachtete bewegt.

Man tippt auch hinein. Befehle kommen unten hinein, ohne `portable` davor, und
das Getippte samt Antwort reiht sich in die Ausgabe der Dienste ein, in der
Reihenfolge des Geschehens — das macht aus „ich habe eine Website hinzugefügt,
und dann erschien dies im Protokoll" etwas Lesbares statt etwas zu
Rekonstruierendes. Die Pfeiltasten holen Getipptes zurück; beim Tippen gibt es
Vorschläge.

`F10` beendet, `F5` aktualisiert, `F2` pausiert. Funktionstasten, weil die
Buchstaben jetzt der Befehlszeile gehören. `dash php` folgt unten nur PHP. Eine
Datenbank in ihrer Tabelle auswählen und Enter drücken öffnet eine
Eingabeaufforderung dazu.

Oben rechts sitzt ein Gesicht, und es beantwortet dieselbe Frage wie der Block
daneben — ist etwas nicht in Ordnung —, nur aus der Entfernung: eine sich
ändernde Form sieht man quer durch den Raum, eine Textzeile nicht.

```
|\---/|      |\---/|      |\---/|      |\---/|
| o o |      | o o |      | ^ ^ |      | x x |
|  >_ |      |  .. |      | \_/ |      |  >! |
'-----'      '-----'      '-----'      '-----'
 arbeitet      wartet      geschafft    Fehler

|\---/|      |\---/|      |\---/|      |\---/|
| o o |      | - - |      | O O |      | - - |
|  >_ |      |  >_ |      |  >o |      |  zZ |
'-----'      '-----'      '-----'      '-----'
  bereit      blinzelt    ist weg      gestoppt
```

Es meldet Zustände, keine Tätigkeiten. Welcher Befehl läuft, steht schon auf der
Leiste darunter, mit Namen und gezählten Sekunden; ein Gesicht, das dasselbe
wiederholt, sagt es ungenauer, und eines, das sich bei jeder Handlung ändert,
hieße bald nicht mehr „hierher sehen“.

- **wartet** — der Befehl hat fünf Sekunden nichts gesagt. Der größte Teil einer
  Installation vergeht mit Warten auf fremde Hosts, und der Zeiger dreht sich
  dabei gleich: das ist der Unterschied zwischen langsamem Laden und einer
  Zeitüberschreitung, die wie ein Hänger aussieht.
- **geschafft** und **Fehler** — womit der getippte Befehl zurückkam. Ein Fehler
  bleibt, bis etwas gelingt, statt nach einer Weile zu verblassen: ein Gesicht,
  das von selbst aufheitert, meldet den Lauf der Zeit und nicht den Zustand von
  irgendetwas.
- **ist weg** — der Supervisor war eben noch da, und niemand hat ihn gebeten zu
  gehen. Das ist eine Nachricht; dass er beim Öffnen nicht lief, ist keine.
- **blinzelt** — alle fünfundzwanzig Sekunden, kurz. Dasselbe Argument wie beim
  Arbeitszeiger: ein Standbild unterscheidet Aufmerksamkeit nicht von einem
  hängenden Prozess.

Dieselbe Figur wie im Logo, und mit Absicht aus ASCII: ein Terminal ohne die
Schrift für Rahmenzeichen zeigt Kästchen, und ein Maskottchen aus Kästchen lässt
den Bildschirm genau dann kaputt aussehen, wenn er beruhigen soll. Unter
vierundzwanzig Zeilen verschwindet es samt den drei Zeilen daneben: Zeilen sind
hier das Knappe, und ein Bild zum Preis einer Tabellenzeile ist ein schlechter
Tausch.


Ein paar Befehle werden von dort abgelehnt, jeder mit Begründung: `upgrade`
ersetzt den Ordner, aus dem die Ansicht läuft, `purge` stellt eine Frage, die
diese Ansicht nicht stellen kann, und `logs -f` ist das, was der untere Bereich
ohnehin tut.

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

`trust` legt die Wurzel der lokalen Instanz in **Ihren** Zertifikatspeicher —
den einzigen ohne Administratorrechte erreichbaren — und schreibt
`conf/ca-bundle.pem`: die eigenen vertrauten Wurzeln dieser Maschine samt jener
Wurzel. Jedes installierte PHP wird darauf ausgerichtet.

PHP, curl und Node lesen ihre eigenen Listen statt des Systemspeichers. So
öffnet eine Seite in Chrome grün, während
`file_get_contents('https://api.localhost')` aus dem Code derselben Seite am
Zertifikat scheitert. `portable run` und `portable env` reichen dieselbe Datei
an curl und Node weiter.

Die Wurzeln der Maschine stehen mit Absicht darin: ein Bündel mit nur der
lokalen Instanz ließe PHP `api.localhost` vertrauen und jedes öffentliche
Zertifikat ablehnen. Lassen sie sich nicht einsammeln, wird nichts geschrieben,
und `trust` sagt es.

Dafür muss `trust` nicht erneut laufen: der Supervisor schreibt die Datei beim
Start, sobald die Instanz existiert und die Datei fehlt oder älter ist.

Sind 443 und 8443 beide belegt, wird gar kein HTTPS-Listener gestartet — HTTP
bleibt unberührt. `status` sagt das und nennt, wer sie hält.

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
| `portable service cli [name]` | Eine Eingabeaufforderung dazu — `psql`, `mariadb`, `redis-cli`. |
| `portable service remove <name>` | Anhalten. **Die Daten bleiben.** |

Mehrere Versionen derselben Datenbank laufen nebeneinander: jede ist ein Dienst
mit eigenem Namen, eigener Version und eigenem Port, und `service cli <name>`
wählt über den Namen, nicht über die Version. `service list` zeigt, auf welcher
Version jeder tatsächlich läuft — nicht immer die angeforderte, denn ein ohne
Version angemeldeter Dienst folgt der neuesten installierten.

```powershell
portable service add postgres --name pg16 --version 16 --port 5432
portable service add postgres --name pg17 --version 17 --port 5433
portable service cli pg17
```

`service cli` startet den Client aus demselben Archiv wie den Server, gerichtet
auf den richtigen Port über TCP — und zwar den Client der laufenden Version,
nicht den der neuesten auf der Maschine. Alle drei schreiben dieselben drei Angaben —
Host, Port, Benutzer — auf drei verschiedene Weisen, und der MariaDB-Client
bevorzugt unter Windows zusätzlich eine benannte Pipe, wenn der Host lokal
aussieht — die dieser Server nicht anbietet. Der Name kann entfallen, wenn es
nur eine gibt.

In der Ansicht wählen Sie eine Datenbank in ihrer Tabelle und drücken Enter:
der Bildschirm tritt beiseite, der Client bekommt das echte Terminal, und der
Bildschirm kommt zurück, wenn Sie ihn verlassen. `--json` druckt den Befehl,
statt ihn auszuführen — was ein Editor-Plugin möchte.

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
| `portable proxy` | Worüber alles Ausgehende läuft und wer das entschied. |
| `portable proxy set <url>` | Downloads, Kataloge und Aktualisierungsprüfungen darüber. |
| `portable proxy clear` | Wieder der Umgebung folgen. |
| `portable home clear` | Zurück zur Voreinstellung. |
| `portable path` | Ob dies im PATH steht. |
| `portable purge` | Alles entfernen, was außerhalb des eigenen Ordners abgelegt wurde. |
| `portable path add` | Eintragen — für **Sie**, ohne Administrator. `remove` nimmt es zurück. |

`proxy` ist für die Maschine, die einen braucht und ihn nie exportiert bekommen
hat. `HTTPS_PROXY` wird wie bisher beachtet; ein hier gesetzter hat Vorrang,
denn zwei Quellen für eine Antwort heißt, dass die halbe Zeit die falsche gilt
und nichts sagt welche.

```powershell
portable proxy set http://proxy.corp:3128
portable proxy set http://bob:secret@proxy.corp:3128    # falls er ein Passwort will
```

Es gilt für alles Geholte: Laufzeiten, PHP-Erweiterungen, die Kataloge der
Anbieter, GitHubs API und die Aktualisierungsprüfung. Das Schema ist `http://`
auch bei einem Proxy, der `https` holt — diese Adresse sagt, wie dieser Proxy
erreicht wird, nicht wie er das Ziel erreicht. SOCKS wird abgelehnt statt
angenommen und später zu scheitern: mit Proxys spricht hier Pythons
Standardbibliothek, und die kann nur HTTP-Proxys.

Ein Passwort wird gespeichert wie angegeben und nie gedruckt: `proxy` und
`version` zeigen `bob:***@`, damit es nicht in einem eingefügten Fehlerbericht
landet.


`path add` schreibt nach `HKEY_CURRENT_USER` — in Ihre eigene Umgebung, wofür
keine Administratorrechte nötig sind. Der PATH der Maschine liegt woanders und
wird nie angefasst; eine Option dafür gibt es nicht.

Es ist das Einzige, was dieses Werkzeug außerhalb seines eigenen Verzeichnisses
schreibt — deshalb tun Sie es und nicht die Installation, und deshalb stellt
`path remove` genau den vorherigen Zustand wieder her.

`portable run node --version` sagt Ihnen, wenn es ein Node ausführt, das dieses
Werkzeug nicht verwaltet — das der Maschine, im PATH gefunden. Damit lassen sich
sonst leicht zwanzig Minuten verbringen.
