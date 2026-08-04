. "$PSScriptRoot\windows-common.ps1"

Invoke-ProjectPython `
    (Join-Path $script:RepositoryRoot "scripts\generate-env.py") `
    (Join-Path $script:RepositoryRoot ".env.example") `
    (Join-Path $script:RepositoryRoot ".env")
