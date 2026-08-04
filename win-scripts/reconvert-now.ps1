# Use $args (not a ValueFromRemainingArguments parameter) so flags forwarded to
# the command survive the powershell.exe -File launch.
. "$PSScriptRoot\windows-common.ps1"

$forwardedArguments = @($args | Where-Object { -not [string]::IsNullOrEmpty($_) })
# Re-converts every source through Docling, then synchronizes mrag as usual.
# Review the plan with sync-dry-run.bat first.
[string[]]$arguments = @(
    "exec", "--user", "mrag",
    "mrag-batch",
    "python", "/app/batch/sync.py", "--force-reconvert"
) + $forwardedArguments
Invoke-ProjectCompose $arguments
