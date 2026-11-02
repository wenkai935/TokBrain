param(
    [ValidateSet(
        "menu", "status", "install", "start", "start-bg",
        "stop", "restart", "dev", "dev-stop", "mcp", "control", "bridge",
        "bridge-start", "bridge-stop", "open"
    )]
    [string]$Action = "menu",
    [switch]$WithMcp,
    [switch]$WithDev,
    [switch]$WithoutBilling,
    # 只报告会停哪些进程，不动手。给 dev-stop 用。
    [switch]$DryRun
)

# TokBrain 单入口控制台。
#   TokBrain.cmd            -> 交互菜单
#   TokBrain.cmd status     -> 只打印环境与服务状态
#   TokBrain.cmd install    -> 安装/重建（可加 -WithMcp / -WithDev / -WithoutBilling）
#   TokBrain.cmd start      -> 前台启动（Ctrl+C 或关闭窗口即停）
#   TokBrain.cmd start-bg   -> 隐藏窗口启动
#   TokBrain.cmd stop       -> 停止
#   TokBrain.cmd restart    -> 停止后隐藏窗口启动
#   TokBrain.cmd dev        -> 开发模式（后端 + 前端热更新两个窗口）
#   TokBrain.cmd dev-stop   -> 停止开发模式实例（加 -DryRun 只看不动手）
#   TokBrain.cmd mcp        -> 启动只读 MCP 服务（需先装 MCP 环境）
#   TokBrain.cmd control    -> 启动控制 MCP 服务 8011（可写，需先配 control.env）
#   TokBrain.cmd bridge     -> 飞书同步桥状态（不启动）
#   TokBrain.cmd bridge-start / bridge-stop -> 起停飞书同步桥
#   TokBrain.cmd open       -> 打开网页
# 启动服务（start / start-bg / restart）会自动拉起飞书同步桥，
# 设 TOKBRAIN_AUTOSTART_BRIDGE=0 可关闭这一默认行为。
# 其余脚本（setup.cmd / start.cmd / 停止.cmd / 重启.cmd / dev.cmd）保留为兼容入口。

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$BackendPort = 8001
$FrontendPort = 3001
$McpPort = 8010
$ControlPort = 8011
$LocalUrl = "http://127.0.0.1:$FrontendPort"
# 受管实例的身份记录。开发模式（dev.cmd）从不写这个文件，所以不能拿它判断
# "有没有在跑"，只能拿它判断"这个监听进程是不是 start.ps1 起的"。
$RuntimeStatePath = Join-Path $Root "data\runtime.json"

function Get-ListenerMap {
    param([int[]]$Ports)

    $Map = @{}
    $Pattern = "^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$"
    foreach ($Line in (& netstat.exe -ano -p tcp)) {
        if ($Line -match $Pattern) {
            $Port = [int]$Matches[1]
            if ($Ports -contains $Port -and -not $Map.ContainsKey($Port)) {
                $Map[$Port] = [int]$Matches[2]
            }
        }
    }
    return $Map
}

function Test-PathReady {
    param([string]$RelativePath)

    return (Test-Path -LiteralPath (Join-Path $Root $RelativePath))
}

