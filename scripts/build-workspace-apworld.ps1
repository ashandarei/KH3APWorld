<#
.SYNOPSIS
Build the KH3 APWorld using its pinned native release.
.DESCRIPTION
Run from any directory. Defaults to this APWorld checkout and writes
archives under build/apworlds. Verifies the published native release without
building or bundling a DLL. Does not install packages, modify the game, or
publish releases. -HostOnly packages generation support without downloads.
Project paths may be overridden by parameters or KH3_APWORLD_PROJECT and
KH3_RUNTIME_DIR in the environment or .env. Relative project paths resolve
against this checkout. Output paths resolve against the parent workspace.
#>
[CmdletBinding()]
param(
    [string] $APWorldRoot = '',
    [string] $RuntimeRoot = '',
    [string] $OutputDirectory = '',
    [string] $Python = 'python',
    [switch] $RunSmokeTests,
    [switch] $HostOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$CheckoutRoot = Split-Path $PSScriptRoot -Parent
$WorkspaceRoot = Split-Path $CheckoutRoot -Parent
. (Join-Path $PSScriptRoot 'kh3-local-environment.ps1')

function Resolve-WorkspacePath {
    param([string] $Value, [string] $Default)
    if (-not $Value) { $Value = $Default }
    if (-not [IO.Path]::IsPathRooted($Value)) { $Value = Join-Path $WorkspaceRoot $Value }
    return [IO.Path]::GetFullPath($Value)
}

$APWorldRoot = Resolve-Kh3LocalPath -Name 'KH3_APWORLD_PROJECT' -Value $APWorldRoot -Default $CheckoutRoot
$RuntimeRoot = Resolve-Kh3LocalPath -Name 'KH3_RUNTIME_DIR' -Value $RuntimeRoot -Default '../KH3Runtime'
$OutputDirectory = Resolve-WorkspacePath $OutputDirectory 'build/apworlds'
$ArchiveBuilder = Join-Path $APWorldRoot 'scripts/build-kh3-apworld.ps1'
if (-not (Test-Path -LiteralPath $ArchiveBuilder -PathType Leaf)) {
    throw "APWorld packaging script not found: $ArchiveBuilder"
}

$Settings = @{
    SKIP_REQUIREMENTS_UPDATE = '1'
    AP_WORLD_LOAD_PATHS = (ConvertTo-Json -InputObject @((Join-Path $APWorldRoot 'worlds/kh3')) -Compress)
    AP_TEST_WORLDS = 'kh3'
    KH3_RUNTIME_DIR = $RuntimeRoot
}
$Previous = @{}
foreach ($Name in $Settings.Keys) {
    $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name)
    [Environment]::SetEnvironmentVariable($Name, $Settings[$Name])
}
try {
    & $ArchiveBuilder -BuildDir $OutputDirectory -Python $Python -HostOnly:$HostOnly -RunSmokeTests:$RunSmokeTests
}
finally {
    foreach ($Name in $Previous.Keys) { [Environment]::SetEnvironmentVariable($Name, $Previous[$Name]) }
}
