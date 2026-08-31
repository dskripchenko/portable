<img src="../logo.svg" alt="portable" width="240">

[![tests](https://img.shields.io/github/actions/workflow/status/dskripchenko/portable/tests.yml?branch=main&label=tests)](https://github.com/dskripchenko/portable/actions/workflows/tests.yml)
[![locked-down install](https://img.shields.io/github/actions/workflow/status/dskripchenko/portable/install.yml?branch=main&label=locked-down%20install)](https://github.com/dskripchenko/portable/actions/workflows/install.yml)
[![tag](https://img.shields.io/github/v/tag/dskripchenko/portable?label=tag&sort=semver)](https://github.com/dskripchenko/portable/tags)
[![release](https://img.shields.io/github/v/release/dskripchenko/portable?label=release)](https://github.com/dskripchenko/portable/releases/latest)
[![release scanned](https://img.shields.io/github/actions/workflow/status/dskripchenko/portable/virustotal.yml?label=release%20scanned)](https://github.com/dskripchenko/portable/releases/latest)
[![license](https://img.shields.io/github/license/dskripchenko/portable?label=license)](https://github.com/dskripchenko/portable/blob/main/LICENSE)

面向 Windows 的本地开发环境 —— PHP、Caddy、PostgreSQL、MariaDB、Redis、Node ——
它安装在系统**旁边**，而不是装进系统里。

- [快速开始](getting-started.md) —— 安装、跑起一个站点、加一个数据库
- [命令](commands.md) —— 每条命令，以及它是干什么的
- [工作原理](design.md) —— 值得了解的那些取舍
- [出问题的时候](troubleshooting.md) —— 你最可能遇到的故障，以及它们意味着什么

## “在系统旁边”是什么意思

以下每一条都是这个工具据以构建的约束，而不是愿望：

- **不需要管理员权限。** 安装时不需要，运行时不需要，任何时候都不需要。
- **不碰 `hosts` 文件。** 站点通过 `*.localhost` 访问，Windows 自己就会把它解析
  到回环地址。
- **没有服务，没有自启动。** 监督进程由你启动。它能挺过关闭终端和 IDE；挺不过
  重启。
- **不碰注册表、PATH 和系统目录。** 一切都在一个目录里。删掉它就等于彻底卸载。

结果是它能在被严格管控的公司电脑上运行 —— 而那正是这类工具通常根本装不上的地方。

## 现状

在真实 Windows 上日常使用：通过进程池提供 PHP 服务、以普通用户身份绑定 80 端口、
在控制台关闭后继续存活、全屏面板，以及用 `upgrade` 替换自身 —— 最后这一项要到
1.4.2 才真正成立：它的测试通过了好几个月，却从未在真机上跑完过一次，而且在那之前
两次被宣布修好。经过见[项目 README 末尾的说明](../../README.md)。

有一项限制是测量出来的，而不是承诺出来的。如果终端把它启动的东西放进一个**不允许
脱离的作业对象**，那么关闭时会把监督进程一起带走 —— 面对这样的作业对象，进程层面
无法逃脱，所以 `portable up` 会告诉你它正处于其中，而不是留给你以后发现。见
[出问题的时候](troubleshooting.md)。

macOS 和 Linux 不是目标平台。所有目录解析的都是 Windows 压缩包，所以工具在那里能
跑起来，但装下来的是那台机器无法执行的二进制文件。
