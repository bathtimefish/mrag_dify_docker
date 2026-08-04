# Use $args (not a ValueFromRemainingArguments parameter) so flags forwarded to
# the command survive the powershell.exe -File launch.
. "$PSScriptRoot\windows-common.ps1"

$forwardedArguments = @($args | Where-Object { -not [string]::IsNullOrEmpty($_) })
[string[]]$arguments = @(
    "exec", "-T", "--user", "mrag",
    "mrag-batch",
    "python", "/app/batch/docling_admin.py", "status",
    "--converted-dir", "/data/converted-docs"
) + $forwardedArguments
Invoke-ProjectCompose $arguments