function Test-PythonReady {
    param([string]$RelativePath)

    $Python = Join-Path $Root $RelativePath
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { return $false }
    $PreviousExitCode = $global:LASTEXITCODE
    try {
        & $Python -c "pass" 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
    finally {
        $global:LASTEXITCODE = $PreviousExitCode
    }
}

function Test-LarkCliReady {
    return (
        (Test-PathReady "cli\node_modules\@larksuite\cli\bin\lark-cli.exe") -or
        (Test-PathReady "cli\node_modules\.bin\lark-cli.cmd") -or
        [bool](Get-Command lark-cli -ErrorAction SilentlyContinue)
    )
}

function Get-RuntimeIdentity {
    # 只读 data\runtime.json，取出 start.ps1 记录的监听 PID。文件缺失或损坏
    # 一律当作"没有受管实例"，绝不因为读不懂就去猜。
    $Identity = @{ backend = 0; frontend = 0 }
    if (-not (Test-Path -LiteralPath $RuntimeStatePath -PathType Leaf)) {
        return $Identity
    }
    try {
        $State = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimeStatePath |
            ConvertFrom-Json
    }
    catch {
        return $Identity
    }
    foreach ($Role in @("backend", "frontend")) {
        $Record = $State.PSObject.Properties["${Role}_listener"]
        if (-not $Record -or -not $Record.Value) { continue }
        # 别写成 $Pid：$PID 是只读自动变量，遮蔽它容易踩坑。
        $PidField = $Record.Value.PSObject.Properties["pid"]
        if ($PidField -and $PidField.Value) {
            try { $Identity[$Role] = [int]$PidField.Value } catch { }
        }
    }
    return $Identity
}

function Get-ProcessRecords {
    # 一次查表，只取关心的几个 PID，返回 @{ <pid> = @{ name; cmdline } }。
    # 带上进程名是必须的：cmd /k ".venv\Scripts\python.exe -m uvicorn ..." 这种
    # 外壳的命令行里也含 uvicorn，只按文本匹配会一路溯到 cmd 窗口上去。
    param([int[]]$ProcessIds)

    $Map = @{}
    $Wanted = @($ProcessIds | Where-Object { $_ -gt 0 } | Select-Object -Unique)
    if (-not $Wanted.Count) { return $Map }
    try {
        $All = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    }
    catch {
        return $Map
    }
    foreach ($Entry in $All) {
        $Id = [int]$Entry.ProcessId
        if ($Wanted -contains $Id) {
            $Map[$Id] = @{
                name    = [string]$Entry.Name
                cmdline = [string]$Entry.CommandLine
            }
        }
    }
    return $Map
}

function Test-OwnBackendProcess {
    # dev.cmd 与 start.ps1 起后端的命令行是一样的，所以这条只能证明
    # "是本项目的 uvicorn"，是不是受管实例要靠 runtime.json 分流。
    param($Record)

    if (-not $Record) { return $false }
    if ($Record.name -notmatch '^python') { return $false }
    return ($Record.cmdline -match 'uvicorn' -and $Record.cmdline -match 'app[.:]main:app')
}

function Test-OwnFrontendProcess {
    # 必须同时是 node 且带上本项目 frontend 目录，否则 3001 上任何 next
    # 都会被误判成自己的。
    param($Record)

    if (-not $Record) { return $false }
    if ($Record.name -notmatch '^node') { return $false }
    if ($Record.cmdline -notmatch 'next') { return $false }
    return ($Record.cmdline -match [regex]::Escape((Join-Path $Root "frontend")))
}

function Get-ListenerOwner {
    # 把"谁占着这个端口"翻译成一个分类。
    # 受管 = runtime.json 记录在案（[4] 能停）；非受管 = 本项目的开发模式/手动实例（[11] 能停）。
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$Role,
        $Managed,
        $Records
    )

    $ManagedPid = if ($Role -eq "backend") { $Managed.backend } else { $Managed.frontend }
    if ($ManagedPid -and $ProcessId -eq [int]$ManagedPid) { return "managed" }

    $Record = if ($Records -and $Records.ContainsKey($ProcessId)) {
        $Records[$ProcessId]
    }
    else { $null }
    $Own = if ($Role -eq "backend") {
        Test-OwnBackendProcess -Record $Record
    }
    else {
        Test-OwnFrontendProcess -Record $Record
    }
    if ($Own) { return "unmanaged" }
    return "foreign"
}

function Format-Listener {
    param($Map, [int]$Port, [string]$Role = "", $Managed = $null, $Records = $null)

    if (-not $Map.ContainsKey($Port)) {
        return "未启动"
    }
    $ProcessId = $Map[$Port]
    if (-not $Role -or -not $Managed) {
        return "运行中 (PID $ProcessId)"
    }
    $Owner = Get-ListenerOwner -ProcessId $ProcessId -Role $Role `
        -Managed $Managed -Records $Records
    switch ($Owner) {
        "managed" { return "运行中 (PID $ProcessId，受管实例)" }
        "unmanaged" { return "运行中 (PID $ProcessId，非受管实例)" }
        default { return "运行中 (PID $ProcessId，其他程序)" }
    }
}

function Get-BridgeState {
    # 飞书同步桥不监听端口，只能按命令行认进程。判定口径与
    # tokbrain_control.bridge.processes() 一致：python 且命令行含 bridge_feishu_obsidian.py。
    try {
        $Hits = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" `
                -ErrorAction Stop |
            Where-Object {
                $_.CommandLine -and $_.CommandLine -match 'bridge_feishu_obsidian\.py'
            })
    }
    catch {
        return "未知（查询进程失败）"
    }
    if ($Hits.Count -gt 0) {
        return "运行中 (PID $($Hits[0].ProcessId))"
    }
    return "未启动"
}

