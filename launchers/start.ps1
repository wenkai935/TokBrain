$ErrorActionPreference = "Stop"

$WorkspacePath = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$FrontendPath = Join-Path $WorkspacePath "frontend"
$DataPath = Join-Path $WorkspacePath "data"
$LogPath = Join-Path $DataPath "logs"
$StatePath = Join-Path $DataPath "runtime.json"
$BackendPython = Join-Path $WorkspacePath ".venv\Scripts\python.exe"
$FrontendPort = 3001
$FrontendHost = "127.0.0.1"
$LocalUrl = "http://${FrontendHost}:$FrontendPort"
Set-Location $WorkspacePath

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:NODE_ENV = "production"

# Some launchers expose the same variable twice with different casing (PATH and
# Path, http_proxy and HTTP_PROXY). Windows PowerShell's Start-Process builds a
# case-insensitive environment dictionary and otherwise aborts before either
# local service is started, so collapse each duplicate to a single entry.
$EnvironmentNames = @([Environment]::GetEnvironmentVariables("Process").Keys)
$SettledNames = [System.Collections.Generic.HashSet[string]]::new()
foreach ($VariableName in $EnvironmentNames) {
    $Name = [string]$VariableName
    if (-not $SettledNames.Add($Name.ToLowerInvariant())) {
        continue
    }
    foreach ($Candidate in $EnvironmentNames) {
        $CandidateName = [string]$Candidate
        if ($CandidateName -cne $Name -and
            [string]::Equals(
                $CandidateName, $Name, [System.StringComparison]::OrdinalIgnoreCase
            )) {
            [Environment]::SetEnvironmentVariable($CandidateName, $null, "Process")
        }
    }
}

# Locate FFmpeg without hard-coding one machine's install path. Order:
#   $env:FFMPEG_BIN / $env:TOKBRAIN_FFMPEG_BIN -> ffmpeg already on PATH ->
#   project tools\ffmpeg\bin -> unpacked ffmpeg*\bin beside the workspace and
#   on the usual C:/D: roots.
$FFmpegBin = $null
foreach ($Candidate in @($env:FFMPEG_BIN, $env:TOKBRAIN_FFMPEG_BIN)) {
    if (-not $Candidate) {
        continue
    }
    if (Test-Path -LiteralPath (Join-Path $Candidate "ffmpeg.exe")) {
        $FFmpegBin = $Candidate
        break
    }
    if (Test-Path -LiteralPath (Join-Path $Candidate "bin\ffmpeg.exe")) {
        $FFmpegBin = Join-Path $Candidate "bin"
        break
    }
}
if (-not $FFmpegBin) {
    $ExistingFFmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    if ($ExistingFFmpeg) {
        $FFmpegBin = Split-Path -Parent $ExistingFFmpeg.Source
    }
}
if (-not $FFmpegBin) {
    $FFmpegSearchPaths = @(
        (Join-Path $WorkspacePath "tools\ffmpeg\bin"),
        (Join-Path $WorkspacePath "ffmpeg\bin"),
        (Join-Path (Split-Path -Parent $WorkspacePath) "ffmpeg*\bin"),
        "C:\ffmpeg\bin",
        "C:\ffmpeg*\bin",
        "D:\ffmpeg*\bin",
        "D:\code\ffmpeg*\bin",
        "D:\tools\ffmpeg*\bin",
        "D:\tokbrain-release\ffmpeg\bin"
    )
    foreach ($SearchPath in $FFmpegSearchPaths) {
        # Get-ChildItem -Filter does not expand wildcards in the -Path tail,
        # so resolve the directory first and then probe for the executable.
        foreach ($Directory in @(Get-Item -Path $SearchPath -ErrorAction SilentlyContinue |
                Sort-Object -Property FullName -Descending)) {
            if (Test-Path -LiteralPath (Join-Path $Directory.FullName "ffmpeg.exe")) {
                $FFmpegBin = $Directory.FullName
                break
            }
        }
        if ($FFmpegBin) {
            break
        }
    }
}
if ($FFmpegBin) {
    $env:PATH = "$FFmpegBin;$env:PATH"
    if (-not $env:FFMPEG_BIN) {
        $env:FFMPEG_BIN = $FFmpegBin
    }
    Write-Host "FFmpeg: $FFmpegBin" -ForegroundColor DarkGray
}
else {
    Write-Warning (
        "未找到 ffmpeg/ffprobe，视频将无法进入完整处理流水线。" +
        "请安装 ffmpeg 并加入 PATH，或设置环境变量 FFMPEG_BIN 指向其 bin 目录。"
    )
}

