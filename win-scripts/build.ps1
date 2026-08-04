. "$PSScriptRoot\windows-common.ps1"

$versions = Get-VersionValues
$mragSource = Join-Path $script:RepositoryRoot "vendor\mrag"
# vendor\mrag is a submodule, so its .git is a gitlink *file*, not a directory.
if (-not (Test-Path -LiteralPath (Join-Path $mragSource ".git"))) {
    throw "Missing vendor\mrag. Run win-scripts\bootstrap-vendors.bat first."
}

# The submodule gitlink is the authoritative pin; VERSIONS restates it so that a
# gitlink bumped without updating the image tags fails here instead of shipping.
$recordedCommit = (& git -C $script:RepositoryRoot rev-parse ":vendor/mrag").Trim()
if ($LASTEXITCODE -eq 0 -and $recordedCommit -ne $versions.MRAG_COMMIT) {
    throw "vendor/mrag gitlink is $recordedCommit but VERSIONS says $($versions.MRAG_COMMIT)."
}

$actualCommit = (& git -C $mragSource rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the mrag checkout."
}
if ($actualCommit -ne $versions.MRAG_COMMIT) {
    throw "Unexpected mrag commit: $actualCommit (expected $($versions.MRAG_COMMIT))."
}

# NOTE: Use single-quoted Python string literals only. Windows PowerShell 5.1
# strips embedded double quotes when passing an argument to a native command,
# which would corrupt this script (e.g. encoding="utf-8" -> encoding=utf-8).
$versionScript = @'
import pathlib, sys, tomllib
print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))['project']['version'])
'@
$python = Get-PythonInvocation
$actualVersion = (& $python.Command @($python.Prefix) -c $versionScript `
    (Join-Path $mragSource "pyproject.toml")).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the mrag version."
}
if ($actualVersion -ne $versions.MRAG_VERSION) {
    throw "Unexpected mrag version: $actualVersion (expected $($versions.MRAG_VERSION))."
}

$runtimeImage = "local/mrag-runtime:$($versions.MRAG_VERSION)"
$batchImage = "local/mrag-batch:$($versions.MRAG_VERSION)"

Write-Host "Building $runtimeImage from mrag $actualVersion ($actualCommit)"
Invoke-NativeCommand "docker" @(
    "build",
    "--file", (Join-Path $script:RepositoryRoot "mrag\Dockerfile"),
    "--build-arg", "MRAG_SOURCE_COMMIT=$($versions.MRAG_COMMIT)",
    "--build-arg", "PROJECT_VERSION=$($versions.PROJECT_VERSION)",
    "--build-arg", "PYTORCH_VERSION=$($versions.PYTORCH_VERSION)",
    "--build-arg", "SQLITE_VAPORETTO_VERSION=$($versions.SQLITE_VAPORETTO_VERSION)",
    "--build-arg", "SQLITE_VAPORETTO_AMD64_SHA256=$($versions.SQLITE_VAPORETTO_AMD64_SHA256)",
    "--build-arg", "SQLITE_VAPORETTO_ARM64_SHA256=$($versions.SQLITE_VAPORETTO_ARM64_SHA256)",
    "--tag", $runtimeImage,
    $script:RepositoryRoot
)

Write-Host "Building $batchImage from $runtimeImage"
Invoke-NativeCommand "docker" @(
    "build",
    "--file", (Join-Path $script:RepositoryRoot "batch\Dockerfile"),
    "--build-arg", "MRAG_RUNTIME_IMAGE=$runtimeImage",
    "--build-arg", "MRAG_SOURCE_COMMIT=$($versions.MRAG_COMMIT)",
    "--build-arg", "PROJECT_VERSION=$($versions.PROJECT_VERSION)",
    "--tag", $batchImage,
    $script:RepositoryRoot
)

Invoke-NativeCommand "docker" @("image", "inspect", $runtimeImage, $batchImage)
Write-Host "Custom images are ready."