function Get-StatusLines {
    $Map = Get-ListenerMap -Ports @($BackendPort, $FrontendPort, $McpPort, $ControlPort)
    # 端口只说明"有人在听"，得再查一次身份才能分清是 [4] 能停的受管实例，
    # 还是 [11] 才能停的开发模式实例。
    $Managed = Get-RuntimeIdentity
    $Records = Get-ProcessRecords -ProcessIds @($Map.Values)
    $BackendEnv = if (Test-PythonReady ".venv\Scripts\python.exe") { "OK" }
        elseif (Test-PathReady ".venv\Scripts\python.exe") { "损坏（请重装）" }
        else { "缺失" }
    $FrontendDeps = if (Test-PathReady "frontend\node_modules") { "OK" } else { "缺失" }
    $FrontendBuild = if (Test-PathReady "frontend\.next\BUILD_ID") { "OK" } else { "缺失" }
    $McpEnv = if (Test-PythonReady ".venv-mcp\Scripts\python.exe") { "OK" }
        elseif (Test-PathReady ".venv-mcp\Scripts\python.exe") { "损坏（请重装）" }
        else { "未安装(可选)" }
    # 控制面要两样东西：MCP 环境 + control.env（含令牌）。只提示，不代填。
    $ControlEnv = if (Test-PathReady "mcp_server\control.env") { "OK" } else { "未配置(可选)" }
    $Ffmpeg = $null
    if (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) {
        $Ffmpeg = "PATH"
    }
    else {
        foreach ($Candidate in @(
                (Join-Path $Root "tools\ffmpeg\bin"),
                "C:\ffmpeg\bin",
                "D:\ffmpeg*\bin",
                "D:\code\ffmpeg*\bin"
            )) {
            $Hit = Get-Item -Path $Candidate -ErrorAction SilentlyContinue |
                Where-Object {
                    Test-Path -LiteralPath (Join-Path $_.FullName "ffmpeg.exe")
                } |
                Select-Object -First 1
            if ($Hit) {
                $Ffmpeg = $Hit.FullName
                break
            }
        }
    }
    if (-not $Ffmpeg) { $Ffmpeg = "未找到" }

    # 下载板块（bridge_douyin.py）、抖音浏览器解析（browser_resolver.py）、
    # 飞书同步桥（feishu_auth.py 驱动 lark-cli）的可选依赖：核心服务能起，
    # 缺这些只是对应功能不可用，所以统一标“可选”。
    $Playwright = if (Test-PathReady ".venv\Lib\site-packages\playwright") {
        "OK"
    }
    elseif (-not (Test-PythonReady ".venv\Scripts\python.exe")) {
        "需先装 .venv"
    }
    else { "缺失(可选)" }

    # playwright-cli 有两种来源：npm 局部装在 frontend\node_modules\.bin（局部安装
    # 不会进 PATH），或全局装在 PATH。clone 用户走的是前者，所以必须先认项目内安装，
    # 否则装完了状态面板还显示「缺失(可选)」。
    $PlaywrightCli = if (Test-PathReady "frontend\node_modules\.bin\playwright-cli.cmd") {
        "项目内"
    }
    elseif (Get-Command playwright-cli -ErrorAction SilentlyContinue) {
        "PATH"
    }
    else { "缺失(可选)" }

    $Chrome = $null
    if (Get-Command chrome.exe -ErrorAction SilentlyContinue) {
        $Chrome = "PATH"
    }
    else {
        foreach ($Candidate in @(
                "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
                "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
                "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
            )) {
            if (Test-Path -LiteralPath $Candidate) { $Chrome = "已安装"; break }
        }
    }
    if (-not $Chrome) { $Chrome = "缺失(可选)" }

    $Ffprobe = $null
    if (Get-Command ffprobe.exe -ErrorAction SilentlyContinue) {
        $Ffprobe = "PATH"
    }
    else {
        foreach ($Candidate in @(
                (Join-Path $Root "tools\ffmpeg\bin"),
                "C:\ffmpeg\bin",
                "D:\ffmpeg*\bin",
                "D:\code\ffmpeg*\bin"
            )) {
            $Hit = Get-Item -Path $Candidate -ErrorAction SilentlyContinue |
                Where-Object {
                    Test-Path -LiteralPath (Join-Path $_.FullName "ffprobe.exe")
                } |
                Select-Object -First 1
            if ($Hit) { $Ffprobe = $Hit.FullName; break }
        }
    }
    if (-not $Ffprobe) { $Ffprobe = "缺失(可选)" }

    $YtDlp = $null
    if (Get-Command yt-dlp.exe -ErrorAction SilentlyContinue) {
        $YtDlp = "PATH"
    }
    elseif (Test-PathReady ".venv\Scripts\yt-dlp.exe") {
        $YtDlp = "OK"
    }
    if (-not $YtDlp) { $YtDlp = "缺失(可选)" }

    $LarkCli = if (Test-PathReady "cli\node_modules\@larksuite\cli\bin\lark-cli.exe") {
        "项目内"
    }
    elseif (Get-Command lark-cli -ErrorAction SilentlyContinue) {
        "PATH"
    }
    else { "缺失（请重装）" }

    return @(
        @("项目目录", $Root),
        @("后端环境 .venv", $BackendEnv),
        @("前端依赖 node_modules", $FrontendDeps),
        @("前端构建 .next", $FrontendBuild),
        @("MCP 环境 .venv-mcp", $McpEnv),
        @("控制面配置 control.env", $ControlEnv),
        @("ffmpeg", $Ffmpeg),
        @("Playwright (Python)", $Playwright),
        @("playwright-cli (解析)", $PlaywrightCli),
        @("Chrome 浏览器", $Chrome),
        @("ffprobe", $Ffprobe),
        @("yt-dlp", $YtDlp),
        @("lark-cli (飞书桥)", $LarkCli),
        @("后端端口 $BackendPort", (Format-Listener -Map $Map -Port $BackendPort `
                    -Role "backend" -Managed $Managed -Records $Records)),
        @("前端端口 $FrontendPort", (Format-Listener -Map $Map -Port $FrontendPort `
                    -Role "frontend" -Managed $Managed -Records $Records)),
        @("MCP 端口 $McpPort", (Format-Listener -Map $Map -Port $McpPort)),
        @("控制端口 $ControlPort", (Format-Listener -Map $Map -Port $ControlPort)),
        @("飞书同步桥", (Get-BridgeState))
    )
}

