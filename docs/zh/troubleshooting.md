# 出问题的时候

先用这两条。它们加起来能回答大多数问题。

```powershell
.\portable.cmd status        # 什么在跑；若什么都没提供服务，那是为什么
.\portable.cmd version       # 这个构建、解释器、正在运行的守护进程
```

日志在 `portable home` 所指数据目录下的 `logs` 文件夹里 —— 每个进程一个文件。

## `php-cgi.exe` 起不来

消息会点名 PHP 并显示那个工作进程日志的末尾。在 Windows 上通常的原因是缺少
**Visual C++ Redistributable**：php.net 的构建链接了它，缺失时报为
`VCRUNTIME140.dll not found`。

安装它需要管理员权限，而这个工具不会去要。如果你装不了，
`portable install php --from C:\另一个\php` 可以接管机器上已经能用的 PHP。

## 什么都没提供服务

`portable status` 会说明原因。一个起来了、能列出站点、工作进程也在跑、除了这个问题
之外什么都能回答的监督进程，比一个没起来的更糟糕 —— 所以原因会被保留并在那里报告。

多数情况是端口的事。

## Caddy 在 80 或 8080 上起不来

按照实际发生频率排列：

1. **另一套本地环境在跑** —— Laragon、XAMPP、Docker Desktop。它们会把 80 和 8080
   一起占掉，看起来正是这样。
2. **IIS 或 WWW 发布服务**占着 80。
3. **端口落在 Windows 保留的区间里。** 没人在监听，绑定却照样失败。
   `netsh interface ipv4 show excludedportrange protocol=tcp` 会列出这些区间 ——
   Hyper-V 和 WSL 会动态保留。

```powershell
netstat -ano | findstr ":80 "     # 指出占用它的进程
.\portable.cmd port 8888          # 或者干脆换一个
```

绑不上的端口不会被记下来：之前那个会被放回去，你的站点也随之回去。

### 它说端口被占，而那个端口上确实有东西在应答

值得知道：一个程序可以在 IPv4 上占着端口，而 Caddy 拿到了 IPv6，反过来也一样。两边
都“成功”了，而发往 `127.0.0.1` 的请求落到了另一个程序那里。这个工具的检查方式是问
那个端口，答复是不是自己的 —— 所以它有时会拒绝一个看起来已经绑上的端口。

## 下载失败 —— `10054`、`10060`、`record layer failure`

```
SSLError: [SSL] record layer failure (_ssl.c:2660)
URLError: <urlopen error [WinError 10054] ...>
```

TLS 握手反复在中途被重置，通常是你的机器与对端**之间**的东西，而不是两端本身 ——
流量审查，或者一个过滤代理。它天生是断断续续的：同一条命令往往下一次就成功了。

所有操作都会以递增的间隔重试五次，中断的传输从断点续传而不是从头再来 —— 正是这一点
让九十兆的压缩包能在不断掉线的连接上最终到达。

总共五次，只算一层。1.3.0 之前，下载会把「已经重试五次并放弃」的连接再重试一遍，于是
一台不可达的主机被问了二十五次，每次都要各自等满超时。

如果仍然失败，消息会列出每一次尝试。五次一模一样的重置和五个不同的错误，含义并不相
同。

如果这台机器只能通过代理出网，就告诉它：`portable proxy set http://proxy.corp:3128`。
`HTTPS_PROXY` 一如既往会被采用，而在这里设置的会覆盖它。`portable version` 会显示当前
生效的是哪一个 —— 不带密码。

**对 `downloads.mariadb.org` 的 `WinError 10060`** 是连接超时而不是重置 —— 那台主机
在某些网络里根本不可达，重试只会让失败来得更慢。MariaDB 改从 `archive.mariadb.org`
获取，那里有同样的发行版本，旁边还带着校验和。

版本列表和下载都会退到那里 —— 以前只有列表会。列表只有几 KB，在一百兆过不去的网络上
往往还能通过，于是版本解析得好好的，下载却失败。`PORTABLE_MARIADB_ARCHIVE` 可以指向你
自己的镜像。

## `SSLCertVerificationError`，“证书无法验证”

在受管控的网络里，这通常意味着 TLS 在某个代理处终止，而这台机器不认识它的证书颁发
机构。把那个机构的根证书导出来并指向它：

```powershell
$env:PORTABLE_CA_BUNDLE = "C:\路径\corporate-root.pem"
```

没有跳过验证的开关，也不该有：这里下载的每一样东西之后都会被执行。

如果消息说这个 Python **一个**受信任的根证书都没有，那是另一回事 —— 解释器自己的存
储是空的。这在 Windows 上不会发生，那里 Python 读的是系统存储。

## GitHub 说超出了速率限制

Caddy、PostgreSQL 和 Redis 是通过 GitHub 的 API 解析的，匿名请求**按地址**每小时六
十次。在公司 NAT 后面，那是整栋楼六十次，而且可能被从没运行过这个工具的人用光。

```powershell
$env:PORTABLE_GITHUB_TOKEN = "ghp_..."
```

任何一个不带任何权限范围的令牌都行。它不需要权限，只需要一个身份。PHP 不受影响 ——
它发布在别处。

## Firefox 里 `https://` 仍然警告

Firefox 有自己的证书存储，既不读 Windows 的，也不读这个工具能触及的任何东西。只有在
`about:config` 里打开 `security.enterprise_roots.enabled` 它才会读 Windows 的存储 ——
那是你配置文件里的设置，这个工具没有资格去改。

Chrome、Edge 以及其他读系统存储的程序，`portable trust` 都能覆盖。

## 我一关终端，监督进程就死了

那说明那个终端把它放进了一个不允许它离开的**作业对象**，而 `portable up` 在启动
时已经说过了：

> This terminal put it in a job it could not leave, so closing this window may
> stop it.

作业对象是启动程序用来确保自己启动的一切在退出时被清理的手段，有些编辑器会为它们
的运行配置这样做。Windows 只有在作业对象允许时才让进程脱离它 —— 那是创建者设置
的一个标志 —— 而面对不允许的作业对象，进程层面无法逃脱。已在 Windows 上双向测
量，并且每次运行都会检查。

从普通的 PowerShell 窗口启动它，它就能挺过那个窗口的关闭。

脱离的其余部分都是有效的：控制台和进程组在任何情况下都会被留下，而那正是普通终端
关闭时会带走的东西。

## 扩展已启用，PHP 里却没有

`portable ext list` 会把它标为 **MISSING**：`php.ini` 加载了这个构建里没有的东西。
PHP 不会因此失败 —— 它在启动时警告一句，写进日志，然后不带这个扩展继续跑 —— 所以
症状否则会在几小时后以“某个函数不存在”的形式出现。

`portable ext install <名称>` 会取来真正匹配这个构建的那一个。

## 我挪了安装位置，东西全没了

改变存放位置不会搬动任何东西。改的时候会点名旧目录以及里面还剩什么 —— 把它复制过
去，或者重新安装再删掉它。什么都没丢。

## 报告问题

`portable status --json`、`portable version --json`，以及 `logs` 文件夹里相关的那个
文件。如果工具已经在错误消息里给你看了日志末尾，那段末尾通常就是全部。
