# KH3 UI movie sources

The canonical [movie authoring guide](../../../KH3Runtime/docs/MOVIE_SOURCES.md)
lists editable ActionScript, variable contracts, generated DLL resources and
compiler/check commands. Run its commands from the runtime checkout; use
`KH3_RUNTIME_DIR` if it is not the usual sibling `KH3Runtime` directory.

APWorld-side behavior and integration tests:

- [Shop names, icons and Forge navigation](shop_ui/README.md)
- [Ingredients, Adversaries and Gate Opener](ingredient_journal/README.md)
- [Large map and minimap markers](map_overlay/README.md)
- [Now Playing](now_playing/README.md)

Movie changes rebuild the DLL; they do not require an APWorld rebuild.