function Get-TextSha256 {
    param([AllowEmptyString()][string]$Value)

    $Algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$Value)
        return -join ($Algorithm.ComputeHash($Bytes) | ForEach-Object {
            $_.ToString("x2")
        })
    }
    finally {
        $Algorithm.Dispose()
    }
}

function Get-ProcessIdentity {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $Process) {
        return $null
    }
    try {
        $ExecutablePath = [string]$Process.Path
        $CreationTime = $Process.StartTime.ToUniversalTime()
    }
    catch {
        return $null
    }
    if (-not $ExecutablePath) {
        return $null
    }
    $CommandLineSha256 = $null
    try {
        $CimProcess = Get-CimInstance -ClassName Win32_Process -Filter (
            "ProcessId = $ProcessId"
        ) -ErrorAction SilentlyContinue
        if ($CimProcess -and $CimProcess.CommandLine) {
            $CommandLineSha256 = Get-TextSha256 ([string]$CimProcess.CommandLine)
        }
    }
    catch {
        # Standard-user or managed environments can deny WMI command-line
        # access. Creation time + executable path still prevent PID reuse.
    }
    return [ordered]@{
        pid = [int]$Process.Id
        creation_time_utc = $CreationTime.ToString("o")
        executable_path = [System.IO.Path]::GetFullPath($ExecutablePath)
        command_line_sha256 = $CommandLineSha256
    }
}

function Start-ProcessWatchdog {
    <#
    .SYNOPSIS
        Launch a detached hidden PowerShell process that waits for the parent
        to exit and then forcibly terminates the supplied child process IDs.
    .DESCRIPTION
        This covers the "parent killed without running finally" gap that Job
        Objects normally fill. If a Job Object cannot be created (because the
        parent is already in a job, as is common for cmd/powershell on Windows),
        the watchdog becomes the primary cleanup mechanism. It exits on its own
        once all children are already gone.
    #>
    param(
        [Parameter(Mandatory = $true)][int]$ParentProcessId,
        [Parameter(Mandatory = $true)][int[]]$ChildProcessIds,
        [Parameter(Mandatory = $true)][string]$LogDirectory,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string]$BridgeScriptPath
    )

    $ValidChildren = $ChildProcessIds |
        Where-Object { $_ -gt 0 } |
        Select-Object -Unique
    if (-not $ValidChildren) {
        return $null
    }

    $ChildList = ($ValidChildren -join ",")
    $TempPath = $ScriptPath
    $ScriptContent = @'
param(
    [Parameter(Mandatory = $true)][int]$ParentPid,
    [Parameter(Mandatory = $true)][string]$ChildrenCsv,
    [Parameter(Mandatory = $true)][string]$LogDirectory,
    [Parameter(Mandatory = $true)][string]$BridgeScriptPath
)

$Children = $ChildrenCsv -split "," | ForEach-Object {
    $Value = 0
    [int]::TryParse($_, [ref] $Value) | Out-Null
    $Value
} | Where-Object { $_ -gt 0 }

$LogPath = Join-Path $LogDirectory "watchdog.log"
function Write-WatchdogLog {
    param([string]$Message)
    Add-Content -Path $LogPath -Value "$(Get-Date -Format o) $Message" -Encoding UTF8 -ErrorAction SilentlyContinue
}

