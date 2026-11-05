# Gummiphone Ingredients, Adversaries and Gate Opener

The native Gummiphone pages expose collection history, source maps and world-door
selection. [Native implementation](../../../../KH3Runtime/KH3APProbeCpp/kh3ap/features/journal/README.md)
covers movie patching and inactive map previews; [UI movie sources](../MOVIE_SOURCES.md)
covers authoring.

## Navigation

Open Gummiphone, then **Ingredients** (Character Files, anchor 13) or
**Adversaries** (anchor 23). Both are available before chest/enemy acquisition.
Ingredients shows **51 unique field ingredients**, with normal collected icons
and dim gray uncollected icons. Select by mouse, D-pad, stick or arrows; LB/RB,
Q/E or onscreen arrows filter worlds. Filtering changes the view, not completion.
The source panel always lists all source worlds using friendly map labels.

Confirm moves from grid to sources, then opens a selected location's full-screen
map. Back returns through map, sources, grid and home. Enter confirms and Space
goes back; Space also cancels Gate Opener confirmation. Mouse wheel/Page Up/Down
scroll long source lists. L1/R1 page matching map sheets; Square/Tab have no
journal binding. Separate source rows preserve readable names and headings.

Collection means the first recorded field pickup anywhere. Buying food, AP
rewards, cooking, consumption and loading an older save do not change it. The
journal works with checks off, Ingredientsanity or Ingredientsanity+; the 298
pickup spots do not become separate ingredient entries. Eight Flan/Pooh fruit
types remain outside this 51-type field tracker.

## Adversaries

The grid lists all **53 native visible kill-counter entries**, including unbeaten
ones, with status/counts and world filters. Native main/sub IDs combine variants.
Mouse wheel and D-pad/stick selection scroll the grid. These are saved game
counters; the optional `adversary_checks` setting separately awards 48 first-defeat
AP checks, restricted to filler/useful rewards, even with the journal closed.

Dark Inferno, Metal Troll, Chaos Carriage and Frost Serpent are boss-only;
unverified Sniper is also journal-only. The 44 native rows hiding kill counters
are outside this tracker. Visibility does not prove a row passed a live defeat test.

Source maps cover 49 entries and carry the **Vanilla locations** footer. Story
state and randomization can change actual encounters. Text-only landmarks cover
Metal Troll (San Fransokyo's first boss encounter), Sniper (Twilight Town's first
victory-bonus NPC fight) and Sorcerer (Old Mansion, Battlegate 3). Dark Inferno has
no verified map. Missing coordinates are not invented.

## Gate Opener

Gate Opener replaces Story (anchor 12). `gate_opener_count: 0..7` adds useful
items to newly generated seeds by replacing removable rewards. They appear as
`KEY_ITEM20` in native key-item inventory; AP reachability ignores them.

The picker shows twelve world doors with native title artwork. Unlocked doors
stay visible but disabled. Choosing a locked door opens a Yes/No confirmation
with Yes initially selected; No/Back cancels. Confirming spends one distinct
received item and records its door in the seed/player reconciliation journal.
The unlock survives travel, Retry and older native saves; inventory reconciles
to unspent receipts. Portal014 is excluded from the picker and native policy.

## Catalog generation and checks

Run from the APWorld checkout; stock assets/localization remain ignored fixtures:

```powershell
python tools/kh3/ingredient_journal/generate_catalog.py --items LOCAL_TresItemData.uasset
python -m tools.kh3.ingredient_journal.generate_adversaries --snapshot build/adversaries-test/catalog-with-actors.json --names build/adversaries-test/kh3_char_name.locres
.\scripts\test-kh3.ps1 -NativeIntegration -PytestArgs @('tools/kh3/tests/test_adversary_journal.py', 'tools/kh3/tests/test_ingredient_journal.py', 'tools/kh3/tests/test_shop_runtime_extension.py', '-q')
```

Enemy generation combines passive dictionary evidence, the player's English
localization, maintained target metadata and friendly minimap reference. Commit
generated metadata, not localization or stock movie fixtures. Movie rebuilding
and source/resource checks use the [runtime authoring guide](../../../../KH3Runtime/docs/MOVIE_SOURCES.md#generate-and-verify).
