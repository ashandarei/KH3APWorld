# Now playing

An independent embedded Scaleform movie shows the audible song at the top center
of the viewport, with a thin two-pixel authored margin at the top. Short titles
are centered inside the panel too. The dark background has 17.5% opacity in the
middle and fades to transparent at both side edges; text remains fully opaque.
The title window is 50% wider, with the original
text size and panel height. It does not receive focus or input and hides in menus,
cinematics and title screens. It follows Vanilla, shuffled music, form themes,
and custom replacements. Suspended slots are excluded; pending or failed custom
decodes continue to show the native track until custom playback starts.

Native soundtrack labels live in `now_playing_title.h`. Unlisted cues display a
readable asset name. The title spellings reference the
[official soundtrack tracklist](https://www.jp.square-enix.com/music/sem/page/kh3/).
Custom titles preserve the filename's case and spaces, without its extension.

`KH3_RUNTIME_DIR/authoring/kh3/now_playing/APNowPlaying.as` is the editable
document class. The shop/journal compiler builds
one original class in its own movie, with a runtime reference to KH3's existing
font library. No game font or movie is bundled. The window uses the first
**six words** as its reference width, then extends that width by 50%, capped at 630 authored
pixels and 48% of the stage width. The title pauses for **2.5 seconds**,
slides left at **28 pixels/second**, pauses at the end for **2.5 seconds**, then
resets. Short titles remain stationary. A changed title restarts the cycle.
`maxWords` supports values 1–12; the default is six. Timing, width and style are
authored in the ActionScript. **AP Options > Map options > Song Display** toggles
the panel immediately and saves the choice for this seed. It is independent of
map icons, the map shortcut and music randomization. YAML supplies its initial
value (enabled by default):

```yaml
Kingdom Hearts III:
  song_display: true
```

Regenerate the checked-in SWF and hash header after source edits:

```powershell
python ../KH3Runtime/authoring/kh3/now_playing/build_movie.py --java tmp/kh3-workshop-ui/jre21/jdk-21.0.12+8-jre/bin/java.exe --ffdec tmp/kh3-workshop-ui/ffdec/ffdec-cli.jar
python ../KH3Runtime/authoring/kh3/now_playing/build_movie.py --check
```

Normal DLL builds verify the generated movie and require no Java/JPEXS. Music
events update the cached title using the event's existing slot mapping. The UI
publishes changes on a 100 ms gate; the movie handles smooth scrolling locally.
The movie keeps its panel and immutable font reference across travel, with weak
UObject bindings in C++. No GoA pak or APWorld rebuild is needed.

## Soundtrack-name coverage

Soundtrack-name coverage is incomplete: uncommon native cues such as
`BGM_dw152_battle` currently show the readable fallback `dw152 battle`.
Additional soundtrack mappings are needed for these cues.
