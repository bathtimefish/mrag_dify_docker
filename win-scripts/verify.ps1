. "$PSScriptRoot\windows-common.ps1"

function Convert-ComposeJson {
    param([object[]]$Lines)

    $raw = ($Lines | ForEach-Object { [string]$_ }) -join "`n"
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @()
    }
    try {
        return @(ConvertFrom-Json -InputObject $raw)
    }
    catch {
        $records = @()
        foreach ($line in $raw -split "`r?`n") {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                $records += ConvertFrom-Json -InputObject $line
            }
        }
        return $records
    }
}

$expectedServices = @(
    "agent_backend",
    "api",
    "api_websocket",
    "db_postgres",
    "docling-serve",
    "local_sandbox",
    "mrag-api",
    "mrag-batch",
    "mrag-qdrant",
    "nginx",
    "ollama",
    "plugin_daemon",
    "qdrant",
    "redis",
    "sandbox",
    "ssrf_proxy",
    "web",
    "worker",
    "worker_beat"
)

$timeoutSeconds = 240
if (-not [string]::IsNullOrWhiteSpace($env:VERIFY_TIMEOUT_SECONDS)) {
    $timeoutSeconds = [int]$env:VERIFY_TIMEOUT_SECONDS
}
$deadline = [DateTime]::UtcNow.AddSeconds($timeoutSeconds)

while ($true) {
    $running = @(
        Invoke-ProjectCompose "ps", "--services", "--status", "running" |
            ForEach-Object { ([string]$_).Trim() } |
            Where-Object { $_ }
    )
    $missing = @($expectedServices | Where-Object { $_ -notin $running })

    $records = Convert-ComposeJson @(
        Invoke-ProjectCompose "ps", "--format", "json"
    )
    $pendingHealth = @(
        $records |
            Where-Object {
                ([string]$_.Health).ToLowerInvariant() -in @("starting", "unhealthy")
            } |
            ForEach-Object {
                $name = if ($_.Service) { $_.Service } else { $_.Name }
                "$name`: $($_.Health)"
            }
    )

    if ($missing.Count -eq 0 -and $pendingHealth.Count -eq 0) {
        break
    }
    if ([DateTime]::UtcNow -ge $deadline) {
        if ($missing.Count -gt 0) {
            Write-Error "Not running: $($missing -join ', ')"
        }
        if ($pendingHealth.Count -gt 0) {
            Write-Error "Health not ready: $($pendingHealth -join ', ')"
        }
        Invoke-ProjectCompose "ps"
        throw "Timed out waiting for the stack."
    }
    Start-Sleep -Seconds 5
}

$initializers = Convert-ComposeJson @(
    Invoke-ProjectCompose `
        "ps", "--all", "--format", "json", `
        "ollama-model-init", "mrag-init"
)
$expectedInitializers = @("ollama-model-init", "mrag-init")
foreach ($service in $expectedInitializers) {
    $record = @($initializers | Where-Object { $_.Service -eq $service })
    if ($record.Count -eq 0) {
        throw "Missing initialization service: $service."
    }
    if ($record[0].State -ne "exited" -or [int]$record[0].ExitCode -ne 0) {
        throw "$service did not complete successfully."
    }
}

$envPath = Join-Path $script:RepositoryRoot ".env"
$difyPort = $env:EXPOSE_NGINX_PORT
if ([string]::IsNullOrWhiteSpace($difyPort)) {
    $difyPort = Get-DotEnvValue $envPath "EXPOSE_NGINX_PORT"
}
if ([string]::IsNullOrWhiteSpace($difyPort)) {
    $difyPort = "80"
}
$mragPort = $env:MRAG_EXPOSE_PORT
if ([string]::IsNullOrWhiteSpace($mragPort)) {
    $mragPort = Get-DotEnvValue $envPath "MRAG_EXPOSE_PORT"
}
if ([string]::IsNullOrWhiteSpace($mragPort)) {
    $mragPort = "8000"
}
Test-HttpEndpoint "http://127.0.0.1:$difyPort/install"
Test-HttpEndpoint "http://127.0.0.1:$difyPort/console/api/setup"
Test-HttpEndpoint "http://127.0.0.1:$mragPort/openapi.json"

$doclingProbe = @'
import json
import os
import urllib.request

request = urllib.request.Request(
    'http://127.0.0.1:5001/version',
    headers={'X-Api-Key': os.environ['DOCLING_SERVE_API_KEY']},
)
with urllib.request.urlopen(request, timeout=10) as response:
    payload = json.load(response)
if payload.get('docling-serve') != '1.28.0':
    raise SystemExit(f'unexpected docling-serve version: {payload!r}')
if payload.get('docling') != '2.115.0':
    raise SystemExit(f'unexpected Docling version: {payload!r}')
'@
Invoke-ProjectCompose "exec", "-T", "docling-serve", "python", "-c", $doclingProbe

