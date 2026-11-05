"""Verify stock-code preservation and the shipped shop payload's provenance."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest

from tools.kh3.native_integration import native_project_root, load_native_module

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = native_project_root()
SOURCE = RUNTIME / 'authoring/kh3/shop_ui'
RESOURCES = RUNTIME / 'KH3APProbeCpp/resources'
sys.path.insert(0, str(SOURCE))
from abc_format import tag, tags, u30
import build_runtime_patch as builder


def synthetic_stock():
    # Independently authored structural fixture, not extracted game bytecode.
    strings = ('CoTradeListItem', 'Shop_fla', 'McMenuMixTopObj_122', 'Object')
    abc = struct.pack('<HH', 16, 46) + b'\x01\x01\x01' + u30(5)
    abc += b''.join(u30(len(s)) + s.encode() for s in strings)
    abc += bytes([3, 0x16, 0, 0x16, 2, 1, 4, 7, 1, 1, 7, 2, 3, 7, 1, 4])
    abc += b'\x05' + b'\x00\x00\x00\x00' * 5
    abc += bytes([0, 2, 1, 3, 0, 0, 0, 0, 2, 3, 0, 0, 2, 0, 1, 0, 3, 0])
    abc += bytes([1, 4, 2, 1, 4, 1, 0, 2, 4, 2, 1])
    codes = [b'\xd0\x30\xd0\x49\x00\x47', b'\xd0\x30\x47'] * 2 + [b'\xd0\x30\x47']
    abc += u30(5)
    for method, code in enumerate(codes):
        abc += b''.join(u30(v) for v in (method, 1, 1, 1, 2, len(code))) + code + b'\x00\x00'
    data = b'\x08\x00\x00\x18\x01\x00' + tag(69, struct.pack('<I', 8))
    data += tag(82, struct.pack('<I', 1) + b'fixture\0' + abc) + tag(1) + tag(0)
    return b'FWS\x15' + struct.pack('<I', len(data) + 8) + data


def literal_operations(delta):
    pos = 0
    while pos < len(delta):
        opcode = delta[pos]
        pos += 1
        if opcode == 0:
            pos += 8
        elif opcode == 1:
            length = struct.unpack_from('<I', delta, pos)[0]
            pos += 4
            yield delta[pos:pos+length]
            pos += length
        else:
            raise AssertionError('Unknown patch operation')
    assert pos == len(delta)


class ShopRuntimeExtensionTests(unittest.TestCase):
    def test_all_resource_metadata_matches_authored_sources_and_binaries(self):
        load_native_module("authoring/kh3/movie_resources.py").check_generated()

    def test_splices_preserve_original_methods_and_movie_tags(self):
        original = synthetic_stock()
        own = builder.blank_helper_movie()
        start, _, _, end, _ = next(builder.abc_tags(own))
        helper = own[start:end]
        result, delta, audit = builder.extend_movie(original, helper)
        self.assertEqual(set(next(builder.abc_tags(result))[4].classes), {'kh3ap.APShopRuntime'})
        literals = list(literal_operations(delta))
        self.assertEqual([p for p in literals if len(p) > 64], [helper])
        self.assertTrue(all(e['removed'] <= 4 for e in audit))
        self.assertTrue(all(e['provenance'].startswith('encoded ') for e in audit if e['removed']))
        self.assertEqual(sum(e['added'] - e['removed'] for e in audit), len(result)-len(original))
        self.assertEqual(len(list(builder.abc_tags(result))), 2)

    def test_constructor_control_flow_is_rejected(self):
        for code, exceptions in ((b'\x10\x00\x00\x00\x47', 0),
                                 (b'\x47\x47', 0), (b'\xd0\x47', 1)):
            self.assertFalse(builder.constructor_is_straight_line(dict(code=code, exceptions=exceptions)))
        self.assertTrue(builder.constructor_is_straight_line(dict(code=b'\xd0\x30\xd0\x49\x00\x47', exceptions=0)))
        # Listener arguments add constant booleans and a lexical event type;
        # they introduce no control-flow target that could bypass our hook.
        self.assertTrue(builder.constructor_is_straight_line(dict(code=b'\xd0\x30\x60\x01\x26\x27\x47', exceptions=0)))

    def test_corrupted_original_method_is_detected(self):
        original = synthetic_stock()
        own = builder.blank_helper_movie()
        start, _, _, end, _ = next(builder.abc_tags(own))
        result, _, _ = builder.extend_movie(original, own[start:end])
        _, _, abc_start, _, abc = list(builder.abc_tags(result))[1]
        damaged = bytearray(result)
        damaged[abc_start + abc.bodies[1]['start']] ^= 1
        old_abc = next(builder.abc_tags(original))[4]
        hooks = {name:abc.bodies[old_abc.classes[name]]['code'][len(old_abc.bodies[old_abc.classes[name]]['code'])-1:-1]
                 for name in builder.HOOKS}
        with self.assertRaisesRegex(ValueError, 'instructions changed'):
            builder.verify_preservation(original, bytes(damaged), hooks)

    def test_packaged_literals_contain_only_one_original_helper(self):
        source = builder.OUTPUT.read_text()
        delta = (RESOURCES / 'shop_movie_patch_egs.bin').read_bytes()
        literals = list(literal_operations(delta))
        large = [p for p in literals if len(p) > 64]
        self.assertEqual(len(large), 1)
        blank = builder.blank_helper_movie()
        frame_start = next(tags(blank))[1]
        movie = blank[:frame_start] + large[0] + tag(1) + tag(0)
        movie = movie[:4] + struct.pack('<I', len(movie)) + movie[8:]
        classes = list(builder.abc_tags(movie))
        self.assertEqual(len(classes), 1)
        self.assertEqual(set(classes[0][4].classes), {'kh3ap.APShopRuntime'})
        self.assertEqual(len(classes[0][4].bodies), 6)
        digest = hashlib.sha256((SOURCE / 'APShopRuntime.as').read_text(encoding='utf-8').encode('utf-8')).hexdigest()
        self.assertIn('Helper source SHA256 (LF): ' + digest, source)
        self.assertIn('Resource SHA256: ' + hashlib.sha256(delta).hexdigest(), source)
        self.assertNotIn('uint8_t delta[]', source)

    @unittest.skipUnless((ROOT / 'build/shop-runtime/original.gfx').exists(), 'Local vanilla fixture unavailable')
    def test_real_game_fixture_retains_every_method_and_graphic(self):
        source = builder.OUTPUT.read_text()
        delta = (RESOURCES / 'shop_movie_patch_egs.bin').read_bytes()
        helper = next(p for p in literal_operations(delta) if len(p) > 64)
        original = (ROOT / 'build/shop-runtime/original.gfx').read_bytes()
        result, expected_delta, _ = builder.extend_movie(original, helper)
        self.assertEqual(delta, expected_delta)
        self.assertIn('Extended SHA256: ' + hashlib.sha256(result).hexdigest(), source)

    @unittest.skipUnless((ROOT / 'build/shop-runtime-steam/original.gfx').exists(), 'Local Steam fixture unavailable')
    def test_steam_fixture_preserves_stock_code_and_uses_same_original_helper(self):
        source = (builder.OUTPUT.parent / 'shop_movie_patch_steam_data.h').read_text()
        delta = (RESOURCES / 'shop_movie_patch_steam.bin').read_bytes()
        helpers = [p for p in literal_operations(delta) if len(p) > 64]
        self.assertEqual(len(helpers), 1)
        egs_delta = (RESOURCES / 'shop_movie_patch_egs.bin').read_bytes()
        self.assertEqual(helpers, [p for p in literal_operations(egs_delta) if len(p) > 64])
        original = (ROOT / 'build/shop-runtime-steam/original.gfx').read_bytes()
        result, expected_delta, _ = builder.extend_movie(original, helpers[0])
        self.assertEqual(delta, expected_delta)
        self.assertIn('Original SHA256: ' + hashlib.sha256(original).hexdigest(), source)
        self.assertIn('Extended SHA256: ' + hashlib.sha256(result).hexdigest(), source)


if __name__ == '__main__':
    unittest.main()
