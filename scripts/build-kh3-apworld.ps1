[CmdletBinding()]
param(
    [string] $WorldName = "Kingdom Hearts III",
    [string] $OutputName = "",
    [string] $BuildDir = "",
    [string] $Python = "python",
    [switch] $RunSmokeTests,
    [switch] $HostOnly,
    [switch] $KeepBaseArtifact = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$WorldDir = Join-Path $RepoRoot "worlds\kh3"
$ManifestPath = Join-Path $WorldDir "archipelago.json"
$ApignorePath = Join-Path $WorldDir ".apignore"
if (-not $BuildDir) { $BuildDir = Join-Path $RepoRoot "build\apworlds" }
if (-not [IO.Path]::IsPathRooted($BuildDir)) { $BuildDir = Join-Path $RepoRoot $BuildDir }
$BuildDir = [IO.Path]::GetFullPath($BuildDir)
$BaseArtifact = Join-Path $BuildDir "kh3.apworld"
$HostDownloadArtifact = Join-Path $BuildDir "kh3-host.apworld"
$LicenseDir = Join-Path $WorldDir "runtime\Win64\KH3AP-Licenses"
$LicenseManifestPath = Join-Path $LicenseDir "manifest.json"
if (-not $HostOnly) {
    $CompanionConfigPath = Join-Path $WorldDir 'runtime/KH02/02-goa-release.json'
    $CompanionConfig = Get-Content -LiteralPath $CompanionConfigPath -Raw | ConvertFrom-Json
    if ($CompanionConfig.source -ne 'release' -or $CompanionConfig.asset_protocol -ne 3) {
        throw 'Player packaging requires a published KH0.2 companion with asset protocol 3. The current companion is a local development build.'
    }
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,
        [string[]] $CommandArguments = @()
    )

    Push-Location $RepoRoot
    try {
        & $FilePath @CommandArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($CommandArguments -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Write-ArchiveChecksum {
    param([Parameter(Mandatory = $true)][string] $ArtifactPath)
    $ArchiveHash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$ArtifactPath.sha256", "$ArchiveHash  $([IO.Path]::GetFileName($ArtifactPath))`n",
        [Text.UTF8Encoding]::new($false))
}

function Assert-ArchiveNotices {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ArtifactPath,
        [switch] $PlayerPackage
    )

    $RequiredNotices = @(
        "LICENSE",
        "COPYRIGHT.txt",
        "LICENSE-Archipelago.txt",
        "LICENSE-KH3RandomizerClient.txt",
        "THIRD_PARTY_NOTICES.txt",
        "UPSTREAM-KH3RandomizerClient-CREDITS.txt"
    )
    if ($PlayerPackage) {
        $RequiredNotices += @(
            "seedgen/THIRD_PARTY_NOTICES.txt",
            "runtime/THIRD_PARTY_NOTICES.md",
            "runtime/KH02/02-GoA-Licenses/LICENSE",
            "runtime/KH02/02-GoA-Licenses/THIRD_PARTY_NOTICES.md",
            "runtime/KH02/02-goa-release.json",
            "runtime/KH02/host-manifest.json",
            "kh02/host_runtime.py",
            "runtime/Mods/KH3APProbeCpp/LICENSE",
            "runtime/Mods/KH3APProbeCpp/THIRD_PARTY_NOTICES.md",
            "runtime/Mods/KH3APProbeCpp/runtime-release.json"
        )
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $NoticeZip = [System.IO.Compression.ZipFile]::OpenRead($ArtifactPath)
    try {
        foreach ($Notice in $RequiredNotices) {
            $Entry = $NoticeZip.GetEntry("kh3/$Notice")
            if ($null -eq $Entry -or $Entry.Length -eq 0) {
                throw "Built APWorld is missing a required third-party notice: $Notice"
            }
        }
        if ($PlayerPackage) {
            if ($null -ne $NoticeZip.GetEntry("kh3/runtime/KH02/KH02_CastleTownHost_P.pak")) {
                throw "Built APWorld bundles the host pak instead of using its pinned release."
            }
            # Host-only packages do not distribute the runtime or its notices.
            $ExpectedLicenses = @($LicenseManifest.files.PSObject.Properties | ForEach-Object {
                @{ Name = $_.Name; Hash = $_.Value }
            }) + @(@{ Name = "manifest.json"; Hash = (Get-FileHash -LiteralPath $LicenseManifestPath -Algorithm SHA256).Hash })
            foreach ($ExpectedLicense in $ExpectedLicenses) {
                $EntryName = "kh3/runtime/Win64/KH3AP-Licenses/$($ExpectedLicense.Name)"
                $LicenseEntry = $NoticeZip.GetEntry($EntryName)
                if ($null -eq $LicenseEntry) {
                    throw "Built APWorld is missing a frozen runtime notice: $EntryName"
                }
                $NoticeStream = $LicenseEntry.Open()
                $Hasher = [System.Security.Cryptography.SHA256]::Create()
                try {
                    $NoticeHash = [System.BitConverter]::ToString($Hasher.ComputeHash($NoticeStream)).Replace("-", "")
                    if ($NoticeHash -ne $ExpectedLicense.Hash) {
                        throw "Built APWorld runtime notice differs from its manifest: $EntryName"
                    }
                }
                finally {
                    $NoticeStream.Dispose()
                    $Hasher.Dispose()
                }
            }
        }
    }
    finally {
        $NoticeZip.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $ManifestPath)) {
    throw "Missing KH3 APWorld manifest: $ManifestPath"
}

if (-not (Test-Path -LiteralPath $ApignorePath)) {
    throw "Missing KH3 .apignore: $ApignorePath"
}

if (-not $HostOnly) {
    if (-not (Test-Path -LiteralPath $LicenseManifestPath)) {
        throw "Missing frozen UE4SS runtime license manifest: $LicenseManifestPath"
    }
    $LicenseManifest = Get-Content -LiteralPath $LicenseManifestPath -Raw | ConvertFrom-Json
    if ($LicenseManifest.schema -ne 1 -or
        $null -eq $LicenseManifest.files.PSObject.Properties["THIRD_PARTY_NOTICES.txt"]) {
        throw "Invalid frozen UE4SS runtime license manifest."
    }
    foreach ($LicenseFile in $LicenseManifest.files.PSObject.Properties) {
        $SourceNotice = Join-Path $LicenseDir $LicenseFile.Name
        if (-not (Test-Path -LiteralPath $SourceNotice -PathType Leaf) -or
            (Get-FileHash -LiteralPath $SourceNotice -Algorithm SHA256).Hash -ne $LicenseFile.Value) {
            throw "Runtime license file is missing or differs from its manifest: $($LicenseFile.Name)"
        }
    }
    $SourceLicenses = @(Get-ChildItem -LiteralPath $LicenseDir -Recurse -File)
    if ($SourceLicenses.Count -ne @($LicenseManifest.files.PSObject.Properties).Count + 1) {
        throw "Runtime license folder contains files outside its manifest. Remove obsolete notices before packaging."
    }
    Invoke-CheckedCommand -FilePath $Python -CommandArguments @(
        "-c",
        "from pathlib import Path; from worlds.kh3.kh02.host_runtime import host_manifest; root=Path('worlds/kh3'); resource=lambda *parts: root.joinpath(*parts); host_manifest(resource); print('Verified pinned KH0.2 host map configuration.')"
    )
}

$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($Manifest.license -cne 'GPL-3.0-only') {
    throw 'The KH3 APWorld manifest must declare GPL-3.0-only; native runtimes retain their separate licenses.'
}
$Version = [string] $Manifest.world_version
if ([string]::IsNullOrWhiteSpace($Version)) {
    throw "worlds\kh3\archipelago.json does not define world_version."
}

if ([string]::IsNullOrWhiteSpace($OutputName)) {
    # Archipelago imports the module matching the archive's filename. Keep
    # kh3.apworld as the filename and put the release version in its directory.
    $OutputName = "kh3-$Version\kh3.apworld"
}
elseif (-not $OutputName.EndsWith(".apworld", [System.StringComparison]::OrdinalIgnoreCase)) {
    $OutputName = "$OutputName.apworld"
}

if ([System.IO.Path]::GetFileName($OutputName) -cne "kh3.apworld") {
    throw "KH3 archives must be named kh3.apworld to load in Archipelago. Put the version in a parent directory, for example kh3-$Version\kh3.apworld."
}

$RequiredIgnores = @(
    "/randomizer/Seeds",
    "/randomizer/wwwroot/pak/Seeds",
    "/randomizer/wwwroot/pak/UnrealPak.exe",
    "/tools/repak",
    "/runtime/Mods/KH3APProbeCpp/dlls/main.dll*"
)

$ApignoreText = Get-Content -LiteralPath $ApignorePath -Raw
foreach ($RequiredIgnore in $RequiredIgnores) {
    if ($ApignoreText -notmatch [regex]::Escape($RequiredIgnore)) {
        throw "KH3 .apignore is missing required packaging exclusion: $RequiredIgnore"
    }
}

$TargetArtifact = Join-Path $BuildDir $OutputName
$HostArtifact = Join-Path (Split-Path -Parent $TargetArtifact) "host\kh3.apworld"
if ([System.IO.Path]::GetFullPath($TargetArtifact) -eq [System.IO.Path]::GetFullPath($BaseArtifact)) {
    throw "OutputName must include a release subdirectory, for example kh3-$Version\kh3.apworld."
}

Push-Location $RepoRoot
try {
    if ($HostOnly) {
        Invoke-CheckedCommand -FilePath $Python -CommandArguments @(
            "-c",
            "import sys; from pathlib import Path; from scripts.kh3_apworld_archive import build_world_apworld; build_world_apworld(Path('worlds/kh3'), 'Kingdom Hearts III', Path(sys.argv[1]), variant='host')",
            $HostArtifact
        )
        Assert-ArchiveNotices -ArtifactPath $HostArtifact
        if ($RunSmokeTests) {
            Invoke-CheckedCommand -FilePath $Python -CommandArguments @(
                "-m", "pytest", "test\programs\test_kh3_host_apworld.py", "-q"
            )
        }
        if ($KeepBaseArtifact) {
            Copy-Item -LiteralPath $HostArtifact -Destination $HostDownloadArtifact -Force
        }
        Write-Host "Built host APWorld $HostArtifact"
        Write-Host "Version: $Version"
        Write-Host "Size: $((Get-Item -LiteralPath $HostArtifact).Length) bytes"
        Write-Host "SHA256: $((Get-FileHash -LiteralPath $HostArtifact -Algorithm SHA256).Hash)"
        Write-ArchiveChecksum -ArtifactPath $HostArtifact
        return
    }
    # Verify the exact published player DLL selected by the packaged release pin.
    Invoke-CheckedCommand -FilePath $Python -CommandArguments @(
        "-c",
        "import json; from pathlib import Path; from worlds.kh3.installer_support.resources import _runtime_probe_source, _decode_probe_dll_text, _find_probe_dll_version; source=_runtime_probe_source(); expected=json.loads(Path('worlds/kh3/archipelago.json').read_text())['world_version']; assert source.config['version'] == expected; actual=_find_probe_dll_version(_decode_probe_dll_text(source.read_bytes())); assert actual == expected, f'Released probe ModVersion {actual} does not match APWorld {expected}'; print('Verified published KH3Runtime release', expected)"
    )
    if ($RunSmokeTests) {
        Invoke-CheckedCommand -FilePath $Python -CommandArguments @(
            "-m",
            "pytest",
            "worlds\kh3\test\test_world_generation.py::TestKH3WorldIntegrity::test_bridge_world_version_matches_manifest",
            "worlds\kh3\test\test_client_config.py::TestKH3ItemConsumerPlan::test_runtime_installer_reads_runtime_resources_from_apworld_without_zipfile_path",
            "worlds\kh3\test\test_runtime_download.py::TestRuntimeDownload::test_apworld_without_dlls_downloads_and_backs_up_then_skips",
            "worlds\kh3\test\test_probe_release.py",
            "worlds\kh3\test\test_pak.py",
            "worlds\kh3\test\test_python_seedgen.py",
            "worlds\kh3\test\test_pak_generation_preflight.py",
            "test\programs\test_kh3_host_apworld.py",
            "-q"
        )
    }

    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $TargetArtifact) | Out-Null
    Remove-Item -LiteralPath $BaseArtifact -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $TargetArtifact -Force -ErrorAction SilentlyContinue

    Invoke-CheckedCommand -FilePath $Python -CommandArguments @(
        "-c",
        "import sys; from pathlib import Path; from scripts.kh3_apworld_archive import build_world_apworld; build_world_apworld(Path('worlds/kh3'), sys.argv[1], Path(sys.argv[2]))",
        $WorldName,
        $BaseArtifact
    )

    if (-not (Test-Path -LiteralPath $BaseArtifact)) {
        throw "APWorld build did not produce expected base artifact: $BaseArtifact"
    }

    Copy-Item -LiteralPath $BaseArtifact -Destination $TargetArtifact -Force
    Assert-ArchiveNotices -ArtifactPath $TargetArtifact -PlayerPackage
    if (-not $KeepBaseArtifact) {
        Remove-Item -LiteralPath $BaseArtifact -Force
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Zip = [System.IO.Compression.ZipFile]::OpenRead($TargetArtifact)
    try {
        $Entries = @($Zip.Entries)
        $SeedEntries = @($Entries | Where-Object {
            $_.FullName -like "kh3/randomizer/Seeds/*" -or
            $_.FullName -like "kh3/randomizer/wwwroot/pak/Seeds/*"
        })
        $BackupDllEntries = @($Entries | Where-Object {
            $_.FullName -like "kh3/runtime/Mods/KH3APProbeCpp/dlls/main.dll*"
        })
        $ManifestEntry = $Zip.GetEntry("kh3/archipelago.json")
        $DllEntry = $Zip.GetEntry("kh3/runtime/Mods/KH3APProbeCpp/dlls/main.dll")
        $LegacyUnrealPakEntry = $Zip.GetEntry("kh3/randomizer/wwwroot/pak/UnrealPak.exe")

        if ($SeedEntries.Count -ne 0) {
            throw "Built APWorld contains generated seed files. First entry: $($SeedEntries[0].FullName)"
        }
        if ($BackupDllEntries.Count -ne 0) {
            throw "Built APWorld contains bundled DLL files. First entry: $($BackupDllEntries[0].FullName)"
        }
        if ($null -eq $ManifestEntry) {
            throw "Built APWorld is missing kh3/archipelago.json."
        }
        if ($null -ne $DllEntry -or
            $null -eq $Zip.GetEntry("kh3/runtime/Mods/KH3APProbeCpp/runtime-release.json") -or
            $null -eq $Zip.GetEntry("kh3/installer_support/probe_release.py")) {
            throw "Built APWorld must contain the pinned KH3Runtime downloader/configuration without a DLL."
        }
        foreach ($DocumentationFile in @(
            "en_Kingdom Hearts III.md",
            "setup_en.md",
            "olympus_coliseum_mod.md"
        )) {
            if ($null -eq $Zip.GetEntry("kh3/docs/$DocumentationFile")) {
                throw "Built APWorld is missing player documentation: $DocumentationFile"
            }
        }
        foreach ($CaptureFile in @("passive_dump.py", "capture-kh3-passive-dump.ps1")) {
            if ($null -eq $Zip.GetEntry("kh3/client_support/$CaptureFile")) {
                throw "Built APWorld is missing crash capture support: $CaptureFile"
            }
        }
        foreach ($RemoteDll in @("UE4SS.dll", "dwmapi.dll", "xinput1_3.dll")) {
            if ($null -ne $Zip.GetEntry("kh3/runtime/Win64/$RemoteDll")) {
                throw "Built APWorld unexpectedly bundles downloaded runtime DLL: $RemoteDll"
            }
        }
        if (@($Entries | Where-Object { $_.FullName -like "kh3/runtime/KH02/main.dll*" }).Count -ne 0) {
            throw "Built APWorld unexpectedly bundles the downloaded 02-GoA companion DLL."
        }
        if (@($Entries | Where-Object { $_.FullName -match 'kh3/runtime/KH02/(asset-catalog-.*[.]json[.]zlib|catalog-manifest[.]json)$' }).Count -ne 0) {
            throw "Built APWorld contains obsolete KH0.2 source-byte catalogs."
        }
        $CompanionPin = $Zip.GetEntry("kh3/runtime/KH02/02-goa-release.json")
        $CompanionDownloader = $Zip.GetEntry("kh3/kh02/runtime_release.py")
        if ($null -eq $CompanionPin -or $null -eq $CompanionDownloader) {
            throw "Built APWorld is missing the pinned 02-GoA downloader/configuration."
        }
        $CompanionReader = New-Object System.IO.StreamReader($CompanionPin.Open())
        try {
            $CompanionConfig = $CompanionReader.ReadToEnd() | ConvertFrom-Json
            if ($CompanionConfig.source -ne 'release') {
                throw "Player APWorld must use the pinned 02-GoA release download."
            }
            if ($CompanionConfig.asset_protocol -ne 3) {
                throw "Player APWorld requires a released KH0.2 companion with asset protocol 3."
            }
        }
        finally {
            $CompanionReader.Dispose()
        }
        if (@($Entries | Where-Object { $_.FullName -match '/xinput1_3[.]dll($|[.])' }).Count -ne 0) {
            throw "Built APWorld unexpectedly bundles a loose Microsoft XInput DLL or backup."
        }
        if ($null -eq $Zip.GetEntry("kh3/runtime/Win64/ue4ss-release.json") -or
            $null -eq $Zip.GetEntry("kh3/RuntimeDownload.py") -or
            $null -eq $Zip.GetEntry("kh3/platform_support/xinput_runtime.py")) {
            throw "Built APWorld is missing the UE4SS downloader/configuration or XInput prerequisite installer."
        }
        if ($null -ne $LegacyUnrealPakEntry) {
            throw "Built APWorld still contains the legacy DirectX-dependent UnrealPak.exe."
        }
        if (@($Entries | Where-Object { $_.FullName -like "kh3/tools/repak/*" }).Count -ne 0) {
            throw "Built APWorld unexpectedly bundles native repak tools."
        }
        foreach ($PythonPakFile in @("Pak.py", "seedgen/__init__.py", "seedgen/__main__.py", "seedgen/assets.py", "seedgen/rng.py",
                "seedgen/tables.py", "seedgen/remaps.py", "seedgen/localization.py", "seedgen/presentation.py",
                "seedgen/generator.py", "seedgen/coliseum.py", "seedgen/THIRD_PARTY_NOTICES.txt")) {
            if ($null -eq $Zip.GetEntry("kh3/$PythonPakFile")) {
                throw "Built APWorld is missing Python pak support: $PythonPakFile"
            }
        }
        if (@($Entries | Where-Object { $_.FullName -like "kh3/randomizer/*.cs" -or
                $_.FullName -like "kh3/randomizer/*.csproj" }).Count -ne 0) {
            throw "Built APWorld unexpectedly bundles the retired C# randomizer."
        }

        $Reader = New-Object System.IO.StreamReader($ManifestEntry.Open())
        try {
            $BuiltManifest = $Reader.ReadToEnd() | ConvertFrom-Json
        }
        finally {
            $Reader.Dispose()
        }

        if ([string] $BuiltManifest.world_version -ne $Version) {
            throw "Built APWorld version mismatch. Expected $Version, got $($BuiltManifest.world_version)."
        }
    }
    finally {
        $Zip.Dispose()
    }

    $Hash = Get-FileHash -LiteralPath $TargetArtifact -Algorithm SHA256
    $Size = (Get-Item -LiteralPath $TargetArtifact).Length

    Write-Host "Built $TargetArtifact"
    Write-Host "Version: $Version"
    Write-Host "Size: $Size bytes"
    Write-Host "SHA256: $($Hash.Hash)"
    Write-ArchiveChecksum -ArtifactPath $TargetArtifact
    Invoke-CheckedCommand -FilePath $Python -CommandArguments @(
        "-c",
        "import sys; from pathlib import Path; from scripts.kh3_apworld_archive import build_world_apworld; build_world_apworld(Path('worlds/kh3'), 'Kingdom Hearts III', Path(sys.argv[1]), variant='host')",
        $HostArtifact
    )
    Assert-ArchiveNotices -ArtifactPath $HostArtifact
    if ($KeepBaseArtifact) {
        Copy-Item -LiteralPath $HostArtifact -Destination $HostDownloadArtifact -Force
    }
    Write-Host "Built host APWorld $HostArtifact"
    Write-Host "Size: $((Get-Item -LiteralPath $HostArtifact).Length) bytes"
    Write-Host "SHA256: $((Get-FileHash -LiteralPath $HostArtifact -Algorithm SHA256).Hash)"
    Write-ArchiveChecksum -ArtifactPath $HostArtifact
}
finally {
    Pop-Location
}