function Get-PaddedText {
    # Pad by console columns: CJK characters occupy two columns, so the
    # built-in -f alignment drifts on mixed Chinese/English labels.
    param([string]$Text, [int]$Width)

    $Columns = 0
    foreach ($Character in $Text.ToCharArray()) {
        if ([int]$Character -gt 127) { $Columns += 2 } else { $Columns += 1 }
    }
    $Padding = [Math]::Max(0, $Width - $Columns)
    return $Text + (" " * $Padding)
}

function Show-Status {
    foreach ($Row in (Get-StatusLines)) {
        $Name = $Row[0]
        $Value = $Row[1]
        $Color = "Gray"
        if ($Value -like "运行中*") {
            # 跑着但不归 runtime.json 管，颜色上就要跟受管实例分开，
            # 免得又出现"显示运行中、点 [4] 却停不掉"的困惑。
            $Color = if ($Value -match "非受管|其他程序") { "Yellow" } else { "Green" }
        }
        elseif ($Value -eq "OK" -or $Value -eq "PATH" -or $Value -eq "项目内") {
            $Color = "Green"
        }
        elseif ($Value -eq "缺失" -or $Value -eq "未找到") {
            $Color = "Red"
        }
        elseif ($Value -like "*可选*" -or $Value -like "需先装*") {
            # 功能依赖缺失但核心服务能起，黄而不是红，免得和真正的启动故障混淆。
            $Color = "Yellow"
        }
        Write-Host ("  " + (Get-PaddedText -Text $Name -Width 32)) -NoNewline `
            -ForegroundColor DarkGray
        Write-Host $Value -ForegroundColor $Color
    }
}

function Invoke-Setup {
    $Extra = @()
    if ($WithMcp) { $Extra += "-WithMcp" }
    if ($WithDev) { $Extra += "-WithDev" }
    if ($WithoutBilling) { $Extra += "-WithoutBilling" }
    if ($Action -eq "menu" -and -not $WithMcp) {
        $Answer = Read-Host "  是否同时安装可选的只读 MCP 服务环境？(y/N)"
        if ($Answer -match '^(y|Y)') {
            $Extra += "-WithMcp"
        }
    }
    $SetupScript = Join-Path $Root "scripts\setup.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SetupScript @Extra
}

function Invoke-ForegroundStart {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $Root "launchers\start.ps1")
}

function Invoke-BackgroundStart {
    $Launcher = Join-Path $Root "launchers\启动.cmd"
    if (Test-Path -LiteralPath $Launcher) {
        & $Launcher
    }
    else {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass `
            -File (Join-Path $Root "launchers\start.ps1")
    }
}