$doclingSmokeProbe = @'
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '/app/batch')
from docling_http_client import HttpDoclingClient
from docling_ingestion import DoclingSettings, SourceDocument

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'docling-smoke.txt'
    path.write_text('Docling verification marker', encoding='utf-8')
    stat = path.stat()
    result = HttpDoclingClient().convert(
        SourceDocument(
            relative_path=path.name,
            absolute_path=path,
            sha256='verify-only',
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        ),
        DoclingSettings.from_environment(),
    )
if result.status.value != 'success':
    raise SystemExit(f'Docling smoke conversion failed: {result.status.value}')
if 'Docling verification marker' not in (result.markdown or ''):
    raise SystemExit('Docling smoke conversion returned unexpected Markdown')
'@
Invoke-ProjectCompose "exec", "-T", "mrag-batch", "python", "-c", $doclingSmokeProbe

# NOTE: Use single-quoted Python string literals only. Windows PowerShell 5.1
# strips embedded double quotes when passing an argument to a native command,
# so double quotes here would be lost before the probe reaches Python.
$profileProbe = @'
from pathlib import Path
from mrag.config.profile import load_profile

project_dir = Path('/data/mrag')
profile = load_profile('default', project_dir)
if not profile.rerank.enabled:
    raise SystemExit('default profile reranking is disabled')
print(f'Reranker ready: {profile.rerank.model}')
'@
Invoke-ProjectCompose "exec", "-T", "mrag-api", "python", "-c", $profileProbe

$apiKey = (
    Invoke-ProjectCompose `
        "exec", "-T", "mrag-api", `
        "sh", "-c", 'printf %s $MRAG_API_KEY' |
        Out-String
).Trim()
$registrationProbe = @'
import sys
from core.helper import ssrf_proxy

# Windows PowerShell prepends a UTF-8 BOM when piping text to a native
# command's stdin. Decode with utf-8-sig so the leading ﻿ is stripped
# before it can corrupt the Authorization header (it is a no-op elsewhere).
key = sys.stdin.buffer.read().decode('utf-8-sig').strip()
response = ssrf_proxy.post(
    'http://mrag-api:8000/retrieval',
    headers={'Authorization': 'Bearer ' + key},
)
print(response.status_code)
'@
$registrationStatus = (
    Invoke-ProjectComposeWithInput `
        -InputText $apiKey `
        -Arguments @("exec", "-T", "api", "python", "-c", $registrationProbe) |
        Out-String
).Trim()
if ($registrationStatus -ne "422") {
    throw "Unexpected Dify-to-mrag registration status: $registrationStatus."
}

$redisProbe = @'
import os
from urllib.parse import urlparse
import redis

broker = urlparse(os.environ['CELERY_BROKER_URL'])
if broker.password != os.environ.get('REDIS_PASSWORD'):
    raise SystemExit('CELERY_BROKER_URL password does not match REDIS_PASSWORD')
client = redis.Redis(
    host=broker.hostname,
    port=broker.port or 6379,
    password=broker.password,
    db=int(broker.path.lstrip('/') or '0'),
)
if not client.ping():
    raise SystemExit('Celery Redis broker ping failed')
'@
Invoke-ProjectCompose "exec", "-T", "worker", "python", "-c", $redisProbe

# Exit status alone is not enough: per-document conversion failures are a
# supported outcome that exits 0, so the terminal event decides the verdict.
# A system failure exits non-zero and Invoke-ProjectCompose throws before this.
$syncOutput = Invoke-ProjectCompose @(
    "exec", "--user", "mrag",
    "mrag-batch", "python", "/app/batch/sync.py"
)
$syncOutput | ForEach-Object { Write-Host $_ }

$terminalEvents = @(
    "sync_completed",
    "sync_completed_with_errors",
    "sync_failed",
    "sync_skipped",
    "sync_no_changes"
)
$syncOutcome = $null
foreach ($line in $syncOutput) {
    $text = "$line".Trim()
    if (-not $text.StartsWith("{")) { continue }
    try { $event = $text | ConvertFrom-Json } catch { continue }
    if ($terminalEvents -contains $event.event) { $syncOutcome = $event }
}

if ($null -eq $syncOutcome) {
    throw "Synchronization produced no terminal event; treating as failure."
}
switch ($syncOutcome.event) {
    "sync_completed" { }
    "sync_no_changes" { }
    "sync_completed_with_errors" {
        # A supported outcome: healthy documents synchronized while some were
        # degraded. Report it loudly, but do not fail verification.
        Write-Warning "Synchronization completed with document-level errors."
        Write-Warning ($syncOutcome | ConvertTo-Json -Compress)
        Write-Warning ("Inspect them with: .\win-scripts\compose.bat exec --user mrag " +
            "mrag-batch python /app/batch/docling_admin.py status " +
            "--converted-dir /data/converted-docs")
    }
    "sync_skipped" {
        throw "Synchronization was skipped by the exclusive lock, so nothing was verified."
    }
    default {
        throw "Synchronization reported $($syncOutcome.event)."
    }
}

Write-Host "Verification succeeded."
Write-Host "Dify:     http://localhost:$difyPort/install"
Write-Host "mrag API: http://localhost:$mragPort/docs"
