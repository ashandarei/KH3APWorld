# Read individual developer settings as literals; never execute .env contents.
function Get-Kh3LocalSetting {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,
        [string] $Default = '',
        [string] $SettingsRoot = (Split-Path $PSScriptRoot -Parent)
    )

    $ProcessValue = [Environment]::GetEnvironmentVariable($Name)
    if ($null -ne $ProcessValue) {
        if ($ProcessValue.Trim()) { return $ProcessValue.Trim() }
        return $Default
    }
    $ConfiguredFile = $env:AP_ENV_FILE
    $EnvFile = if ($ConfiguredFile) { $ConfiguredFile } else { Join-Path $SettingsRoot '.env' }
    if (-not [IO.Path]::IsPathRooted($EnvFile)) { $EnvFile = Join-Path $SettingsRoot $EnvFile }
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        if ($ConfiguredFile) { throw "Environment file does not exist: $EnvFile" }
        return $Default
    }
    $Value = $Default
    $Pattern = '^\s*(?:export\s+)?' + [regex]::Escape($Name) + '\s*=\s*(.*)$'
    foreach ($Line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
        $SettingMatch = [regex]::Match($Line, $Pattern)
        if (-not $SettingMatch.Success) { continue }
        $Value = $SettingMatch.Groups[1].Value.Trim()
        if ($Value.StartsWith('"') -or $Value.StartsWith("'")) {
            $QuotedValue = [regex]::Match($Value, '^([''"])(.*?)\1\s*(?:#.*)?$')
            if (-not $QuotedValue.Success) { throw "Invalid single-line value for $Name in $EnvFile" }
            $Value = $QuotedValue.Groups[2].Value
        } else {
            $Value = if ($Value.StartsWith('#')) { '' } else { ($Value -split '\s+#', 2)[0].TrimEnd() }
        }
        if (-not $Value) { $Value = $Default }
    }
    return $Value
}

function Resolve-Kh3LocalPath {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [string] $Value = '',
        [string] $Default = '',
        [string] $SettingsRoot = (Split-Path $PSScriptRoot -Parent)
    )
    if ([string]::IsNullOrWhiteSpace($Value)) {
        $Value = Get-Kh3LocalSetting -Name $Name -Default $Default -SettingsRoot $SettingsRoot
    }
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Configure $Name in the environment/.env or pass its path argument."
    }
    if (-not [IO.Path]::IsPathRooted($Value)) { $Value = Join-Path $SettingsRoot $Value }
    return [IO.Path]::GetFullPath($Value)
}
