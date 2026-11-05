# Third-Party Notices

The complete notices for the frozen `v3.0.4` UE4SS runtime and its embedded
dependencies and fonts are in `Win64/KH3AP-Licenses/`. Patch Game copies that
folder to `Binaries/Win64/KH3AP-Licenses/` from the APWorld without downloading
licenses. Its `manifest.json` identifies the frozen DLLs and records the
license-file hashes and source provenance. The same folder is available as
`KH3AP-Licenses-v3.0.4.zip` on the ue4ss-goa release.

Microsoft XInput 1.3 is a Windows prerequisite, not a UE4SS proxy, and is not
covered by the RE-UE4SS MIT license. This APWorld neither includes nor downloads
the loose Microsoft DLL from the UE4SS release. On Windows, Patch Game obtains
the official complete DirectX redistributable from Microsoft when needed and
runs DXSETUP with its license acceptance and administrator prompts. On Linux,
Patch Game uses Wine/Proton's built-in XInput and no Microsoft installer.
The frozen v3.0.4 manifest still records the former release's XInput hash as
historical provenance; it does not grant Microsoft redistribution rights.

Copyright Microsoft Corporation. All rights reserved.
Official runtime distribution: https://www.microsoft.com/en-us/download/details.aspx?id=8109
DirectX setup guidance: https://learn.microsoft.com/en-us/windows/win32/dxtecharts/directx-setup-for-game-developers

The consolidated file contains the required notices for binary distribution.
GitHub source repositories retain their original source licenses and headers;
those can require notices that are unnecessary with the binary release.

KH3APProbeCpp uses JSON for Modern C++ for its JSON parsing and serialization:

- Project: https://github.com/nlohmann/json
- Version: 3.11.3 (commit 9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03)
- License: MIT

MIT License

Copyright (c) 2013-2022 Niels Lohmann

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
