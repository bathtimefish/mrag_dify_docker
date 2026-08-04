. "$PSScriptRoot\windows-common.ps1"

Invoke-ProjectCompose `
    "exec", "-T", "--user", "10001:10001", `
    "mrag-batch", "sh", "-c", "cd /data/mrag && exec mrag index"