Write-WatchdogLog "watchdog started for parent $ParentPid, children $($Children -join ',')"
while ($true) {
    $Parent = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
    if (-not $Parent) {
        Write-WatchdogLog "parent $ParentPid gone; terminating children"
        foreach ($ChildPid in $Children) {
            if ($ChildPid -gt 0) {
                try { taskkill /F /T /PID $ChildPid 2>$null } catch {}
            }
        }
        try {
            Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" `
                -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.CommandLine -and
                    $_.CommandLine.IndexOf($BridgeScriptPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
                } |
                ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null }
        }
        catch {}
        exit
    }
    $Alive = $Children | Where-Object { (Get-Process -Id $_ -ErrorAction SilentlyContinue) }
    if (-not $Alive) {
        Write-WatchdogLog "all children gone; watchdog exiting"
        exit
    }
    Start-Sleep -Milliseconds 500
}
'@
    Set-Content -Path $TempPath -Value $ScriptContent -Encoding UTF8 -Force

    $ArgumentList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", $TempPath,
        "-ParentPid", [string]$ParentProcessId,
        "-ChildrenCsv", $ChildList,
        "-LogDirectory", $LogDirectory,
        "-BridgeScriptPath", $BridgeScriptPath
    )
    return Start-Process -FilePath "powershell.exe" `
        -ArgumentList $ArgumentList `
        -PassThru -WindowStyle Hidden
}