function Invoke-Stop {
    # 先停飞书同步桥：它通常由 start.ps1 拉进同一个 Job Object 自动回收，
    # 但用户也可能单独 bridge-start 起过，停服务时不该留一个还在轮询飞书的孤儿。
    if ((Get-BridgeState) -like "运行中*") {
        Invoke-Bridge -Sub stop
    }
    # stop.ps1 只认 runtime.json。开发模式的实例不在册，先记下来，
    # 停完再指路 [11] —— 否则用户看到的只有一句"没有找到本项目的运行状态"。
    $Unmanaged = @(Get-UnmanagedSummary)
    & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $Root "scripts\stop.ps1")
    if ($Unmanaged.Count) {
        Write-Host ""
        Write-Host "  注意：下列实例不受 runtime.json 管理，[4] 按设计不会停它们：" -ForegroundColor Yellow
        foreach ($Line in $Unmanaged) {
            Write-Host "    $Line" -ForegroundColor Yellow
        }
        Write-Host "  它们由开发模式（dev.cmd）拉起，请用 [11] 或 TokBrain.cmd dev-stop 停止。" `
            -ForegroundColor DarkGray
    }
}

function Invoke-DevMode {
    & (Join-Path $Root "launchers\dev.cmd")
}

function Get-UnmanagedSummary {
    # 给 [4] 用：找出占着端口但不归 runtime.json 管的实例。
    # stop.ps1 按设计不会碰它们，所以要在这里把话说清楚。
    $Map = Get-ListenerMap -Ports @($BackendPort, $FrontendPort)
    if (-not $Map.Count) { return @() }

    $Managed = Get-RuntimeIdentity
    $Records = Get-ProcessRecords -ProcessIds @($Map.Values)
    $Lines = @()
    foreach ($Spec in @(
            [pscustomobject]@{ role = "后端"; port = $BackendPort; key = "backend" },
            [pscustomobject]@{ role = "前端"; port = $FrontendPort; key = "frontend" }
        )) {
        if (-not $Map.ContainsKey($Spec.port)) { continue }
        $ProcessId = [int]$Map[$Spec.port]
        $Owner = Get-ListenerOwner -ProcessId $ProcessId -Role $Spec.key `
            -Managed $Managed -Records $Records
        if ($Owner -eq "unmanaged") {
            $Lines += "$($Spec.role) PID $ProcessId（端口 $($Spec.port)）"
        }
    }
    return $Lines
}

function Get-OwnProcessTreeIds {
    # 算出"要一起停掉的一批 PID"，只查表不动手。
    # next dev 会再拉一个 next-server 子进程，只杀子进程父进程还会重起，
    # 所以先向上找到同类进程里最靠上的那个，再向下收整棵树。
    # 根放在最前，其余是按深度收敛后的顺序（调用方要倒着杀）。
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][scriptblock]$IsSameKind
    )

    $Result = @{ root = $ProcessId; ids = @($ProcessId) }
    try {
        $All = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    }
    catch {
        return $Result
    }

    $ById = @{}
    $Children = @{}
    foreach ($Entry in $All) {
        $ChildId = [int]$Entry.ProcessId
        # 只留判定需要的字段；parent 必须留着，否则上溯一步就断。
        $ById[$ChildId] = @{
            name    = [string]$Entry.Name
            cmdline = [string]$Entry.CommandLine
            parent  = [int]$Entry.ParentProcessId
        }
        $ParentId = [int]$Entry.ParentProcessId
        if (-not $Children.ContainsKey($ParentId)) {
            $Children[$ParentId] = [System.Collections.Generic.List[int]]::new()
        }
        $Children[$ParentId].Add($ChildId)
    }

    # 上溯到最靠上的同类进程。进程名和命令行要一起判：cmd /k "…uvicorn…"
    # 这种外壳的命令行也含关键字，只看文本会一路溯到 cmd 窗口上去。
    $Top = $ProcessId
    for ($Hop = 0; $Hop -lt 12; $Hop++) {
        if (-not $ById.ContainsKey($Top)) { break }
        $ParentId = [int]$ById[$Top].parent
        if (-not $ById.ContainsKey($ParentId)) { break }
        if (-not (& $IsSameKind $ById[$ParentId])) { break }
        $Top = $ParentId
    }

    $Order = [System.Collections.Generic.List[int]]::new()
    $Stack = [System.Collections.Generic.Stack[int]]::new()
    $Seen = @{}
    $Stack.Push($Top)
    while ($Stack.Count -gt 0) {
        $Current = $Stack.Pop()
        if ($Seen.ContainsKey($Current)) { continue }
        $Seen[$Current] = $true
        $Order.Add($Current)
        if ($Children.ContainsKey($Current)) {
            foreach ($ChildId in $Children[$Current]) { $Stack.Push($ChildId) }
        }
    }

    return @{ root = $Top; ids = @($Order) }
}

