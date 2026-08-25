# 快速开始

## 安装

在普通的 PowerShell 窗口里：

```powershell
irm https://raw.githubusercontent.com/dskripchenko/portable/main/install.ps1 | iex
```

要指定位置或版本：

```powershell
$env:PORTABLE_INSTALL_DIR = 'D:\portable'
$env:PORTABLE_VERSION = '0.1.1'
irm https://raw.githubusercontent.com/dskripchenko/portable/main/install.ps1 | iex
```

用管道交给 `iex` 而不是存下来再执行，这不是风格偏好：在 `Restricted` 执行策略下
——没人动过的机器上的默认值，也是受管控机器上最常被强制的设置——磁盘上的 `.ps1`
文件不会运行，而字符串会。

它不需要原装 Windows 之外的任何东西，会用发布方在旁边公布的校验和核对压缩包，在
宣布成功之前先运行它一次，并且拒绝覆盖已有的安装 —— 那是 `portable upgrade` 的
事。

### 从任何地方运行

把安装目录加进 PATH 是可行的，别的什么都不需要：启动器按自己所在的位置去找自己的
解释器和自己的设置，而不是按你所在的位置。这时 `portable site add demo .` 指的就
是你当前所在的目录，`portable run` 也在那里执行。

而且这个目录不会被谁占住。守护进程以及它监督的一切都待在安装目录里：进程会占住自
己的工作目录，而在 Windows 上被占住的文件夹既不能删除也不能重命名 —— 资源管理器
只会说它“已在另一个程序中打开”。

**不会碰你的 PATH。** 这个工具的承诺是删掉它的目录就等于彻底移除，而 PATH 里的
一条记录会是这个承诺的例外。如果你仍然想要，设置
`$env:PORTABLE_ADD_TO_PATH = '1'`；那是安装器唯一写在安装目录之外的东西。

### 如果被封的是 PowerShell 本身

有些机器锁住的不是这个工具，而是 PowerShell：AppLocker 和 WDAC 会把它置于
Constrained Language Mode，在那里连 `Get-FileHash` 和 `Expand-Archive` 都拒绝
运行。上面写的一切都不是必需的。在 `cmd` 里：

```bat
curl -fsSL -o portable.zip https://github.com/dskripchenko/portable/releases/latest/download/portable-windows-x64.zip
curl -fsSL -o portable.zip.sha256 https://github.com/dskripchenko/portable/releases/latest/download/portable-windows-x64.zip.sha256

certutil -hashfile portable.zip SHA256
type portable.zip.sha256

tar -xf portable.zip
```

自己比对这两个哈希，然后运行出现的文件夹里的 `portable.cmd`。整个过程不执行任何
脚本，所以执行策略、语言模式和脚本规则都与此无关。`curl.exe`、`tar.exe` 和
`certutil.exe` 自 Windows 10 1803 起就在 `System32` 里。

`-f` 很重要：没有它，curl 会把“未找到”的页面存成压缩包并报告成功，随后 `tar`
会去抱怨压缩包，而不是地址。

### 或者手动下载

从[发布页](https://github.com/dskripchenko/portable/releases)下载
`portable-x86_64-pc-windows-msvc.zip`，解压到任何地方 —— 桌面上的一个文件夹、第二
块硬盘、U 盘都行。没有什么要安装的，也没有安装程序。

这个包自带 Python。一个专职把运行时装到没有运行时的机器上的工具，不该反过来先要
求你已经有一个；而 Windows 上默认根本没有：看起来像 `python` 的那个东西，其实是打
开 Microsoft Store 的快捷方式。

```powershell
cd C:\portable
.\portable.cmd version
```

在普通的 PowerShell 窗口里运行。如果它什么时候向你要管理员权限，那是缺陷，请报告。

## 选择它把东西放在哪

```powershell
.\portable.cmd home                    # 在哪，以及是什么决定的
.\portable.cmd home set D:\portable    # 从现在起放到那里
.\portable.cmd home set --beside       # 放在启动器旁边，随它一起走
```

默认是 `%LOCALAPPDATA%\portable`。在受管控的机器上，这个默认值可能不只是不合意，
而是根本不能用：AppLocker 的常见配置会拒绝从用户配置目录下执行程序 —— 不用管理员
权限安装的软件正是落在那里，这也正是那条规则的用意 —— 而这里下载的每一样东西都是
可执行文件。凡是这条规则生效的地方，不把位置挪走就什么都启动不了。

`--beside` 是为 U 盘准备的。它记下的是这个词，而不是今天的路径，所以盘符变了包依
然能用。

## 启动

```powershell
.\portable.cmd up
```

这会启动监督进程，其余一切都归它管。它能挺过关闭终端和 IDE，挺不过重启 —— 这是有
意为之：要挺过重启就得写自启动项，而这个工具不写。

## 跑起一个站点

```powershell
.\portable.cmd install php
.\portable.cmd install caddy
.\portable.cmd site add demo C:\projects\demo
```

打开 `http://demo.localhost`。没有改过 hosts，也没有 DNS 服务器参与：Windows 自己
就把 `.localhost` 下的一切解析到回环地址。

如果项目把前端控制器放在 `public/` 里 —— Laravel、Symfony 以及大多数框架 —— 提供服
务的就是它，工具会告诉你。否则把站点指向仓库根目录，会把应用源码连同 `.env` 一起
通过 HTTP 交出去，而表面上看只像是“没跑起来”。加 `--exact` 就按字面取用路径。

用 `--php 8.2` 为每个站点固定 PHP 版本；不加则跟随最新的那个。多个版本并存，各有
各的工作进程池。

## HTTPS

```powershell
.\portable.cmd trust
```

站点同时通过 TLS 提供服务，证书来自 Caddy 在本地运行的证书颁发机构。`trust` 把该
机构的根证书放进**你的**证书存储 —— 不是机器的那个，那需要管理员权限。

Windows 会弹出确认对话框。那是 Windows 在问，不是这个工具，而且也不该有绕过它的
办法。

Firefox 有自己的存储，仍然会警告。只有在 `about:config` 里打开
`security.enterprise_roots.enabled` 它才会读 Windows 的存储 —— 那是你配置文件里的
设置，这个工具没有资格去改。

## 加一个数据库

```powershell
.\portable.cmd install postgres
.\portable.cmd service add postgres
```

它会在 `127.0.0.1:5432` 上启动，用户 `postgres`，无密码 —— 并且只绑定回环地址，因
为在可从网络访问的端口上用 trust 认证，正是一台笔记本在会议 WiFi 上变成别人的东西
的方式。

`service remove` 停掉它并**保留数据**。再加回来就从原处继续。

`mariadb` 和 `redis` 同理，端口是 3306 和 6379。

## Node 和其他工具

```powershell
.\portable.cmd install node
.\portable.cmd run npm install
```

`portable run` 只为这一条命令把已安装的运行时放进 PATH。机器本身的 PATH 不受影响。
如果你想要整个 shell 会话的设置，`portable env` 会把它们打印出来。

## 停止

```powershell
.\portable.cmd down
```

全部停止：路由器、PHP 工作进程、数据库。命令返回时端口已经空出来了。

要彻底移除这个工具，删掉它存放数据的目录（`portable home` 会告诉你是哪个）和你解压
出来的文件夹。此外别无他物：没有注册表项，没有服务，没有对 PATH 的改动。
