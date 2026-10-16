# Lodgings capability probe for Windows. PowerShell 5.1+, no modules, read-only.
# `lodgings probe` streams this file to `powershell -NoProfile -NonInteractive -Command -` over ssh, so nothing is
# installed or written on the host. Before it runs, lodgings prepends one line: $LodgingsCfg = '<json>' | ConvertFrom-Json
# It prints one line: LODGINGS-FACTS {json}. It records no user name, host name or file path.
# Commands it may run: the version flag of each configured tool. Everything else is read from WMI, the registry and
# well-known folders (counts only for certificates).
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$errors = New-Object System.Collections.ArrayList
$t0 = Get-Date

function Get-ToolVersion($names, $toolArgs, $pattern, $timeoutMs) {
    $entry = [ordered]@{ present = $false; version = $null; note = $null }
    foreach ($n in $names) {
        $cmd = Get-Command $n -CommandType Application | Select-Object -First 1
        if (-not $cmd) { continue }
        $entry.present = $true
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $cmd.Source
        $psi.Arguments = ($toolArgs -join ' ')
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $p = New-Object System.Diagnostics.Process
        $p.StartInfo = $psi
        try { [void]$p.Start() } catch { $entry.note = 'could not start'; continue }
        $outTask = $p.StandardOutput.ReadToEndAsync()
        $errTask = $p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit($timeoutMs)) {
            try { $p.Kill() } catch { }
            $entry.note = 'did not answer in time'
            continue
        }
        $text = $outTask.Result + "`n" + $errTask.Result
        $m = [regex]::Match($text, $pattern, 'Multiline')
        if ($m.Success) { $entry.version = $m.Groups[1].Value; $entry.note = $null; break }
        $entry.note = 'no version in the output'
    }
    return $entry
}

function Get-FileVersion($path) {
    if (Test-Path -LiteralPath $path) { return (Get-Item -LiteralPath $path).VersionInfo.ProductVersion }
    return $null
}

$f = [ordered]@{ schema = 'lodgings.facts'; schema_version = '1.0'; collector = 'probe_windows 0.1.0'
                 collected_epoch = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); errors = @() }

# --- operating system
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    $arch = @{ 'AMD64' = 'x86_64'; 'ARM64' = 'arm64'; 'x86' = 'x86' }[$arch]
    $parts = $os.Version.Split('.')
    $ubr = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').UBR
    $f.os = [ordered]@{ family = 'windows'; name = $os.Caption.Trim(); version = ($parts[0] + '.' + $parts[1]); build = $parts[2]
                        kernel = $os.Version; arch = $arch; wsl = $null; view = 'windows'; revision = $ubr }
} catch { [void]$errors.Add('os: ' + $_.Exception.Message) }

# --- hardware
try {
    $cpu = @(Get-CimInstance Win32_Processor)
    $cs = Get-CimInstance Win32_ComputerSystem
    $logical = 0
    foreach ($c in $cpu) { $logical += $c.NumberOfLogicalProcessors }
    $f.cpu = [ordered]@{ model = $cpu[0].Name.Trim(); logical = $logical; perf_logical = $null }
    $f.mem = [ordered]@{ total_gb = [math]::Round($cs.TotalPhysicalMemory / 1e9, 1); basis = 'machine' }
    $disks = @()
    foreach ($d in @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3')) {
        $disks += [ordered]@{ label = $d.DeviceID; total_gb = [math]::Round($d.Size / 1e9, 1); free_gb = [math]::Round($d.FreeSpace / 1e9, 1); basis = 'this volume' }
    }
    $f.disks = $disks
    $f.battery = [bool](@(Get-CimInstance Win32_Battery).Count -gt 0)
} catch { [void]$errors.Add('hardware: ' + $_.Exception.Message) }

# --- gpu
try {
    $devs = @()
    foreach ($g in @(Get-CimInstance Win32_VideoController)) {
        $v = 'other'
        if ($g.Name -match 'NVIDIA') { $v = 'nvidia' } elseif ($g.Name -match 'AMD|Radeon') { $v = 'amd' } elseif ($g.Name -match 'Intel') { $v = 'intel' }
        $devs += [ordered]@{ vendor = $v; name = $g.Name.Trim(); driver = $g.DriverVersion }
    }
    $f.gpu = [ordered]@{ devices = $devs; basis = 'Win32_VideoController'; passthrough = $null }
} catch { [void]$errors.Add('gpu: ' + $_.Exception.Message) }

# --- browsers
try {
    $pf = $env:ProgramFiles
    $pf86 = ${env:ProgramFiles(x86)}
    $chromeVer = $null
    $chromePresent = $false
    foreach ($p in @("$pf\Google\Chrome\Application\chrome.exe", "$pf86\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")) {
        if (Test-Path -LiteralPath $p) { $chromePresent = $true; $chromeVer = Get-FileVersion $p; break }
    }
    $edgeVer = Get-FileVersion "$pf86\Microsoft\Edge\Application\msedge.exe"
    $pw = [ordered]@{}
    $pwRoot = $env:PLAYWRIGHT_BROWSERS_PATH
    if (-not $pwRoot -or $pwRoot -eq '0') { $pwRoot = "$env:LOCALAPPDATA\ms-playwright" }
    foreach ($d in @(Get-ChildItem -LiteralPath $pwRoot -Directory)) {
        $m = [regex]::Match($d.Name, '^(chromium|firefox|webkit)-(\d+)$')
        if ($m.Success) {
            $k = $m.Groups[1].Value
            if (-not $pw.Contains($k)) { $pw[$k] = @() }
            $pw[$k] += [int]$m.Groups[2].Value
        }
    }
    $f.browsers = [ordered]@{ chrome = [ordered]@{ present = $chromePresent; version = $chromeVer }
                              edge = [ordered]@{ present = [bool]$edgeVer; version = $edgeVer }
                              safari = $null; safaridriver = $null; playwright = $pw; chrome_probe = $null }
} catch { [void]$errors.Add('browsers: ' + $_.Exception.Message) }

# --- windows-only tooling
try {
    $kits = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    $signtool = $null
    foreach ($d in @(Get-ChildItem -LiteralPath $kits -Directory | Sort-Object Name -Descending)) {
        $p = Join-Path $d.FullName 'x64\signtool.exe'
        if (Test-Path -LiteralPath $p) { $signtool = $d.Name; break }
    }
    $certs = @(Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.EnhancedKeyUsageList.ObjectId -contains '1.3.6.1.5.5.7.3.3' -and $_.NotAfter -gt (Get-Date) }).Count
    $session = [bool](Get-CimInstance Win32_ComputerSystem).UserName
    $f.windows = [ordered]@{ signtool = $signtool; code_signing_certs = $certs; desktop_session = $session; powershell = $PSVersionTable.PSVersion.ToString() }
} catch { [void]$errors.Add('windows: ' + $_.Exception.Message) }

# --- tools
try {
    $tools = [ordered]@{}
    foreach ($prop in $LodgingsCfg.tools.PSObject.Properties) {
        $s = $prop.Value
        $tools[$prop.Name] = Get-ToolVersion @($s.names) @($s.args) $s.pattern ([int]($LodgingsCfg.timeout_s * 1000))
    }
    $f.tools = $tools
} catch { [void]$errors.Add('tools: ' + $_.Exception.Message) }

$f.errors = @($errors)
$f.collect_ms = [int]((Get-Date) - $t0).TotalMilliseconds
$json = $f | ConvertTo-Json -Depth 8 -Compress
Write-Output ('LODGINGS-FACTS ' + $json)
