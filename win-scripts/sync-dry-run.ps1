# Use $args (not a ValueFromRemainingArguments parameter) so flags forwarded to
# the sync survive the powershell.exe -File launch.
. "$PSScriptRoot\windows-common.ps1"

$forwardedArguments = @($args | Where-Object { -not [string]::IsNullOrEmpty($_) })
[string[]]$arguments = @(
    "exec", "--user", "mrag",
    "mrag-batch",
    "python", "/app/batch/sync.py",
    "--dry-run"
) + $forwardedArguments
Invoke-ProjectCompose $arguments
