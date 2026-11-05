[CmdletBinding()]
param(
    [string] $Python = 'python',
    [switch] $NativeIntegration,
    [string[]] $PytestArgs = @('worlds/kh3/test', 'tools/kh3/tests', '-m', 'world or not world', '-q')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'kh3-local-environment.ps1')
$ConfiguredRuntime = Get-Kh3LocalSetting -Name 'KH3_RUNTIME_DIR' -SettingsRoot $ProjectRoot
$RuntimeRoot = if ($ConfiguredRuntime) { $ConfiguredRuntime } else { Join-Path (Split-Path $ProjectRoot -Parent) 'KH3Runtime' }
if (-not [IO.Path]::IsPathRooted($RuntimeRoot)) { $RuntimeRoot = Join-Path $ProjectRoot $RuntimeRoot }
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
$Settings = @{
    AP_TEST_WORLDS = 'kh3'
    SKIP_REQUIREMENTS_UPDATE = '1'
    KH3_RUNTIME_DIR = $RuntimeRoot
    KH3_NATIVE_INTEGRATION = $(if ($NativeIntegration -or $PytestArgs -contains '--native-integration') { '1' } else { '0' })
}
$Previous = @{}
foreach ($Name in $Settings.Keys) {
    $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name)
    [Environment]::SetEnvironmentVariable($Name, $Settings[$Name])
}
# The scoped loader otherwise defaults to world-only tests, which would silently
# deselect an explicit tools test path. Preserve any caller-supplied marker.
if (-not ($PytestArgs -match '^(?:-m|--markexpr(?:=|$))')) {
    $PytestArgs += @('-m', 'world or not world')
}
Push-Location $ProjectRoot
try {
    & $Python -m pytest -p tools.kh3.pytest_plugin @PytestArgs
    if ($LASTEXITCODE -ne 0) { throw "KH3 tests failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
    foreach ($Name in $Previous.Keys) { [Environment]::SetEnvironmentVariable($Name, $Previous[$Name]) }
}
