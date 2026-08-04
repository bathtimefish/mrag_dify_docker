. "$PSScriptRoot\windows-common.ps1"

Invoke-ProjectCompose "--profile", "tools", "run", "--rm", "-T", "mrag-doctor"
