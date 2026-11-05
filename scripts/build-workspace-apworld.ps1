<#
.SYNOPSIS
Build the KH3 player DLL, synchronize its release inputs, and build the APWorld.
.DESCRIPTION
Run from any directory. Defaults to the two adjacent repositories and writes
archives under build/apworlds. Does not install packages, modify the game, or
publish releases. -HostOnly packages generation support without building a DLL.
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

function Invoke-CheckedPython {
    param([string[]] $Arguments)
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE" }
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
    if (-not $HostOnly) {
        $NativeBuilder = Join-Path $RuntimeRoot 'scripts/build.ps1'
        if (-not (Test-Path -LiteralPath $NativeBuilder -PathType Leaf)) {
            throw "Native build script not found: $NativeBuilder"
        }
        Invoke-CheckedPython -Arguments @((Join-Path $APWorldRoot 'tools/kh3/export_dll_inputs.py'), '--check')
        Write-Host 'Building the player DLL with diagnostics and automation disabled.'
        & $NativeBuilder

        $Dll = Join-Path $RuntimeRoot 'build/KH3APProbeCpp/main.dll'
        $Metadata = "$Dll.build.json"
        $Info = Get-Content -LiteralPath $Metadata -Raw | ConvertFrom-Json
        $DllHash = (Get-FileHash -LiteralPath $Dll -Algorithm SHA256).Hash.ToLowerInvariant()
        $InputsHash = (Get-FileHash -LiteralPath (Join-Path $RuntimeRoot 'data/runtime-inputs.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Info.schema -ne 3 -or $Info.performance_diagnostics -isnot [bool] -or
            $Info.automation_handlers -isnot [bool] -or $Info.performance_diagnostics -or
            $Info.automation_handlers -or $Info.first_party_license -ne 'MIT' -or
            $Info.sha256 -ne $DllHash -or $Info.runtime_inputs_sha256 -ne $InputsHash) {
            throw 'APWorld packaging requires a hash-verified MIT player DLL built from the current runtime inputs.'
        }
        $PinMatch = [regex]::Match((Get-Content -LiteralPath (Join-Path $RuntimeRoot 'CMakeLists.txt') -Raw),
            'set\(KH3AP_PINNED_UE4SS_COMMIT "([0-9a-f]{40})"\)')
        if (-not $PinMatch.Success -or $Info.ue4ss_revision -cne $PinMatch.Groups[1].Value -or
            $Info.ue4ss_dirty -isnot [bool] -or $Info.ue4ss_dirty -or
            $Info.allow_unpinned_ue4ss -isnot [bool] -or $Info.allow_unpinned_ue4ss -or
            $Info.release_eligible -isnot [bool] -or -not $Info.release_eligible) {
            throw 'APWorld packaging requires a clean reviewed UE4SS build without research overrides.'
        }

        $ModRoot = Join-Path $APWorldRoot 'worlds/kh3/runtime/Mods/KH3APProbeCpp'
        $TargetDll = Join-Path $ModRoot 'dlls/main.dll'
        $BackupRoot = Join-Path $WorkspaceRoot ('.local/package-input-backups/' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'))
        $ReleaseInputs = [ordered]@{ $TargetDll = $Dll; "$TargetDll.build.json" = $Metadata }
        foreach ($Existing in $ReleaseInputs.Keys) {
            if ((Test-Path -LiteralPath $Existing -PathType Leaf) -and
                (Get-FileHash -LiteralPath $Existing -Algorithm SHA256).Hash -ne
                (Get-FileHash -LiteralPath $ReleaseInputs[$Existing] -Algorithm SHA256).Hash) {
                New-Item -ItemType Directory -Path $BackupRoot -Force > $null
                Copy-Item -LiteralPath $Existing -Destination $BackupRoot
            }
        }
        New-Item -ItemType Directory -Path (Split-Path $TargetDll -Parent) -Force > $null
        Copy-Item -LiteralPath $Dll -Destination $TargetDll -Force
        Copy-Item -LiteralPath $Metadata -Destination "$TargetDll.build.json" -Force
        foreach ($Notice in @('LICENSE', 'THIRD_PARTY_NOTICES.md')) {
            Copy-Item -LiteralPath (Join-Path $RuntimeRoot $Notice) -Destination (Join-Path $ModRoot $Notice) -Force
        }
        $LicenseRoot = Join-Path $RuntimeRoot 'licenses'
        foreach ($File in Get-ChildItem -LiteralPath $LicenseRoot -Recurse -File) {
            $Relative = $File.FullName.Substring($LicenseRoot.Length).TrimStart('\', '/')
            $Destination = Join-Path $ModRoot "licenses/$Relative"
            New-Item -ItemType Directory -Path (Split-Path $Destination -Parent) -Force > $null
            Copy-Item -LiteralPath $File.FullName -Destination $Destination -Force
        }
        if ((Get-FileHash -LiteralPath $TargetDll -Algorithm SHA256).Hash.ToLowerInvariant() -ne $DllHash) {
            throw 'The synchronized APWorld DLL does not match the native player build.'
        }
        Write-Host "Verified player DLL synchronized: $DllHash"
    }

    & $ArchiveBuilder -BuildDir $OutputDirectory -Python $Python -HostOnly:$HostOnly -RunSmokeTests:$RunSmokeTests
}
finally {
    foreach ($Name in $Previous.Keys) { [Environment]::SetEnvironmentVariable($Name, $Previous[$Name]) }
}
