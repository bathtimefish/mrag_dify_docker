# Use $args (not a ValueFromRemainingArguments parameter) so flags forwarded to
# the command survive the powershell.exe -File launch.
. "$PSScriptRoot\windows-common.ps1"

$forwardedArguments = @($args | Where-Object { -not [string]::IsNullOrEmpty($_) })
# Runs the Docling conversion phase and stops before touching mrag.
# Inspect the outcome with docling-status.bat.
[string[]]$arguments = @(
    "exec", "--user", "mrag",
    "mrag-batch",
    "python", "/app/batch/sync.py", "--convert-only"
) + $forwardedArguments
Invoke-ProjectCompose $arguments
