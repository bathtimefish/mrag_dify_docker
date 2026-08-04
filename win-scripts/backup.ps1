. "$PSScriptRoot\windows-common.ps1"

[string[]]$backupArguments = @(
    (Join-Path $script:RepositoryRoot "scripts\stack_backup.py"),
    "--root",
    $script:RepositoryRoot,
    "backup"
) + $args
Invoke-ProjectPython $backupArguments