function Test-ProcessIdentity {
    param([Parameter(Mandatory = $true)]$Identity)

    if (-not $Identity.pid -or -not $Identity.creation_time_utc -or
        -not $Identity.executable_path) {
        return $false
    }
    $Current = Get-ProcessIdentity -ProcessId ([int]$Identity.pid)
    if (-not $Current) {
        return $false
    }
    $ExpectedCreation = [datetime]::Parse(
        [string]$Identity.creation_time_utc,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::RoundtripKind
    ).ToUniversalTime()
    $CurrentCreation = [datetime]::Parse(
        [string]$Current.creation_time_utc,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::RoundtripKind
    ).ToUniversalTime()
    return (
        [math]::Abs(($CurrentCreation - $ExpectedCreation).TotalSeconds) -lt 1 -and
        [string]::Equals(
            [string]$Current.executable_path,
            [string]$Identity.executable_path,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and (
            -not $Identity.command_line_sha256 -or
            -not $Current.command_line_sha256 -or
            [string]::Equals(
                [string]$Current.command_line_sha256,
                [string]$Identity.command_line_sha256,
                [System.StringComparison]::Ordinal
            )
        )
    )
}

function Stop-OwnedProcessTree {
    param($Identity)

    if (-not $Identity -or -not (Test-ProcessIdentity -Identity $Identity)) {
        return
    }
    try {
        & taskkill.exe /PID ([int]$Identity.pid) /T /F 2>$null | Out-Null
    }
    catch {
        # The process can exit between identity validation and termination.
    }
}

function Start-FeishuBridge {
    # 设计初衷：开服务即在轮询飞书表（手机 → 飞书 AI → 多维表格 → 本机处理）。
    # 启停逻辑复用 tokbrain_control.bridge：CIM 精确匹配进程、注入表 ID 与
    # NO_PROXY（绕系统代理），脚本里不再写第二份。
    # 失败只报警告：飞书不是 TokBrain 的启动前提，缺 lark-cli 授权也不该挡住服务。
    if ($env:TOKBRAIN_AUTOSTART_BRIDGE -eq "0") {
        Write-Host "  飞书同步桥：已按 TOKBRAIN_AUTOSTART_BRIDGE=0 跳过" -ForegroundColor DarkGray
        return 0
    }
    if (-not (Test-Path -LiteralPath $BackendPython)) {
        return 0
    }
    try {
        $Raw = & $BackendPython -m tokbrain_control sync start --json 2>&1
        $Payload = ($Raw -join "`n") | ConvertFrom-Json
        $Data = $Payload.data
        if ($Data -and $Data.started) {
            Write-Host "  飞书同步桥：已启动（pid $($Data.pid)）" -ForegroundColor Green
            return [int]$Data.pid
        }
        elseif ($Data -and $Data.already_running) {
            Write-Host "  飞书同步桥：已在运行，未重复启动" -ForegroundColor DarkGray
            $Existing = @($Data.processes | Where-Object { $_.pid } | Select-Object -First 1)
            return $(if ($Existing.Count) { [int]$Existing[0].pid } else { 0 })
        }
        else {
            $Reason = if ($Payload.error) { $Payload.error.message } else { "未知原因" }
            Write-Host "  飞书同步桥：未启动（$Reason）" -ForegroundColor Yellow
        }
    }
    catch {
        Write-Warning "飞书同步桥未能启动：$($_.Exception.Message)。服务本身不受影响。"
    }
    return 0
}

function Stop-FeishuBridge {
    if ($env:TOKBRAIN_AUTOSTART_BRIDGE -eq "0" -or
        -not (Test-Path -LiteralPath $BackendPython)) {
        return
    }
    try {
        & $BackendPython -m tokbrain_control sync stop --json 2>&1 | Out-Null
    }
    catch {
        Write-Warning "停止飞书同步桥失败：$($_.Exception.Message)"
    }
}

function New-ProcessTreeJob {
    # Returns a job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, or Zero when
    # this process already belongs to a job that forbids nesting.
    if (-not ('TokBrainJobObject' -as [type])) {
        $JobDefinition = @'
using System;
using System.Runtime.InteropServices;
public static class TokBrainJobObject {
    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, int length);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    public static IntPtr Create() {
        IntPtr job = CreateJobObjectW(IntPtr.Zero, null);
        if (job == IntPtr.Zero) { return IntPtr.Zero; }
        JOBOBJECT_BASIC_LIMIT_INFORMATION limits = new JOBOBJECT_BASIC_LIMIT_INFORMATION();
        limits.LimitFlags = 0x00002000;
        int size = Marshal.SizeOf(typeof(JOBOBJECT_BASIC_LIMIT_INFORMATION));
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try {
            Marshal.StructureToPtr(limits, buffer, false);
            if (!SetInformationJobObject(job, 2, buffer, size)) { return IntPtr.Zero; }
        }
        finally { Marshal.FreeHGlobal(buffer); }
        return job;
    }
    public static bool Assign(IntPtr job, int processId) {
        try {
            return AssignProcessToJobObject(job, System.Diagnostics.Process.GetProcessById(processId).Handle);
        }
        catch { return false; }
    }
}
'@
        try {
            Add-Type -TypeDefinition $JobDefinition -ErrorAction Stop
        }
        catch {
            return [IntPtr]::Zero
        }
    }
    return [TokBrainJobObject]::Create()
}

function Get-ListeningProcessId {
    param([Parameter(Mandatory = $true)][int]$Port)

    $Pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
    foreach ($Line in (& netstat.exe -ano -p tcp)) {
        if ($Line -match $Pattern) {
            return [int]$Matches[1]
        }
    }
    return $null
}

