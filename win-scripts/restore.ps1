. "$PSScriptRoot\windows-common.ps1"

[string[]]$restoreArguments = @(
    (Join-Path $script:RepositoryRoot "scripts\stack_backup.py"),
    "--root",
    $script:RepositoryRoot,
    "restore"
) + $args
Invoke-ProjectPython $restoreArguments
