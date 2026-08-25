# Install portable.
#
#   irm https://raw.githubusercontent.com/dskripchenko/portable/main/install.ps1 | iex
#
# Piped into `iex` rather than saved and run, and that is not a stylistic
# preference. Under the `Restricted` execution policy - the default on a machine
# nobody has changed, and the setting most often enforced on a managed one - a
# .ps1 file on disk will not run, while a string does. Measured on Windows, not
# assumed.
#
# Nothing here needs administrator rights, and nothing outside the install
# directory is written to. To choose where it goes, or which version:
#
#   $env:PORTABLE_INSTALL_DIR = 'D:\portable'
#   $env:PORTABLE_VERSION = '0.1.1'
#   irm https://raw.githubusercontent.com/dskripchenko/portable/main/install.ps1 | iex
#
# ASCII only, deliberately. Windows PowerShell reads a script without a byte
# order mark as ANSI, and anything else comes out as mojibake on a machine whose
# code page differs from the one it was written on.
#
# Constrained Language Mode is respected throughout, which rules out a few
# ordinary things. AppLocker and WDAC put PowerShell into that mode on managed
# machines, and in it .NET method calls on anything but a handful of core types
# are refused - `[IO.Path]::GetTempPath()`, `[Math]::Round()`, and, most
# awkwardly, `Get-FileHash`, which cannot create the hashing type it needs. So
# the checksum is computed by `certutil`, an ordinary program that no language
# mode applies to.
#
# If even this is blocked, nothing here is required: see the script-free
# instructions in the documentation, which are two commands using `curl.exe` and
# `tar.exe`.

$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 still negotiates TLS 1.0 by default on some builds, and
# GitHub has not accepted that for years. The failure is a closed connection with
# no explanation, which sends people looking at their firewall.
#
# Guarded, because assigning to a property is refused under Constrained Language
# Mode - reading one is allowed, which is how this got past a first measurement.
# Nothing is lost: TLS 1.2 has been the default since Windows 10 and .NET 4.7,
# and the machines old enough to need the assignment are not the ones running
# WDAC.
if ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
}

# Invoke-WebRequest draws a progress bar by repainting the console for every
# chunk, and on a forty-megabyte download that costs more than the download.
$ProgressPreference = 'SilentlyContinue'

function Fail($message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Red
    Write-Host ""

    # `throw`, not `exit`. This script is executed by `iex` inside the person's
    # own shell, and `exit` there ends the shell - closing the window on the
    # explanation that was just printed, which is the one moment they need to
    # read it. `throw` stops this and leaves them where they were.
    throw 'portable was not installed.'
}

if ($env:OS -ne 'Windows_NT') {
    Fail "portable is a Windows tool. Every runtime it installs is a Windows binary."
}

if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) {
    Fail @"
This needs tar.exe, which Windows has carried in System32 since Windows 10 1803.
On anything older, download the bundle from
https://github.com/dskripchenko/portable/releases and unzip it with Explorer.
"@
}

if ($env:PROCESSOR_ARCHITECTURE -notin @('AMD64', 'IA64')) {
    Fail @"
Only 64-bit x86 is published, and this machine reports $env:PROCESSOR_ARCHITECTURE.
On ARM Windows the x64 build runs under emulation but the PHP and database
binaries it fetches are x64 too, so nothing is gained by pretending.
"@
}

$repository = 'dskripchenko/portable'
$target = 'x86_64-pc-windows-msvc'
$destination = if ($env:PORTABLE_INSTALL_DIR) { $env:PORTABLE_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'portable' }

$headers = @{ 'User-Agent' = 'portable-installer' }

# Sixty anonymous requests an hour, counted per address - which behind a
# corporate NAT is sixty for the building. Any token with no scopes at all
# raises it; it needs no permissions, only an identity.
if ($env:PORTABLE_GITHUB_TOKEN) {
    $headers['Authorization'] = "Bearer $env:PORTABLE_GITHUB_TOKEN"
}

$releaseUrl = if ($env:PORTABLE_VERSION) {
    "https://api.github.com/repos/$repository/releases/tags/v$env:PORTABLE_VERSION"
} else {
    "https://api.github.com/repos/$repository/releases/latest"
}

Write-Host "Asking what the newest version is..."

try {
    $release = Invoke-RestMethod -Uri $releaseUrl -Headers $headers
} catch {
    Fail @"
Could not reach GitHub's API: $_

If this is a rate limit, any token with no scopes will lift it:
    `$env:PORTABLE_GITHUB_TOKEN = 'ghp_...'
"@
}

$version = $release.tag_name -replace '^v', ''
$bundle = $release.assets | Where-Object { $_.name -like "*-$target.zip" } | Select-Object -First 1
$digest = $release.assets | Where-Object { $_.name -like "*-$target.zip.sha256" } | Select-Object -First 1

if (-not $bundle) {
    Fail "Release $version has no $target bundle attached to it."
}

if (-not $digest) {
    Fail "Release $version publishes no checksum beside its bundle, and this is a program about to be run."
}

# Refusing rather than overwriting. An install that lands on top of an existing
# one leaves whichever files the new version happens not to have, and the tool
# already knows how to replace itself properly.
if (Test-Path (Join-Path $destination 'portable.cmd')) {
    $existing = & (Join-Path $destination 'portable.cmd') version --json 2>$null | ConvertFrom-Json

    Fail @"
portable $(if ($existing) { $existing.version } else { '' }) is already installed in $destination.

To update it:
    $destination\portable.cmd upgrade

To install elsewhere:
    `$env:PORTABLE_INSTALL_DIR = 'D:\portable'
"@
}

