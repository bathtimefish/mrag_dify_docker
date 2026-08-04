$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:RepositoryRoot = Split-Path -Parent $PSScriptRoot

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter()]
        [string[]]$Arguments = @()
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command exited with code $LASTEXITCODE."
    }
}

function Get-PythonInvocation {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{
            Command = "python"
            Prefix = @()
        }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{
            Command = "py"
            Prefix = @("-3")
        }
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        return @{
            Command = "python3"
            Prefix = @()
        }
    }
    throw "Python 3 was not found. Install it and enable 'Add Python to PATH'."
}

function Invoke-ProjectPython {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    $python = Get-PythonInvocation
    Invoke-NativeCommand $python.Command ($python.Prefix + $Arguments)
}

function Get-VersionValues {
    $versionsPath = Join-Path $script:RepositoryRoot "VERSIONS"
    if (-not (Test-Path -LiteralPath $versionsPath -PathType Leaf)) {
        throw "Missing $versionsPath."
    }

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $versionsPath) {
        if ($line -match "^\s*([A-Z0-9_]+)=(.*)$") {
            $values[$matches[1]] = $matches[2]
        }
    }
    return $values
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $value = $null
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match ("^" + [regex]::Escape($Name) + "=(.*)$")) {
            $value = $matches[1].TrimEnd("`r")
        }
    }
    return $value
}

function Get-ComposeConfiguration {
    $envPath = Join-Path $script:RepositoryRoot ".env"
    $difyDocker = Join-Path $script:RepositoryRoot "vendor\dify\docker"
    $officialTemplate = Join-Path $difyDocker ".env.example"
    $officialOutput = Join-Path $difyDocker ".env"
    $officialCompose = Join-Path $difyDocker "docker-compose.yaml"

    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        throw "Missing .env. Run win-scripts\init-env.bat first."
    }
    if (-not (Test-Path -LiteralPath $officialCompose -PathType Leaf)) {
        throw "Missing official sources. Run win-scripts\bootstrap-vendors.bat first."
    }

    Invoke-ProjectPython `
        (Join-Path $script:RepositoryRoot "scripts\prepare-dify-env.py") `
        $officialTemplate `
        $envPath `
        $officialOutput

    $accelerator = $env:OLLAMA_ACCELERATOR
    if ([string]::IsNullOrWhiteSpace($accelerator)) {
        $accelerator = Get-DotEnvValue $envPath "OLLAMA_ACCELERATOR"
    }
    if ([string]::IsNullOrWhiteSpace($accelerator)) {
        $accelerator = "cpu"
    }

    $composeArguments = @(
        "--env-file", $envPath,
        "-f", "docker-compose.yaml",
        "-f", (Join-Path $script:RepositoryRoot "compose.images.lock.yaml"),
        "-f", (Join-Path $script:RepositoryRoot "compose.mrag.yaml")
    )
    switch ($accelerator.ToLowerInvariant()) {
        "cpu" {}
        "nvidia" {
            $composeArguments += @(
                "-f",
                (Join-Path $script:RepositoryRoot "compose.ollama.nvidia.yaml")
            )
        }
        default {
            throw "Unsupported OLLAMA_ACCELERATOR: $accelerator (expected cpu or nvidia)."
        }
    }

    $doclingAccelerator = $env:DOCLING_ACCELERATOR
    if ([string]::IsNullOrWhiteSpace($doclingAccelerator)) {
        $doclingAccelerator = Get-DotEnvValue $envPath "DOCLING_ACCELERATOR"
    }
    if ([string]::IsNullOrWhiteSpace($doclingAccelerator)) {
        $doclingAccelerator = "cpu"
    }
    switch ($doclingAccelerator.ToLowerInvariant()) {
        "cpu" {}
        "nvidia" {
            $composeArguments += @(
                "-f",
                (Join-Path $script:RepositoryRoot "compose.docling.nvidia.yaml")
            )
        }
        default {
            throw "Unsupported DOCLING_ACCELERATOR: $doclingAccelerator (expected cpu or nvidia)."
        }
    }

    return @{
        WorkingDirectory = $difyDocker
        Arguments = $composeArguments
    }
}

function Invoke-ProjectCompose {
    # Accept the compose arguments as a single [string[]] array rather than via
    # ValueFromRemainingArguments. Windows PowerShell re-parses splatted or
    # remaining tokens and silently drops short flags such as "-d" or "-T"
    # (treating them as parameter names). Callers must therefore pass a single
    # array (a comma list literal or an array variable), never a splat.
    param(
        [Parameter(Position = 0)]
        [string[]]$Arguments = @()
    )

    $configuration = Get-ComposeConfiguration
    Push-Location $configuration.WorkingDirectory
    try {
        Invoke-NativeCommand "docker" (@("compose") + $configuration.Arguments + $Arguments)
    }
    finally {
        Pop-Location
    }
}

function Invoke-ProjectComposeWithInput {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$InputText,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $configuration = Get-ComposeConfiguration
    Push-Location $configuration.WorkingDirectory
    try {
        $InputText | & docker compose @($configuration.Arguments) @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose exited with code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

function Test-HttpEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [int]$Attempts = 12,

        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 10 | Out-Null
            return
        }
        catch {
            if ($attempt -eq $Attempts) {
                throw "HTTP verification failed for $Uri`: $($_.Exception.Message)"
            }
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}