function Repair-StaleRuntimeState {
    param(
        [Parameter(Mandatory = $true)][int]$CurrentBackendPort,
        [Parameter(Mandatory = $true)][int]$CurrentFrontendPort
    )

    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
        return
    }

    $PortsToCheck = [System.Collections.Generic.HashSet[int]]::new()
    [void]$PortsToCheck.Add($CurrentBackendPort)
    [void]$PortsToCheck.Add($CurrentFrontendPort)
    try {
        $RecordedState = Get-Content -Raw -Encoding UTF8 -LiteralPath $StatePath |
            ConvertFrom-Json
        foreach ($PropertyName in @("backend_port", "frontend_port")) {
            $Property = $RecordedState.PSObject.Properties[$PropertyName]
            if ($Property -and $null -ne $Property.Value) {
                $RecordedPort = [int]$Property.Value
                if ($RecordedPort -ge 1 -and $RecordedPort -le 65535) {
                    [void]$PortsToCheck.Add($RecordedPort)
                }
            }
        }
        if (-not $RecordedState.PSObject.Properties["state_version"]) {
            [void]$PortsToCheck.Add(8001)
            [void]$PortsToCheck.Add(3001)
        }
    }
    catch {
        Write-Warning "data\runtime.json 无法解析；仅在确认当前服务端口空闲后清理。"
    }

    foreach ($Port in $PortsToCheck) {
        if (Get-ListeningProcessId -Port $Port) {
            return
        }
    }

    Remove-Item -LiteralPath $StatePath -Force
    Write-Host "检测到已失效的运行状态；确认相关端口空闲后已自动清理。" `
        -ForegroundColor Yellow
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $Directory = Split-Path -Parent $Path
    $TemporaryPath = Join-Path $Directory (
        ".runtime-$([guid]::NewGuid().ToString('N')).tmp"
    )
    try {
        $Json = $Value | ConvertTo-Json -Depth 8
        $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($TemporaryPath, $Json, $Utf8WithoutBom)
        if (Test-Path -LiteralPath $Path) {
            [System.IO.File]::Replace($TemporaryPath, $Path, $null, $true)
        }
        else {
            [System.IO.File]::Move($TemporaryPath, $Path)
        }
    }
    finally {
        Remove-Item -LiteralPath $TemporaryPath -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -LiteralPath $BackendPython -PathType Leaf)) {
    throw "尚未安装 Python 环境，请先运行 .\launchers\setup.ps1。"
}

$RuntimeProbeScript = @'
import importlib.metadata as metadata
import json
from pathlib import Path

from app.config import settings
from app.main import API_CONTRACT_VERSION, APP_VERSION
from app.services.f2_links import F2_VENDOR_DIR, ensure_f2_runtime

ensure_f2_runtime()
import f2

vendor = F2_VENDOR_DIR.resolve()
origin = Path(f2.__file__).resolve()
if vendor not in origin.parents:
    raise SystemExit(f"F2 loaded outside isolated vendor directory: {origin}")
versions = {
    distribution.version
    for distribution in metadata.distributions(path=[str(vendor)])
    if (distribution.metadata.get("Name") or "").lower() == "f2"
}
if versions != {"0.0.1.7"}:
    raise SystemExit(f"Unexpected isolated F2 version: {sorted(versions)}")
print(json.dumps({
    "app_version": APP_VERSION,
    "api_contract": API_CONTRACT_VERSION,
    "backend_host": settings.app_host,
    "backend_port": settings.app_port,
    "f2_origin": str(origin),
}))
'@
$PreviousErrorActionPreference = $ErrorActionPreference
try {
    # Windows PowerShell 5.1 can strip quotes from a multiline native `-c`
    # argument. Passing the probe over stdin preserves the Python source.
    $ErrorActionPreference = "Continue"
    $RuntimeProbeText = @(
        $RuntimeProbeScript | & $BackendPython - 2>&1
    )
    $RuntimeProbeExitCode = $LASTEXITCODE
}
catch {
    $RuntimeProbeText = @()
    $RuntimeProbeExitCode = 1
}
finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}
$RuntimeProbeLine = [string]($RuntimeProbeText | Select-Object -Last 1)
if ($RuntimeProbeExitCode -ne 0 -or -not $RuntimeProbeLine) {
    $RuntimeProbeDiagnostic = [string](
        $RuntimeProbeText | Where-Object { [string]$_ } | Select-Object -Last 1
    )
    if (-not $RuntimeProbeDiagnostic) {
        $RuntimeProbeDiagnostic = "探针没有返回详细信息"
    }
    throw (
        "Python 依赖、F2 隔离环境或应用配置校验失败：" +
        "$RuntimeProbeDiagnostic。请重新运行 .\launchers\setup.ps1。"
    )
}
try {
    $RuntimeProbe = $RuntimeProbeLine | ConvertFrom-Json
    [int]$ExpectedApiContract = $RuntimeProbe.api_contract
    [int]$BackendPort = $RuntimeProbe.backend_port
    [string]$BackendHost = $RuntimeProbe.backend_host
    [string]$AppVersion = $RuntimeProbe.app_version
}
catch {
    throw "无法读取后端运行配置，请检查 Python 环境与 app.main。"
}
if ($BackendPort -lt 1 -or $BackendPort -gt 65535 -or $BackendPort -eq $FrontendPort) {
    throw "APP_PORT 必须为 1-65535 且不能与前端端口 $FrontendPort 相同。"
}
$BackendUrl = "http://${BackendHost}:$BackendPort"
$env:NEXT_PUBLIC_API_URL = $BackendUrl

if (-not (Test-Path -LiteralPath (Join-Path $FrontendPath "node_modules"))) {
    throw "前端依赖不存在，请先运行 .\launchers\setup.ps1。"
}
$FrontendBuildIdPath = Join-Path $FrontendPath ".next\BUILD_ID"
$FrontendBuildStatePath = Join-Path $FrontendPath ".next\tokbrain-build.json"
if (-not (Test-Path -LiteralPath $FrontendBuildIdPath -PathType Leaf)) {
    throw "前端生产构建不存在，请重新运行 .\launchers\setup.ps1。"
}
if (Test-Path -LiteralPath $FrontendBuildStatePath -PathType Leaf) {
    try {
        $FrontendBuildState = Get-Content -Raw -Encoding UTF8 `
            -LiteralPath $FrontendBuildStatePath |
            ConvertFrom-Json
    }
    catch {
        throw "前端构建信息损坏，请重新运行 .\launchers\setup.ps1。"
    }
    if ([string]$FrontendBuildState.backend_url -ne $BackendUrl) {
        throw "APP_HOST/APP_PORT 已在安装后改变；请重新运行 .\launchers\setup.ps1 以更新前端构建。"
    }
}
elseif ($BackendUrl -ne "http://127.0.0.1:8001") {
    throw "自定义 APP_HOST/APP_PORT 缺少对应的前端构建信息；请重新运行 .\launchers\setup.ps1。"
}