$work = Join-Path $env:TEMP "portable-install-$(New-Guid)"
New-Item -ItemType Directory -Path $work -Force | Out-Null

try {
    $archive = Join-Path $work $bundle.name

    Write-Host "Downloading $($bundle.name) ($('{0:N0}' -f ($bundle.size / 1MB)) MB)..."
    Invoke-WebRequest -Uri $bundle.browser_download_url -OutFile $archive -Headers $headers -UseBasicParsing

    # To a file and then read back, rather than through `.Content`. GitHub
    # serves the checksum as application/octet-stream, so `.Content` is a byte
    # array - and splitting a byte array on whitespace yields the first byte
    # rendered as a number. It compared "50" against the hash and reported a
    # mismatch, 50 being the code of "2", the first character of the digest.
    #
    # `-UseBasicParsing` on both, or Windows PowerShell hands the response to
    # Internet Explorer's engine to build a DOM out of - and Windows 11 has no
    # Internet Explorer, so it throws "Object reference not set to an instance of
    # an object" instead.
    $digestFile = Join-Path $work $digest.name
    Invoke-WebRequest -Uri $digest.browser_download_url -OutFile $digestFile -Headers $headers -UseBasicParsing

    $published = ((Get-Content -Path $digestFile -Raw) -split '\s+')[0]

    # certutil rather than Get-FileHash. Under Constrained Language Mode -
    # which AppLocker and WDAC impose on managed machines - Get-FileHash fails
    # with "Cannot create type", because it builds the hashing object through
    # .NET. certutil is a program rather than a cmdlet, so no language mode
    # applies to it, and it has been in System32 for twenty years.
    $actual = (certutil -hashfile $archive SHA256 | Select-Object -Skip 1 -First 1).Replace(' ', '').ToLower()

    if ($actual -ne $published.ToLower()) {
        Fail @"
The download does not match the checksum the release publishes.
  expected: $published
  received: $actual
It has been discarded. Nothing was installed.
"@
    }

    Write-Host "Checksum matches. Unpacking..."

    # tar rather than Expand-Archive. Expand-Archive is itself written in
    # PowerShell and calls .NET methods internally, so it fails under
    # Constrained Language Mode the same way everything else does - and unlike
    # the earlier cases, `Get-Command Expand-Archive` succeeds, which is how a
    # first measurement said it was fine. tar.exe is bsdtar, reads zip, and has
    # been in System32 since Windows 10 1803.
    & tar.exe -x -f $archive -C $work

    if ($LASTEXITCODE -ne 0) {
        Fail "tar could not unpack the bundle. Nothing was installed."
    }

    # The archive holds one directory named for the version.
    $unpacked = Get-ChildItem -Path $work -Directory | Select-Object -First 1

    if (-not $unpacked -or -not (Test-Path (Join-Path $unpacked.FullName 'portable.cmd'))) {
        Fail "The bundle does not contain a launcher. Nothing was installed."
    }

    $parent = Split-Path -Parent $destination

    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    Move-Item -LiteralPath $unpacked.FullName -Destination $destination
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

# Run it once before saying it worked. A bundle that arrived intact and does not
# start is exactly what should not be reported as installed.
$reported = & (Join-Path $destination 'portable.cmd') version --json 2>$null | ConvertFrom-Json

if (-not $reported) {
    Fail "It was unpacked into $destination and does not run. Please report this."
}

Write-Host ""
Write-Host "portable $($reported.version) is in $destination" -ForegroundColor Green
Write-Host ""
Write-Host "Nothing else was changed: no PATH, no registry, no services. To use it:"
Write-Host ""
Write-Host "    cd $destination"
Write-Host "    .\portable.cmd up"
Write-Host "    .\portable.cmd install php"
Write-Host "    .\portable.cmd install caddy"
Write-Host "    .\portable.cmd site add demo C:\projects\demo"
Write-Host ""
Write-Host "    .\portable.cmd help        every command, with examples"
Write-Host ""

# Opt-in, and named as the one thing here that leaves a trace. The tool's own
# promise is that deleting its directory removes it completely, and an entry in
# PATH would be an exception to that - so it is asked for rather than assumed.
if ($env:PORTABLE_ADD_TO_PATH -eq '1') {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')

    if ($userPath -notlike "*$destination*") {
        [Environment]::SetEnvironmentVariable('Path', "$userPath;$destination", 'User')
        Write-Host "Added to your PATH. Open a new window, then just `portable`." -ForegroundColor Green
        Write-Host "(This is the one thing the installer wrote outside $destination.)"
        Write-Host ""
    }
}
