. "$PSScriptRoot\windows-common.ps1"

$versions = Get-VersionValues
$vendorDirectory = Join-Path $script:RepositoryRoot "vendor"
$difyDirectory = Join-Path $vendorDirectory "dify"
$mragDirectory = Join-Path $vendorDirectory "mrag"

New-Item -ItemType Directory -Force -Path $vendorDirectory | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $difyDirectory ".git") -PathType Container)) {
    Invoke-NativeCommand "git" @(
        "-c", "core.longpaths=true",
        "clone",
        "--depth", "1",
        "--branch", $versions.DIFY_VERSION,
        "https://github.com/langgenius/dify.git",
        $difyDirectory
    )
}
$difyCommit = (& git -C $difyDirectory rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the Dify checkout."
}
if ($difyCommit -ne $versions.DIFY_COMMIT) {
    throw "Unexpected Dify commit: $difyCommit (expected $($versions.DIFY_COMMIT))."
}

# mrag is a git submodule. Its commit lives in the gitlink rather than VERSIONS,
# so this only materializes the checkout the repository already pins.
Invoke-NativeCommand "git" @(
    "-c", "core.longpaths=true",
    "-C", $script:RepositoryRoot,
    "submodule", "update", "--init", "--", "vendor/mrag"
)

Write-Host "Official source checkouts are ready."
