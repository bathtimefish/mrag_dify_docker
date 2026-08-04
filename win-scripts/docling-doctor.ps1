# Use $args (not a ValueFromRemainingArguments parameter) so flags forwarded to
# the command survive the powershell.exe -File launch.
. "$PSScriptRoot\windows-common.ps1"

$forwardedArguments = @($args | Where-Object { -not [string]::IsNullOrEmpty($_) })
# Reports the effective Docling settings and probes the server. Pass
# --no-probe to report settings only, without any network access.
[string[]]$arguments = @(
    "exec", "-T", "--user", "mrag",
    "mrag-batch",
    "python", "/app/batch/docling_admin.py", "doctor"
) + $forwardedArguments
Invoke-ProjectCompose $arguments
