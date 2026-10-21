#requires -Version 5.1
<#
.SYNOPSIS
Start the prepared academic SFT v1 policy on CPU, or stop its owned process.
.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\start-academic-tool-policy.ps1
.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\start-academic-tool-policy.ps1 -Stop
.NOTES
Uses only existing, manifest-verified assets. No downloads or training.
Flags verified against llama.cpp d81235049384534c167caea52b85a694f6103d14/common/arg.cpp.
Readiness checks never run inference. The API listens only on 127.0.0.1:8091.
#>
[CmdletBinding()]
param([switch]$Stop)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$assetRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'outputs\academic-tools-local-v1'))
$serverExe = Join-Path $assetRoot 'runtime\llama-server.exe'
$baseModel = Join-Path $assetRoot 'models\Qwen3-8B-Q4_K_M.gguf'
$adapterModel = Join-Path $assetRoot 'models\sft-v1.gguf'
$chatTemplate = Join-Path $assetRoot 'models\qwen3-nonthinking.jinja'
$pidFile = Join-Path $assetRoot 'server.pid'
$modelAlias = 'academic-tools-sft-v1'
$serverPort = 8091
$serverUri = 'http://127.0.0.1:8091'

function Get-PolicyListeners {
    # Query all listeners so an empty port does not hide a failed CIM query.
    return @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalPort -eq $serverPort })
}

function Get-OwnedProcess([int]$ProcessId) {
    $candidate = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $candidate) { return $null }
    try { $actualPath = [IO.Path]::GetFullPath($candidate.Path) }
    catch { throw 'Cannot verify the recorded server process executable.' }
    if (-not [string]::Equals($actualPath, $serverExe, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Recorded process is not this runtime. No process was stopped or replaced.'
    }
    return $candidate
}

function Assert-OwnedListener([int]$ProcessId) {
    $listeners = @(Get-PolicyListeners)
    if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1' -or
        $listeners[0].OwningProcess -ne $ProcessId) {
        throw 'Port 8091 ownership does not match this loopback server. No process was stopped.'
    }
}

function Read-RecordedPid {
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return $null }
    $raw = [IO.File]::ReadAllText($pidFile).Trim()
    $recorded = 0
    if ($raw -notmatch '^[1-9][0-9]{0,9}$' -or -not [int]::TryParse($raw, [ref]$recorded)) {
        throw 'server.pid is invalid. No process was stopped or replaced.'
    }
    return $recorded
}

function Get-ManifestAsset([string]$RelativePath) {
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or [IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath.Contains(':') -or $RelativePath -match '(^|[\\/])\.\.?([\\/]|$)') {
        throw 'The runtime manifest contains an invalid relative path.'
    }
    $path = [IO.Path]::GetFullPath((Join-Path $assetRoot $RelativePath))
    if (-not $path.StartsWith($assetRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The runtime manifest contains a path outside its asset directory.'
    }
    $partPath = $assetRoot
    foreach ($part in ($RelativePath -split '[\\/]')) {
        if (-not $part) { throw 'The runtime manifest contains an invalid relative path.' }
        $partPath = Join-Path $partPath $part
        $item = Get-Item -LiteralPath $partPath -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Runtime assets must not contain filesystem links.'
        }
    }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'A required runtime asset is missing.'
    }
    return $path
}