New-Item -ItemType Directory -Force -Path $DataPath | Out-Null
New-Item -ItemType Directory -Force -Path $LogPath | Out-Null
Get-ChildItem -Path $LogPath -Filter "watchdog-*.ps1" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

Repair-StaleRuntimeState -CurrentBackendPort $BackendPort `
    -CurrentFrontendPort $FrontendPort

foreach ($RequiredPort in @($BackendPort, $FrontendPort)) {
    $ExistingProcessId = Get-ListeningProcessId -Port $RequiredPort
    if ($ExistingProcessId) {
        throw "端口 $RequiredPort 已被进程 $ExistingProcessId 占用。停止脚本只会终止身份匹配的 TokBrain 实例，不会关闭其他软件。"
    }
}

$Backend = $null
$Frontend = $null
$BackendLauncherIdentity = $null
$FrontendLauncherIdentity = $null
$BackendListenerIdentity = $null
$FrontendListenerIdentity = $null
$Watchdog = $null
$WatchdogScriptPath = $null
$BridgeProcessId = 0
$BridgeProbeCountdown = 0

try {
    $Backend = Start-Process -FilePath $BackendPython -ArgumentList (
        "-m", "uvicorn", "app.main:app", "--host", $BackendHost,
        "--port", [string]$BackendPort
    ) -WorkingDirectory $WorkspacePath -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogPath "backend.log") `
        -RedirectStandardError (Join-Path $LogPath "backend-error.log")
    $BackendLauncherIdentity = Get-ProcessIdentity -ProcessId $Backend.Id
    if (-not $BackendLauncherIdentity) {
        throw "后端启动后无法记录进程身份。"
    }

    $NpmCommand = (Get-Command npm.cmd -ErrorAction Stop).Source
    $Frontend = Start-Process -FilePath $NpmCommand -ArgumentList (
        "run", "start"
    ) -WorkingDirectory $FrontendPath -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogPath "frontend.log") `
        -RedirectStandardError (Join-Path $LogPath "frontend-error.log")
    $FrontendLauncherIdentity = Get-ProcessIdentity -ProcessId $Frontend.Id
    if (-not $FrontendLauncherIdentity) {
        throw "前端启动后无法记录进程身份。"
    }

    # Job membership is inherited by every descendant, so one assignment per
    # launcher covers the whole tree (npm -> node, uvicorn -> workers).
    $ServiceJob = New-ProcessTreeJob
    $JobObjectFailed = $true
    if ($ServiceJob -ne [IntPtr]::Zero) {
        $BackendAssigned = [TokBrainJobObject]::Assign($ServiceJob, $Backend.Id)
        $FrontendAssigned = [TokBrainJobObject]::Assign($ServiceJob, $Frontend.Id)
        if ($BackendAssigned -and $FrontendAssigned) {
            $JobObjectFailed = $false
        }
        else {
            Write-Warning "无法将服务进程加入作业对象（本进程可能已受其他作业约束）。"
        }
    }
    else {
        Write-Warning "无法创建作业对象（本进程可能已受其他作业约束）。"
    }

    # Fallback / additional guard: a detached hidden watchdog that kills the
    # child tree if this parent dies without running its finally block. This is
    # needed because Windows often places cmd/powershell in an existing job,
    # making AssignProcessToJobObject fail even outside the sandbox.
    $WatchdogScriptPath = Join-Path $LogPath "watchdog-$([guid]::NewGuid()).ps1"
    $Watchdog = Start-ProcessWatchdog `
        -ParentProcessId $PID `
        -ChildProcessIds @($Backend.Id, $Frontend.Id) `
        -LogDirectory $LogPath `
        -ScriptPath $WatchdogScriptPath `
        -BridgeScriptPath (Join-Path $WorkspacePath "bridge_feishu_obsidian.py")
    if (-not $Watchdog) {
        throw "无法创建进程看门狗，也不能依赖作业对象；启动中止。"
    }
    if ($JobObjectFailed) {
        Write-Host "已启动独立看门狗（PID $($Watchdog.Id)）作为兜底；父进程异常退出时将自动清理服务。" -ForegroundColor Yellow
    }

    $Ready = $false
    $LastBackendStatus = "未响应"
    $LastBackendContract = "未知"
    $LastCoordinatorStatus = "未响应"
    $LastFrontendStatus = "未响应"
    $LastProbeError = ""
    for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
        try {
            $BackendResponse = Invoke-RestMethod -TimeoutSec 1 "$BackendUrl/health"
            $LastBackendStatus = [string]$BackendResponse.status
            $LastBackendContract = [string]$BackendResponse.api_contract
            $CoordinatorResponse = Invoke-RestMethod -TimeoutSec 1 `
                "$BackendUrl/api/system-health/probes/coordinators"
            $LastCoordinatorStatus = [string]$CoordinatorResponse.status
            $FrontendResponse = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 $LocalUrl
            $LastFrontendStatus = [string]$FrontendResponse.StatusCode
            if ($BackendResponse.status -eq "healthy" -and
                $BackendResponse.api_contract -eq $ExpectedApiContract -and
                $CoordinatorResponse.status -eq "healthy" -and
                $FrontendResponse.StatusCode -eq 200) {
                $Ready = $true
                break
            }
        }
        catch {
            $LastProbeError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $Ready) {
        $FailureSummary = (
            "后端状态=$LastBackendStatus，API contract=$LastBackendContract" +
            "（期望 $ExpectedApiContract），后台协调器=$LastCoordinatorStatus，" +
            "前端 HTTP=$LastFrontendStatus"
        )
        if ($LastProbeError) {
            $FailureSummary += "，最近探测错误=$LastProbeError"
        }
        throw "服务未能在 20 秒内就绪；$FailureSummary。请查看 data\logs\backend-error.log 与 data\logs\frontend-error.log。"
    }

    $BackendListenerProcessId = Get-ListeningProcessId -Port $BackendPort
    $FrontendListenerProcessId = Get-ListeningProcessId -Port $FrontendPort
    if (-not $BackendListenerProcessId -or -not $FrontendListenerProcessId) {
        throw "服务已响应，但无法记录监听进程身份。"
    }
    $BackendListenerIdentity = Get-ProcessIdentity -ProcessId $BackendListenerProcessId
    $FrontendListenerIdentity = Get-ProcessIdentity -ProcessId $FrontendListenerProcessId
    if (-not $BackendListenerIdentity -or -not $FrontendListenerIdentity) {
        throw "服务已响应，但监听进程身份不完整。"
    }

    $InstanceId = [guid]::NewGuid().ToString("D")
    $RuntimeState = [ordered]@{
        state_version = 2
        instance_id = $InstanceId
        workspace_path = [System.IO.Path]::GetFullPath($WorkspacePath)
        created_at_utc = [datetime]::UtcNow.ToString("o")
        app_version = $AppVersion
        api_contract = $ExpectedApiContract
        coordinator_status = $LastCoordinatorStatus
        backend_host = $BackendHost
        backend_port = $BackendPort
        frontend_host = $FrontendHost
        frontend_port = $FrontendPort
        backend_listener = $BackendListenerIdentity
        frontend_listener = $FrontendListenerIdentity
        backend_launcher = $BackendLauncherIdentity
        frontend_launcher = $FrontendLauncherIdentity
    }
    Write-AtomicJson -Path $StatePath -Value $RuntimeState

    Write-Host "TokBrain $AppVersion 已启动：$LocalUrl。关闭服务请双击 launchers 目录下的‘停止.cmd’。" -ForegroundColor Green
    $BridgeProcessId = Start-FeishuBridge
    if ($env:TOKBRAIN_SKIP_LOCAL_UI_OPEN -ne "1") {
        try {
            Start-Process -FilePath $LocalUrl
        }
        catch {
            Write-Warning "本地网页未能自动打开，请手动访问 $LocalUrl。"
        }
    }

    # 后端/前端由 Job Object + 独立 watchdog 监管；bridge 是 detached 进程，
    # 这里把它纳入正式启动器的持续监管，异常退出后自动重新拉起。
    while (-not $Backend.HasExited -and -not $Frontend.HasExited -and
           (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
        Start-Sleep -Seconds 1
        $BridgeProbeCountdown--
        if ($env:TOKBRAIN_AUTOSTART_BRIDGE -ne "0" -and
            $BridgeProbeCountdown -le 0) {
            $BridgeProbeCountdown = 5
            $BridgeAlive = $BridgeProcessId -gt 0 -and
                (Get-Process -Id $BridgeProcessId -ErrorAction SilentlyContinue)
            if (-not $BridgeAlive) {
                Write-Warning "飞书同步桥已退出，正在自动重启。"
                $BridgeProcessId = Start-FeishuBridge
            }
        }
    }
}
finally {
    Stop-FeishuBridge
    Stop-OwnedProcessTree -Identity $FrontendListenerIdentity
    Stop-OwnedProcessTree -Identity $FrontendLauncherIdentity
    Stop-OwnedProcessTree -Identity $BackendListenerIdentity
    Stop-OwnedProcessTree -Identity $BackendLauncherIdentity
    if ($Watchdog) {
        Stop-Process -Id $Watchdog.Id -Force -ErrorAction SilentlyContinue
    }
    if ($WatchdogScriptPath -and (Test-Path -LiteralPath $WatchdogScriptPath)) {
        Remove-Item -Path $WatchdogScriptPath -Force -ErrorAction SilentlyContinue
    }
}
