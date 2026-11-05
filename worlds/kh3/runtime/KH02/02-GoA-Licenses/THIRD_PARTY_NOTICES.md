# Third-party notices

The original project code is licensed under the [MIT License](LICENSE). Third-party components retain their own licenses and notices. No game assets, game executables, or UE4SS loader binaries are included.

The DLL uses UE4SS headers/imports and static utility libraries, nlohmann JSON and PolyHook2. The UE4SS target also exposes transitive native and Rust libraries to the linker. The notices cover those link inputs even when release optimization discards unused code. Corrosion is a build dependency. Dependency licenses are reproduced verbatim below; they retain their own terms.

| Component | Reviewed source revision | License text |
| --- | --- | --- |
| UE4SS | [06dd053d3e537ec2b477ee76337661b15642d012](https://github.com/ashandarei/ue4ss-goa/tree/06dd053d3e537ec2b477ee76337661b15642d012) | [License](licenses/UE4SS.txt) |
| PolyHook2 | [fd2a88f09c8ae89440858fc52573656141013c7f](https://github.com/stevemk14ebr/PolyHook_2_0/tree/fd2a88f09c8ae89440858fc52573656141013c7f) | [License](licenses/PolyHook2.txt) |
| AsmJit | [3577608cab0bc509f856ebf6e41b2f9d9f71acc4](https://github.com/asmjit/asmjit/tree/3577608cab0bc509f856ebf6e41b2f9d9f71acc4) | [License](licenses/AsmJit.txt) |
| AsmTK | [6e25b8983fbd8bf455c01ed7c5dd40c99b789565](https://github.com/asmjit/asmtk/tree/6e25b8983fbd8bf455c01ed7c5dd40c99b789565) | [License](licenses/AsmTK.txt) |
| Zydis | [569320ad3c4856da13b9dbf1f0d9e20bda63870e](https://github.com/zyantific/zydis/tree/569320ad3c4856da13b9dbf1f0d9e20bda63870e) | [License](licenses/Zydis.txt) |
| Zycore | [74620eefd233bec20daeb66e78e744ff06e273b7](https://github.com/zyantific/zycore-c/tree/74620eefd233bec20daeb66e78e744ff06e273b7) | [License](licenses/Zycore.txt) |
| nlohmann-json | [9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03](https://github.com/nlohmann/json/tree/9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03) | [License](licenses/nlohmann-json.txt) |
| Corrosion | [123be1e3d8170c86e121392e8bffa4def7dc3447](https://github.com/corrosion-rs/corrosion/tree/123be1e3d8170c86e121392e8bffa4def7dc3447) | [License](licenses/Corrosion.txt) |

The PolyHook trampoline and Corrosion build changes are maintained in `cmake/patches`. The UE4SS build uses the reviewed layout/hook overrides in `CMakeLists.txt`. Microsoft system libraries and compiler/runtime components are provided by the Windows development environment.

## Additional transitive notices

| Component | Reviewed source revision | License text |
| --- | --- | --- |
| Dear ImGui | [81160fee56027226bc80b48e196d0332f5541a8c](https://github.com/ocornut/imgui/tree/81160fee56027226bc80b48e196d0332f5541a8c) | [License](licenses/Dear-ImGui.txt) |
| Tracy | [37aff70dfa50cf6307b3fee6074d627dc2929143](https://github.com/wolfpld/tracy/tree/37aff70dfa50cf6307b3fee6074d627dc2929143) | [License](licenses/Tracy.txt) |
| GLFW | Bundled with the pinned UE4SS checkout | [License](licenses/GLFW.txt) |
| ImGuiColorTextEdit | [6d943aba9f7cef05da80b86dbb0253b63818f95c](https://github.com/UE4SS-RE/ImGuiColorTextEdit/tree/6d943aba9f7cef05da80b86dbb0253b63818f95c) | [License](licenses/ImGuiColorTextEdit.txt) |
| Lua 5.4 | Embedded Lua.org / PUC-Rio notice from the pinned LuaRaw headers | [License](licenses/Lua.txt) |

[Bundled LZ4 and rpmalloc notices](licenses/Tracy-bundled.txt) are retained for Tracy's embedded components.

UE4SS first-party utility notices are byte-identical to `licenses/UE4SS.txt`: ASMHelper, Constructs, DynamicOutput, File, Function, Helpers, IniParser, Input, JSON, LuaMadeSimple, LuaRaw, ParserBase, SinglePassSigScanner. The Unreal submodule is pinned at `86eb0f1587f1d6743ca0606308d717ee09da8ae3`.

[Rust dependency notices](licenses/Rust-dependencies.txt) cover the 68-crate Windows dependency closure exposed through `patternsleuth_bind`. The pinned patternsleuth workspace declares `MIT OR Apache-2.0` in Cargo.toml; it does not ship a separate license file. That fact and its declared authors are recorded in the aggregate rather than inventing an upstream copyright statement.

No game-owned assets or proprietary engine source are copied into this project. The separately downloaded UE4SS loader has its own distribution and notices.