function Stop-OwnProcessTree {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][scriptblock]$IsSameKind
    )

    $Info = Get-OwnProcessTreeIds -ProcessId $ProcessId -IsSameKind $IsSameKind
    # 后进先出：叶子先死、根最后死，中间不会被父进程重新拉起。
    for ($Slot = $Info.ids.Count - 1; $Slot -ge 0; $Slot--) {
        Stop-Process -Id $Info.ids[$Slot] -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-DevStop {
    # 独立入口：只停 dev.cmd 拉起的前后端，完全不读也不写 runtime.json。
    # 分流原则——端口来自 netstat（事实），命令行证明是本项目（身份），
    # runtime.json 证明是受管实例（那就该走 [4]，这里拒绝动手）。
    param([switch]$PreviewOnly)

    $Map = Get-ListenerMap -Ports @($BackendPort, $FrontendPort)
    if (-not $Map.Count) {
        Write-Host "  8001 / 3001 都没有在监听；没有可停的开发模式实例。" -ForegroundColor Gray
        return
    }

    $Managed = Get-RuntimeIdentity
    $Records = Get-ProcessRecords -ProcessIds @($Map.Values)
    $Targets = @()
    $Refused = @()

    foreach ($Spec in @(
            [pscustomobject]@{
                role        = "后端"
                port        = $BackendPort
                key         = "backend"
                managed_pid = $Managed.backend
            },
            [pscustomobject]@{
                role        = "前端"
                port        = $FrontendPort
                key         = "frontend"
                managed_pid = $Managed.frontend
            }
        )) {
        if (-not $Map.ContainsKey($Spec.port)) { continue }
        $ProcessId = [int]$Map[$Spec.port]

        if ($Spec.managed_pid -and $ProcessId -eq [int]$Spec.managed_pid) {
            $Refused += ("$($Spec.role) PID $ProcessId 由 start.ps1 启动并记录在案" +
                "（端口 $($Spec.port)），请用 [4] 停止服务")
            continue
        }

        $Record = $null
        if ($Records.ContainsKey($ProcessId)) {
            $Record = $Records[$ProcessId]
        }
        if ($Spec.key -eq "backend") {
            $Own = Test-OwnBackendProcess -Record $Record
        }
        else {
            $Own = Test-OwnFrontendProcess -Record $Record
        }
        if (-not $Own) {
            $Refused += ("$($Spec.role) PID $ProcessId 无法确认为本项目实例" +
                "（端口 $($Spec.port) 可能被其他程序占用），未处理")
            continue
        }
        $Targets += [pscustomobject]@{
            role = $Spec.role
            port = $Spec.port
            key  = $Spec.key
            pid  = $ProcessId
        }
    }

    foreach ($Line in $Refused) {
        Write-Host "  跳过：$Line" -ForegroundColor Yellow
    }
    if (-not $Targets.Count) {
        Write-Host "  没有可停止的开发模式实例。" -ForegroundColor Gray
        return
    }

    if ($PreviewOnly) {
        Write-Host "  [预览] 以下实例将被停止（-DryRun 未真正执行）：" -ForegroundColor Cyan
        foreach ($Target in $Targets) {
            if ($Target.key -eq "backend") {
                $Info = Get-OwnProcessTreeIds -ProcessId $Target.pid -IsSameKind {
                    param($Record) Test-OwnBackendProcess -Record $Record
                }
            }
            else {
                $Info = Get-OwnProcessTreeIds -ProcessId $Target.pid -IsSameKind {
                    param($Record) Test-OwnFrontendProcess -Record $Record
                }
            }
            $Detail = if ($Info.ids.Count -gt 1) {
                "（整棵树 $($Info.ids.Count) 个进程，根 PID $($Info.root)）"
            }
            else {
                "（单进程）"
            }
            Write-Host "    $($Target.role) PID $($Target.pid)（端口 $($Target.port)）$Detail" `
                -ForegroundColor White
        }
        return
    }

    foreach ($Target in $Targets) {
        Write-Host "  停止 $($Target.role) PID $($Target.pid)（端口 $($Target.port)）" `
            -ForegroundColor Cyan -NoNewline
        if ($Target.key -eq "backend") {
            Stop-OwnProcessTree -ProcessId $Target.pid -IsSameKind {
                param($Record) Test-OwnBackendProcess -Record $Record
            }
        }
        else {
            Stop-OwnProcessTree -ProcessId $Target.pid -IsSameKind {
                param($Record) Test-OwnFrontendProcess -Record $Record
            }
        }
        Write-Host " ... 已发出终止" -ForegroundColor DarkGray
    }

    # 复查端口：只报"已停止"却留下占端口的孤儿，是这类脚本最坑人的失败方式。
    $Deadline = (Get-Date).AddSeconds(10)
    $After = $Map
    do {
        Start-Sleep -Milliseconds 300
        $After = Get-ListenerMap -Ports @($BackendPort, $FrontendPort)
    } while ($After.Count -and (Get-Date) -lt $Deadline)

    $Left = @()
    foreach ($Target in $Targets) {
        if ($After.ContainsKey($Target.port)) {
            $Left += "$($Target.role) 端口 $($Target.port) 仍被 PID $($After[$Target.port]) 占用"
        }
    }
    if ($Left.Count) {
        Write-Host "  未能完全停止：$($Left -join '；')" -ForegroundColor Red
        Write-Host "  请手动关闭对应窗口，或在任务管理器结束该 PID。" -ForegroundColor DarkGray
        return
    }
    Write-Host "  开发模式实例已停止；未触碰 runtime.json，受管服务不受影响。" -ForegroundColor Green
}

function Invoke-Mcp {
    if (-not (Test-PythonReady ".venv-mcp\Scripts\python.exe")) {
        Write-Host "  MCP 环境未安装。先执行安装，并在询问 MCP 时回答 y。" -ForegroundColor Yellow
        Write-Host "  等价命令：.\scripts\setup.ps1 -WithMcp" -ForegroundColor DarkGray
        return
    }
    & (Join-Path $Root "mcp_server\启动MCP服务.cmd")
}

function Invoke-Control {
    if (-not (Test-PythonReady ".venv-mcp\Scripts\python.exe")) {
        Write-Host "  控制面与只读 MCP 共用 .venv-mcp，但该环境未安装。" -ForegroundColor Yellow
        Write-Host "  等价命令：.\scripts\setup.ps1 -WithMcp" -ForegroundColor DarkGray
        return
    }
    if (-not (Test-PathReady "mcp_server\control.env")) {
        Write-Host "  还没有 mcp_server\control.env（含访问令牌）。" -ForegroundColor Yellow
        Write-Host "  copy mcp_server\control.env.example mcp_server\control.env" -ForegroundColor DarkGray
        Write-Host "  .venv-mcp\Scripts\python.exe -c ""import secrets;print(secrets.token_hex(16))""" -ForegroundColor DarkGray
        Write-Host "  把输出的 32 位填进 TOKBRAIN_CONTROL_TOKEN，并把 HOST 改成局域网地址。" -ForegroundColor DarkGray
        return
    }
    & (Join-Path $Root "mcp_server\启动控制MCP.cmd")
}

function Invoke-Bridge {
    # 起停与查看统一走 tokbrain_control.bridge：与控制 MCP 的 tb_sync_* 是同一份实现，
    # 命令行能做的 MCP 都能做，反过来也成立。
    param(
        [string]$Sub = "status",
        [switch]$Confirm
    )
    if (-not (Test-PythonReady ".venv\Scripts\python.exe")) {
        Write-Host "  .venv 未安装，无法控制飞书同步桥。" -ForegroundColor Yellow
        Write-Host "  等价命令：.\scripts\setup.ps1" -ForegroundColor DarkGray
        return
    }
    if ($Sub -eq "start" -and $Confirm) {
        # 飞书同步桥会访问用户已授权的飞书账号与多维表格，启动前必须确认授权范围。
        Write-Host ""
        if (-not (Test-LarkCliReady)) {
            Write-Host "  ⚠ 未检测到 lark-cli（飞书授权客户端）。" -ForegroundColor Yellow
            Write-Host "  飞书同步桥需要 lark-cli 登录你的飞书账号才能读写多维表格。" -ForegroundColor White
            Write-Host "  请重新执行安装，再在设置页完成「飞书授权」。" -ForegroundColor DarkGray
            $Answer = Read-Host "  仍要尝试启动飞书同步桥？(y/N)"
            if ($Answer -notmatch '^[yY]') {
                Write-Host "  已取消启动。" -ForegroundColor Gray
                return
            }
        }
        else {
            Write-Host "  飞书同步桥启动后将以下列身份访问你的飞书账号：" -ForegroundColor Cyan
            Write-Host "    · 读取 / 写入你已授权的多维表格（视频链接、处理状态、标签等）" -ForegroundColor White
            Write-Host "    · 通过 lark-cli device flow 使用你的访问令牌，仅在本地运行" -ForegroundColor White
            $Answer = Read-Host "  确认启动飞书同步桥？(y/N)"
            if ($Answer -notmatch '^[yY]') {
                Write-Host "  已取消启动。" -ForegroundColor Gray
                return
            }
        }
    }
    & (Join-Path $Root ".venv\Scripts\python.exe") -m tokbrain_control sync $Sub
}

function Invoke-OpenBrowser {
    try {
        Start-Process $LocalUrl
    }
    catch {
        Write-Host "  未能自动打开浏览器，请手动访问 $LocalUrl" -ForegroundColor Yellow
    }
}

function Invoke-Action {
    param([string]$Name)

    switch ($Name) {
        "status" {
            Show-Status
        }
        "install" {
            Invoke-Setup
        }
        "start" {
            Invoke-ForegroundStart
        }
        "start-bg" {
            Invoke-BackgroundStart
        }
        "stop" {
            Invoke-Stop
        }
        "restart" {
            Invoke-Stop
            Invoke-BackgroundStart
        }
        "dev" {
            Invoke-DevMode
        }
        "dev-stop" {
            Invoke-DevStop -PreviewOnly:$DryRun
        }
        "mcp" {
            Invoke-Mcp
        }
        "control" {
            Invoke-Control
        }
        "bridge" {
            Invoke-Bridge
        }
        "bridge-start" {
            Invoke-Bridge -Sub start
        }
        "bridge-stop" {
            Invoke-Bridge -Sub stop
        }
        "open" {
            Invoke-OpenBrowser
        }
    }
}

if ($Action -ne "menu") {
    $global:LASTEXITCODE = 0
    Invoke-Action -Name $Action
    exit $LASTEXITCODE
}

while ($true) {
    Clear-Host
    Write-Host ""
    Write-Host "  ================================================" -ForegroundColor Cyan
    Write-Host "   TokBrain 控制台" -ForegroundColor Cyan
    Write-Host "  ================================================" -ForegroundColor Cyan
    Show-Status
    Write-Host ""
    Write-Host "   [1] 安装 / 重建环境（首次使用）" -ForegroundColor White
    Write-Host "   [2] 启动服务（前台，关闭窗口即停止）" -ForegroundColor White
    Write-Host "   [3] 后台启动（隐藏窗口）" -ForegroundColor White
    Write-Host "   [4] 停止服务" -ForegroundColor White
    Write-Host "   [5] 重启服务" -ForegroundColor White
    Write-Host "   [6] 开发模式（后端 + 前端热更新）" -ForegroundColor White
    Write-Host "   [7] 启动只读 MCP 服务（:8010）" -ForegroundColor White
    Write-Host "   [8] 启动控制 MCP 服务（:8011，可写）" -ForegroundColor White
    Write-Host "   [9] 飞书同步桥（运行中就停止，没运行就启动）" -ForegroundColor White
    Write-Host "   [10] 打开网页 $LocalUrl" -ForegroundColor White
    Write-Host "   [11] 停止开发模式实例（热更新那条，[4] 停不掉的就是它）" -ForegroundColor White
    Write-Host "   [0] 退出" -ForegroundColor White
    Write-Host ""

    $Choice = Read-Host "  请选择"
    switch ($Choice.Trim()) {
        "1" { Invoke-Setup }
        "2" { Invoke-ForegroundStart }
        "3" { Invoke-BackgroundStart }
        "4" { Invoke-Stop }
        "5" { Invoke-Stop; Invoke-BackgroundStart }
        "6" { Invoke-DevMode }
        "7" { Invoke-Mcp }
        "8" { Invoke-Control }
        "9" {
            if ((Get-BridgeState) -like "运行中*") {
                Invoke-Bridge -Sub stop
            }
            else {
                Invoke-Bridge -Sub start -Confirm
            }
        }
        "10" { Invoke-OpenBrowser }
        "11" { Invoke-DevStop }
        "0" { return }
        default {
            Write-Host "  未知选项：$Choice" -ForegroundColor Yellow
        }
    }
    if ($Choice.Trim() -eq "0") { return }
    Write-Host ""
    Write-Host "  按任意键返回菜单..." -ForegroundColor DarkGray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
