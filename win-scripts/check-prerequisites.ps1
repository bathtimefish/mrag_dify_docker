. "$PSScriptRoot\windows-common.ps1"

foreach ($command in @("docker", "git")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command was not found in PATH."
    }
}
$python = Get-PythonInvocation

Invoke-NativeCommand "docker" @("version")
Invoke-NativeCommand "docker" @("compose", "version")
Invoke-NativeCommand "git" @("--version")
Invoke-NativeCommand $python.Command ($python.Prefix + @("--version"))

$containerOs = (& docker info --format "{{.OSType}}").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not reachable."
}
if ($containerOs -ne "linux") {
    throw "Docker Desktop must be switched to Linux containers (reported: $containerOs)."
}
Invoke-NativeCommand "docker" @("ps")

Write-Host "Windows prerequisites are ready (Docker Desktop Linux containers)."
