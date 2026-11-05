[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int] $GameProcessId,
    [Parameter(Mandatory = $true)]
    [string] $ExpectedExecutablePath,
    [Parameter(Mandatory = $true)]
    [string] $OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

# Read a stopped-at-error process from outside it. No DebugActiveProcess,
# injected DLL, breakpoint, exception handler, game launch, or save-file access.
$GameProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$GameProcessId"
if (-not $GameProcess -or $GameProcess.Name -ne 'KINGDOM HEARTS III.exe') {
    throw "KH3 process $GameProcessId is no longer present."
}
$ExpectedPath = [System.IO.Path]::GetFullPath($ExpectedExecutablePath)
if (-not [string]::Equals($GameProcess.ExecutablePath, $ExpectedPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Process executable does not match the requested KH3 installation."
}

if (-not ('Kh3PassiveDump' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class Kh3PassiveDump
{
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inherit, uint id);
    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool QueryFullProcessImageName(IntPtr process, uint flags,
        System.Text.StringBuilder name, ref uint size);
    [DllImport("dbghelp.dll", SetLastError = true)]
    private static extern bool MiniDumpWriteDump(IntPtr process, uint id,
        SafeFileHandle file, uint type, IntPtr exception, IntPtr user, IntPtr callback);

    public static void Capture(uint id, string expectedPath, string destination)
    {
        IntPtr process = OpenProcess(0x0400 | 0x0010, false, id);
        if (process == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            // Verify the actual opened process, too: the original PID may have exited/reused.
            var image = new System.Text.StringBuilder(32768);
            uint length = (uint)image.Capacity;
            if (!QueryFullProcessImageName(process, 0, image, ref length))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if (!String.Equals(Path.GetFullPath(image.ToString()), expectedPath,
                StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Opened process does not match the selected KH3 installation.");
            using (var file = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                // Normal stack/module capture, unloaded modules, indirectly
                // referenced memory, memory-region information, thread info.
                if (!MiniDumpWriteDump(process, id, file.SafeFileHandle, 0x1860,
                    IntPtr.Zero, IntPtr.Zero, IntPtr.Zero))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }
        finally { CloseHandle(process); }
    }
}
'@
}

$DestinationDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $DestinationDirectory -Force | Out-Null
$Destination = Join-Path $DestinationDirectory (
    'kh3-passive-{0}-{1}.dmp' -f $GameProcessId, (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss-fff'))
[Kh3PassiveDump]::Capture([uint32]$GameProcessId, $ExpectedPath, $Destination)
$DumpFile = Get-Item -LiteralPath $Destination
[pscustomobject]@{
    path = $DumpFile.FullName
    size = $DumpFile.Length
    pid = $GameProcessId
    executable = $ExpectedPath
    captured_at_utc = $DumpFile.LastWriteTimeUtc.ToString('o')
    method = 'passive_minidump'
    note = 'Live process snapshot; no exception context is supplied by the capture helper.'
} | ConvertTo-Json -Compress