function Assert-RuntimeManifest {
    $manifestPath = Join-Path $assetRoot 'runtime-manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'runtime-manifest.json is missing. Prepare the existing runtime assets first.'
    }
    try { $manifest = [IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json }
    catch { throw 'runtime-manifest.json is not valid JSON.' }
    if ($null -eq $manifest -or $null -eq $manifest.PSObject.Properties['alias'] -or
        $manifest.alias -cne $modelAlias -or $null -eq $manifest.PSObject.Properties['files'] -or
        $manifest.files -isnot [Management.Automation.PSCustomObject]) {
        throw 'The runtime manifest must declare the expected alias and a files hash mapping.'
    }
    $verified = @{}
    foreach ($entry in $manifest.files.PSObject.Properties) {
        if ($entry.Value -isnot [string] -or $entry.Value -notmatch '^[0-9a-fA-F]{64}$') {
            throw 'The runtime manifest contains an invalid SHA-256 digest.'
        }
        $path = Get-ManifestAsset $entry.Name
        if ($verified.ContainsKey($path)) { throw 'The runtime manifest contains duplicate paths.' }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $entry.Value) {
            throw ('Runtime asset hash mismatch: ' + $entry.Name)
        }
        $verified[$path] = $true
    }
    foreach ($required in @($serverExe, $baseModel, $adapterModel, $chatTemplate)) {
        if (-not $verified.ContainsKey($required)) { throw 'The runtime manifest omits a required model or runtime file.' }
    }
    foreach ($dependency in Get-ChildItem -LiteralPath (Join-Path $assetRoot 'runtime') -Filter '*.dll' -File) {
        if (-not $verified.ContainsKey($dependency.FullName)) {
            throw 'The runtime manifest omits a runtime DLL.'
        }
    }
}

function Get-ServerJson($Client, [string]$Route, [int]$TimeoutMs) {
    if ($TimeoutMs -le 0) { throw 'Readiness deadline reached.' }
    $cancellation = New-Object Threading.CancellationTokenSource
    $cancellation.CancelAfter($TimeoutMs)
    $response = $null
    try {
        $response = $Client.GetAsync($serverUri + $Route, $cancellation.Token).GetAwaiter().GetResult()
        if ([int]$response.StatusCode -ne 200) { throw 'The local server is not ready.' }
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if ($body.Length -gt 1048576) { throw 'Unexpected local server response.' }
        return ,($body | ConvertFrom-Json)
    }
    finally {
        if ($null -ne $response) { $response.Dispose() }
        $cancellation.Dispose()
    }
}

function Get-RemainingTimeout($Timer) {
    return [int][Math]::Max(0, [Math]::Min(2000, 180000 - $Timer.ElapsedMilliseconds))
}

function Wait-PolicyReady($Process) {
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = New-Object Net.Http.HttpClient($handler)
    $client.Timeout = [Threading.Timeout]::InfiniteTimeSpan
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $startedAt = $Process.StartTime.ToUniversalTime().Ticks
    try {
        while ($timer.ElapsedMilliseconds -lt 180000) {
            $current = Get-OwnedProcess $Process.Id
            if ($null -eq $current -or $current.StartTime.ToUniversalTime().Ticks -ne $startedAt) {
                throw 'The local server exited before becoming ready. Inspect its startup logs.'
            }
            $healthy = $false
            try {
                $health = Get-ServerJson $client '/health' (Get-RemainingTimeout $timer)
                $healthy = $health.status -eq 'ok'
            }
            catch { $healthy = $false }
            if ($healthy) {
                Assert-OwnedListener $Process.Id
                $models = Get-ServerJson $client '/v1/models' (Get-RemainingTimeout $timer)
                $adapterPayload = Get-ServerJson $client '/lora-adapters' (Get-RemainingTimeout $timer)
                $adapters = @($adapterPayload)
                $modelsData = @($models.data)
                if ($modelsData.Count -ne 1 -or $modelsData[0].id -cne $modelAlias -or
                    $adapters.Count -ne 1 -or $adapters[0].id -ne 0 -or $adapters[0].scale -ne 1 -or
                    -not [string]::Equals([IO.Path]::GetFullPath($adapters[0].path), $adapterModel,
                                         [StringComparison]::OrdinalIgnoreCase)) {
                    throw 'A server is listening, but its model alias or adapter does not match this runtime.'
                }
                return
            }
            $remaining = 180000 - $timer.ElapsedMilliseconds
            if ($remaining -gt 0) { Start-Sleep -Milliseconds ([int][Math]::Min(500, $remaining)) }
        }
        throw 'The local server did not become ready within 180 seconds. Inspect its startup logs; its PID was retained.'
    }
    finally {
        $timer.Stop()
        $client.Dispose()
        $handler.Dispose()
    }
}

