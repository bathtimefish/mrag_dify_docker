# Manage LLM models inside the Ollama container.
# Uses $args (not a ValueFromRemainingArguments parameter) so flags such as
# --force survive the powershell.exe -File launch, and passes compose arguments
# as arrays so short flags like -T are not dropped.
. "$PSScriptRoot\windows-common.ps1"

# windows-common.ps1 sets $ErrorActionPreference = 'Stop', which turns Write-Error
# into a terminating error (aborting with exit 1 and a stack trace). Write plain
# diagnostics to stderr instead so explicit exit codes are honored.
function Write-Err {
    param([string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Show-Usage {
    Write-Host @'
Usage: ollama-model.bat <command> [args]

Manage LLM models inside the Ollama container.

Commands:
  list                     List models present in the Ollama container.
  pull <model>             Pull <model> only if it is not already present.
  remove <model> [--force] Remove <model>. Refuses to remove a model referenced
                           by an mrag profile (the embedding model, or the
                           augmentation model when augmentation is enabled)
                           unless --force is given.
'@
}

function Get-NormalizedModel {
    param([string]$Model)
    $value = $Model.Trim().Trim('"').Trim("'")
    if ([string]::IsNullOrEmpty($value)) { return "" }
    if ($value.Contains(":")) { return $value }
    return "${value}:latest"
}

function Get-PresentModels {
    $raw = Invoke-ProjectCompose "exec", "-T", "ollama", "ollama", "list" | Out-String
    $names = @()
    $lines = $raw -split "`r?`n"
    for ($i = 1; $i -lt $lines.Count; $i++) {
        $line = $lines[$i].Trim()
        if ($line) {
            $names += Get-NormalizedModel (($line -split "\s+")[0])
        }
    }
    return $names
}

function Get-ProtectedModels {
    $profilesDir = Join-Path $script:RepositoryRoot "mrag_profiles"
    $envFile = Join-Path $script:RepositoryRoot ".env"
    $guard = Join-Path $script:RepositoryRoot "scripts\ollama_model_guard.py"
    $python = Get-PythonInvocation
    $raw = & $python.Command @($python.Prefix + @($guard, $profilesDir, $envFile)) | Out-String
    return @($raw -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

$arguments = @($args)
$command = if ($arguments.Count -ge 1) { $arguments[0] } else { "" }
$rest = if ($arguments.Count -ge 2) { @($arguments[1..($arguments.Count - 1)]) } else { @() }

switch ($command) {
    "list" {
        Invoke-ProjectCompose "exec", "-T", "ollama", "ollama", "list"
    }
    "pull" {
        $model = @($rest | Where-Object { $_ }) | Select-Object -First 1
        if (-not $model) { Write-Err "pull requires a model name."; Show-Usage; exit 2 }
        if ((Get-PresentModels) -contains (Get-NormalizedModel $model)) {
            Write-Host "Model '$model' is already present; skipping pull."
        }
        else {
            Write-Host "Pulling '$model' ..."
            Invoke-ProjectCompose "exec", "-T", "ollama", "ollama", "pull", $model
        }
    }
    "remove" {
        $force = $false
        $model = ""
        foreach ($item in $rest) {
            if ($item -eq "--force") { $force = $true }
            elseif ($item -like "-*") { Write-Err "Unknown option: $item"; Show-Usage; exit 2 }
            elseif (-not $model) { $model = $item }
            else { Write-Err "Unexpected argument: $item"; exit 2 }
        }
        if (-not $model) { Write-Err "remove requires a model name."; Show-Usage; exit 2 }

        if ((Get-ProtectedModels) -contains (Get-NormalizedModel $model)) {
            if (-not $force) {
                Write-Err ("Refusing to remove '$model': it is referenced by an mrag profile " +
                    "(embedding or augmentation model). Removing it will break indexing and " +
                    "retrieval. Re-run with --force to override.")
                exit 1
            }
            Write-Host "WARNING: '$model' is referenced by an mrag profile; removing anyway (--force)."
        }
        Write-Host "Removing '$model' ..."
        Invoke-ProjectCompose "exec", "-T", "ollama", "ollama", "rm", $model
    }
    { $_ -in @("-h", "--help", "help") } {
        Show-Usage
    }
    "" {
        Show-Usage
        exit 2
    }
    default {
        Write-Err "Unknown command: $command"
        Show-Usage
        exit 2
    }
}
