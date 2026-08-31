<img src="../logo.svg" alt="portable" width="240">

[![tests](https://img.shields.io/github/actions/workflow/status/dskripchenko/portable/tests.yml?branch=main&label=tests)](https://github.com/dskripchenko/portable/actions/workflows/tests.yml)
[![locked-down install](https://img.shields.io/github/actions/workflow/status/dskripchenko/portable/install.yml?branch=main&label=locked-down%20install)](https://github.com/dskripchenko/portable/actions/workflows/install.yml)
[![tag](https://img.shields.io/github/v/tag/dskripchenko/portable?label=tag&sort=semver)](https://github.com/dskripchenko/portable/tags)
[![release](https://img.shields.io/github/v/release/dskripchenko/portable?label=release)](https://github.com/dskripchenko/portable/releases/latest)
[![release scanned](https://img.shields.io/github/actions/workflow/status/dskripchenko/portable/virustotal.yml?label=release%20scanned)](https://github.com/dskripchenko/portable/releases/latest)
[![license](https://img.shields.io/github/license/dskripchenko/portable?label=license)](https://github.com/dskripchenko/portable/blob/main/LICENSE)

Локальная среда разработки для Windows — PHP, Caddy, PostgreSQL, MariaDB, Redis,
Node — которая ставится рядом с системой, а не в неё.

- [Начало работы](getting-started.md) — установить, поднять сайт, добавить базу
- [Команды](commands.md) — все команды и для чего каждая
- [Как это устроено](design.md) — решения, о которых стоит знать
- [Когда что-то не так](troubleshooting.md) — отказы, с которыми вы столкнётесь
  вероятнее всего, и что они означают

## Что значит «рядом с системой»

Каждый пункт — ограничение, под которым инструмент построен, а не пожелание:

- **Никаких прав администратора.** Ни при установке, ни при работе, никогда.
- **Никакого файла `hosts`.** Сайты доступны на `*.localhost`, который Windows
  сама разрешает в петлевой адрес.
- **Никаких служб и автозапуска.** Супервизор — процесс, который вы запускаете.
  Он переживает закрытие терминала и IDE; перезагрузку — нет.
- **Ни реестра, ни PATH, ни системных каталогов.** Всё лежит в одном каталоге.
  Его удаление полностью деинсталлирует инструмент.

В итоге это работает на зажатой корпоративной машине — то есть ровно там, где
инструменты такого рода обычно не устанавливаются вовсе.

## Состояние

В ежедневной работе на настоящей Windows: обслуживание PHP через пул, привязка к
порту 80 обычным пользователем, выживание при закрытии консоли, полноэкранная
панель и замена себя командой `upgrade` — последнее только с 1.3.2: тесты она
проходила месяцами, ни разу не доработав до конца на живой машине. Что там было
— в [примечании в конце README проекта](../../README.md).

Одно ограничение измерено, а не обещано. Терминал, помещающий запущенное в
**job-объект, запрещающий выход**, унесёт супервизор с собой при закрытии — на
уровне процессов из такого job не вырваться, поэтому `portable up` говорит, что
оказался в нём, а не оставляет это на потом. См.
[когда что-то не так](troubleshooting.md).

macOS и Linux целями не являются. Все каталоги разрешают Windows-архивы, так что
инструмент там запустится, но поставит двоичные файлы, которые та машина
выполнить не сможет.