function Quote-ServerArgument([string]$Value) {
    # All path arguments are files, so none ends in a backslash. Embedded quotes
    # are invalid in Windows filenames and are never needed by these flags.
    if ($Value.Contains('"') -or $Value.EndsWith('\')) { throw 'Invalid server argument.' }
    return '"' + $Value + '"'
}

$mutex = New-Object Threading.Mutex($false, 'Local\AcademicToolsSftV1Port8091')
$ownsMutex = $false
try {
    try { $ownsMutex = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw 'Another policy start/stop operation is in progress.' }
    $recordedId = Read-RecordedPid
    $recordedProcess = $null
    if ($null -ne $recordedId) { $recordedProcess = Get-OwnedProcess $recordedId }
    if ($Stop) {
        if ($null -eq $recordedProcess) {
            if ($null -ne $recordedId) { Remove-Item -LiteralPath $pidFile }
            Write-Output 'No recorded policy server is running.'
            return
        }
        Assert-OwnedListener $recordedProcess.Id
        # Stop the verified process object, never a process name or a port alone.
        Stop-Process -InputObject $recordedProcess -ErrorAction Stop
        $recordedProcess.WaitForExit(10000) | Out-Null
        if (-not $recordedProcess.HasExited) { throw 'The verified policy process has not exited yet.' }
        Remove-Item -LiteralPath $pidFile
        Write-Output 'Academic tool policy stopped.'
        return
    }

    Assert-RuntimeManifest
    $listeners = @(Get-PolicyListeners)
    if ($listeners.Count -gt 0) {
        if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1') {
            throw 'Port 8091 is occupied by a different listener. No process was started.'
        }
        $existing = Get-OwnedProcess ([int]$listeners[0].OwningProcess)
        if ($null -eq $existing -or ($null -ne $recordedProcess -and $existing.Id -ne $recordedProcess.Id)) {
            throw 'Port 8091 and server.pid do not identify the same runtime process.'
        }
        Wait-PolicyReady $existing
        [IO.File]::WriteAllText($pidFile, [string]$existing.Id, [Text.Encoding]::ASCII)
        Write-Output ('Academic tool policy is already ready at ' + $serverUri + '.')
        return
    }
    if ($null -ne $recordedProcess) {
        Wait-PolicyReady $recordedProcess
        Write-Output ('Academic tool policy is ready at ' + $serverUri + '.')
        return
    }
    if ($null -ne $recordedId) { Remove-Item -LiteralPath $pidFile }

    $arguments = @(
        '-m', $baseModel, '--lora', $adapterModel, '-ngl', '0', '-c', '8192',
        '-np', '1', '-t', '6', '-tb', '6', '--jinja', '--chat-template-file', $chatTemplate,
        '--reasoning', 'off', '--reasoning-format', 'none', '--no-context-shift',
        '--temp', '0', '--repeat-penalty', '1', '--presence-penalty', '0',
        '--no-webui', '--log-disable', '--host', '127.0.0.1', '--port', '8091', '--alias', $modelAlias
    )
    $commandLine = ($arguments | ForEach-Object { Quote-ServerArgument $_ }) -join ' '
    $logStem = 'server-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N')
    $stdoutLog = Join-Path $assetRoot ($logStem + '.stdout.log')
    $stderrLog = Join-Path $assetRoot ($logStem + '.stderr.log')
    # Prevent ambient llama.cpp options from changing this explicitly local run.
    $savedOptions = @(Get-ChildItem Env: | Where-Object { $_.Name.StartsWith('LLAMA_ARG_', [StringComparison]::OrdinalIgnoreCase) })
    try {
        foreach ($option in $savedOptions) { [Environment]::SetEnvironmentVariable($option.Name, $null, 'Process') }
        $serverProcess = Start-Process -FilePath $serverExe -ArgumentList $commandLine -WorkingDirectory $assetRoot `
            -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
    }
    finally {
        foreach ($option in $savedOptions) { [Environment]::SetEnvironmentVariable($option.Name, $option.Value, 'Process') }
    }
    [IO.File]::WriteAllText($pidFile, [string]$serverProcess.Id, [Text.Encoding]::ASCII)
    Wait-PolicyReady $serverProcess
    Write-Output ('Academic tool policy is ready at ' + $serverUri + ' (CPU, SFT v1).')
    Write-Output ('Startup logs: ' + $stdoutLog + ' ; ' + $stderrLog)
}
finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
