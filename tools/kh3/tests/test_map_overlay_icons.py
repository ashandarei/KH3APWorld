"""Validate the embedded movie contract used by the native map renderer."""
import re
import struct
import unittest
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from tools.kh3.native_integration import native_project_root

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = native_project_root()
HEADER = RUNTIME / "KH3APProbeCpp/kh3ap/features/ui/map_overlay/map_chest_movie.h"
MOVIE = RUNTIME / "KH3APProbeCpp/resources/map_chest_movie.swf"


def movie_tags(data, offset=None):
    # Frame RECT has a five-bit field width followed by four signed fields.
    if offset is None:
        offset = 8 + (5 + 4 * (data[8] >> 3) + 7) // 8 + 4
    while offset < len(data):
        word, = struct.unpack_from("<H", data, offset)
        offset += 2
        kind, size = word >> 6, word & 63
        if size == 63:
            size, = struct.unpack_from("<I", data, offset)
            offset += 4
        yield kind, data[offset:offset + size]
        offset += size
    assert offset == len(data)


class MapOverlayIconTests(unittest.TestCase):
    def test_actionscript_sources_match_compiled_movie(self):
        spec = importlib.util.spec_from_file_location("chest_script", RUNTIME / "authoring/kh3/map_overlay/build_chest_icon.py")
        art = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(art)
        art.check_generated()
        self.assertNotIn('inline constexpr uint8_t map_chest_movie[]', HEADER.read_text())
        self.assertIn(f'map_chest_movie_size={MOVIE.stat().st_size};', HEADER.read_text())
        with patch.object(art, 'source_hashes', return_value={'APMapChestIcon.as': 'stale'}):
            with self.assertRaisesRegex(ValueError, 'regenerate'):
                art.check_generated()

        movie = MOVIE.read_bytes()
        classes = []
        for kind, _, payload, end in art.read_movie_tags(movie):
            if kind != 82:
                continue
            abc = art.ABC(movie[movie.index(0, payload+4)+1:end])
            classes.extend(abc.classes)
            self.assertEqual(abc.bodies[abc.classes['APMapChestIcons']]['code'], b'\xd0\x30\xd0\x49\x00\x47')
            chest_code = abc.bodies[abc.classes['APMapChestIcon']]['code']
            stop = chest_code[7]
            self.assertEqual(abc.qname(stop), 'stop')
            # The compiled constructor only calls super() and this.stop(). No
            # frame listener or artwork generation was moved into runtime AS.
            self.assertEqual(chest_code,
                             b'\xd0\x30\xd0\x49\x00\xd0\x4f' + bytes([stop, 0, 0x47]))
        self.assertEqual(set(classes), {'APMapChestIcons', 'APMapChestIcon'})

    def test_embedded_movie_has_unique_hidden_pools_and_vector_art(self):
        data = MOVIE.read_bytes()
        self.assertEqual(data[:4], b"FWS\x0a")
        self.assertEqual(struct.unpack_from("<I", data, 4)[0], len(data))
        tags = list(movie_tags(data))
        self.assertEqual({struct.unpack_from("<H", p)[0] for k, p in tags if k == 32},
                         {2, 4, 6, 8, 9, *range(11, 27), 50, 51, 52})
        sprites = {struct.unpack_from("<H", p)[0]: p for k, p in tags if k == 39}
        self.assertEqual(set(sprites), {3, 5, 7, 10, 28, 29, *range(30, 47), 53, 54, 55, 56})
        # KH3's loader cannot decode embedded bitmaps.
        self.assertFalse(any(k in {6, 20, 21, 35, 36, 90} for k, _ in tags))
        pools = [("chest", 128, 3), ("emblem", 90, 5), ("ingredient", 128, 7)]
        expected = {f"{name}{i}": character for name, count, character in pools for i in range(count)}
        slots, depths = {}, set()
        for kind, payload in movie_tags(sprites[28], 4):
            if kind != 26:
                continue
            self.assertEqual(payload[0], 0x2e)  # named character, matrix, color transform
            depth, character = struct.unpack_from("<HH", payload, 1)
            self.assertNotIn(depth, depths)
            depths.add(depth)
            # Identity matrix: no scale/skew, one-bit zero x/y translation.
            self.assertEqual(payload[5:7], b"\x02\x00")
            bits = "".join(f"{b:08b}" for b in payload[7:13])
            self.assertEqual(bits[:6], "011010")
            self.assertEqual([int(bits[6+i*10:16+i*10], 2) for i in range(4)], [256, 256, 256, 0])
            name = payload[13:-1].decode()
            self.assertNotIn(name, slots)
            slots[name] = character
        self.assertEqual(slots, expected)
        self.assertEqual(depths, set(range(1, 347)))
        # The moving pool has its own clipped viewport; the fixed legend is
        # outside it. Minimap instances leave both containers untransformed.
        root_places = [p for k, p in tags if k == 26]
        self.assertEqual(len(root_places), 3)
        self.assertEqual(struct.unpack_from('<HH', root_places[0], 1), (1, 29))
        self.assertEqual(root_places[0][7:-1], b'markerViewport')
        self.assertEqual(struct.unpack_from('<H', root_places[1], 3)[0], 10)
        self.assertEqual(root_places[1][13:-1], b'keybladeLegend')
        self.assertEqual(struct.unpack_from('<H', root_places[2], 3)[0], 56)
        self.assertEqual(root_places[2][13:-1], b'keybindHints')
        # A separate, initially transparent root sibling stays anchored while
        # the map pans/zooms. The optional chest row can be hidden independently.
        self.assertEqual(root_places[2][7:13], root_places[1][7:13])
        hint_places = [p for k, p in movie_tags(sprites[56], 4) if k == 26]
        for place, name in zip(hint_places, (b'zoomOut\0', b'zoomIn\0', b'lockedChests\0')):
            self.assertTrue(place.endswith(name))
        self.assertEqual(len(hint_places), 3)
        viewport_places = [p for k, p in movie_tags(sprites[29], 4) if k == 26]
        self.assertEqual(len(viewport_places), 1)
        self.assertEqual(struct.unpack_from('<HH', viewport_places[0], 1), (1, 28))
        self.assertEqual(viewport_places[0][7:-1], b'markers')

    def test_chest_frames_and_legend_match_single_color_palette(self):
        spec = importlib.util.spec_from_file_location("chest_art", RUNTIME / "authoring/kh3/map_overlay/build_chest_icon.py")
        art = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(art)
        colors = art.keyblade_colors()
        self.assertEqual(len(colors), 15)
        # Every Keyblade has its own tint, including dark-purple Oblivion.
        self.assertEqual(len(set(colors.values())), 15)
        self.assertEqual(colors['Classic Tone'], (128, 128, 128))
        self.assertEqual(colors['Oblivion'], (96, 32, 128))
        self.assertEqual(colors['Ever After'], (255, 128, 191))
        self.assertEqual(colors['Kingdom Key'], (53, 208, 111))
        self.assertEqual(colors['Happy Gear'], (183, 229, 52))
        self.assertEqual(colors['Starlight'], (191, 198, 208))
        shared = json.loads(art.PALETTES.read_text())["chests"]["palettes"]
        self.assertEqual(set(colors), set(shared))
        self.assertEqual(art.icon_image('chest.png', grayscale=True).getchannel('A').tobytes(),
                         art.icon_image('chest.png').getchannel('A').tobytes())
        header = HEADER.read_text()
        self.assertEqual(re.findall(r'^L"([^"]+)",$', header, re.M), list(colors))
        data = MOVIE.read_bytes()
        tags = list(movie_tags(data))
        sprite = next(p for k, p in tags if k == 39 and struct.unpack_from('<H', p)[0] == 3)
        self.assertEqual(struct.unpack_from('<H', sprite, 2)[0], 16)
        frames = list(movie_tags(sprite, 4))
        self.assertEqual(sum(k == 1 for k, _ in frames), 16)
        self.assertEqual(sum(k == 28 for k, _ in frames), 15)
        placements = [p for k, p in frames if k == 26]
        self.assertEqual(placements[0][0], 6)  # Original art, no color transform.
        self.assertEqual(struct.unpack_from('<H', placements[0], 3)[0], 2)
        self.assertEqual(len(placements), 16)
        for index, color in enumerate(colors.values()):
            placement = placements[1+index]
            self.assertEqual(placement[0], 14)
            self.assertEqual(struct.unpack_from('<HH', placement, 1), (1, 8))
            # One whole-art tint per frame; no second split-color region.
            bits = ''.join(f'{b:08b}' for b in placement[-6:])
            self.assertEqual(bits[:6], '011010')
            self.assertEqual([int(bits[6+i*10:16+i*10], 2) for i in range(4)],
                             [*(round(c * 256 / 255) for c in color), 256])
        # Hidden pool members must stop at construction, without native loops.
        symbols = next(p for k, p in tags if k == 76)
        self.assertIn(struct.pack('<H', 3) + b'APMapChestIcon\0', symbols)

        legend = art.legend_image(colors)
        # Keybinds have no panel/background; even the unused right edge stays
        # transparent on every row, including the optional locked-chest chord.
        for layer in art.keybind_layers():
            self.assertIsNone(layer.getchannel('A').crop((layer.width-10, 0, layer.width, layer.height)).getbbox())
            self.assertIsNotNone(layer.getchannel('A').getbbox())
        self.assertEqual(legend.size, (art.LEGEND_WIDTH*2, art.LEGEND_HEIGHT*2))
        for index, color in enumerate(colors.values()):
            self.assertEqual(legend.getpixel((44, 118+index*56)), (*color, 255))
            self.assertEqual(legend.getpixel((44, 134+index*56)), (*color, 255))
            self.assertEqual(legend.getpixel((28, 126+index*56)), (220, 226, 234, 255))

        # Decode the actual embedded shapes: each legend row has a small fill
        # table and mesh, instead of one large tessellation for the whole panel.
        for kind, payload in tags:
            if kind != 32 or not 9 <= struct.unpack_from('<H', payload)[0] <= 26:
                continue
            offset = 2 + (5 + 4*(payload[2] >> 3) + 7)//8
            self.assertLessEqual(payload[offset], 20)
            self.assertLess(len(payload), 20000)
        legend_sprite = next(p for k, p in tags if k == 39 and struct.unpack_from('<H', p)[0] == 10)
        placements = [p for k, p in movie_tags(legend_sprite, 4) if k == 26]
        self.assertEqual(len(placements), 17)
        for index, name in enumerate(['panel', 'title', *(f'row{i}' for i in range(15))]):
            self.assertTrue(placements[index].endswith(name.encode() + b'\0'))
            self.assertEqual(struct.unpack_from('<H', placements[index], 3)[0], 30+index)


if __name__ == "__main__":
    unittest.main()
