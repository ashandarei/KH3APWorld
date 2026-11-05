# Music chest development

The canonical [native music-chest reference](../../../KH3Runtime/docs/MUSIC_CHESTS_DEVELOPMENT.md)
owns conversion, pause/focus handoff, audio restoration and native test contracts.
Use [Playing music chests](../../worlds/kh3/docs/melody-of-memory.md) for player
setup and [Linux integration](MOM_LINUX.md) for the external game handoff.

Music-chest runtime assets arrive through the compatible GoA pak; there is no
standalone music-chest pak or compatibility fallback. Native iteration does not
authorize rebuilding an APWorld. Production integration, different stock chest
materials and Wine/Proton audio still require their own live validation.
