# Includes code ported and adapted from WaterKH/KH3RandomizerClient (Apache-2.0).
# Modified for the KH3 Archipelago Python seed generator.
# See ../LICENSE-KH3RandomizerClient.txt and ../THIRD_PARTY_NOTICES.txt.
"""KH3 locres resources, item labels and Secret Report hints."""
from __future__ import annotations

import struct

from .assets import AssetError, Reader, fstring
from .rng import SeedRandom

REPORT_OFFSETS = (0x1125C, 0x117B0, 0x11F76, 0x1289A, 0x1310A, 0x13C68,
                  0x14402, 0x14E3C, 0x157C4, 0x163CA, 0x16C76, 0x17562, 0x17DE8)
NAMESPACES = ('', 'Game', 'KH3Text', 'Item', 'item', 'kh3_item', 'kh3_item.key',
              'kh3_mobile.lsi', 'kh3_mobile.top', 'kh3_mobile.treasure')
GOALS = {'KEY_ITEM14': 'Proof of Fantasy', 'KEY_ITEM15': 'Proof of Promises',
         'KEY_ITEM16': 'Proof of Times Past', 'KEY_ITEM17': 'Heart Piece'}
SHARD = 'KH3AP_ULTIMA_WEAPON_SHARD'
SPECIAL_NAMES = {**GOALS, 'KEY_ITEM18': 'Munny Pouch', 'KEY_ITEM19': 'Large Munny Pouch', 'KEY_ITEM20': 'Gate Opener',
                 SHARD: 'Ultima Weapon Shard'}
SPECIAL_DETAILS = {**{key: 'A proof used to fulfill the randomizer goal.' for key in GOALS},
                   'KEY_ITEM17': "A piece of Kairi's heart.", 'KEY_ITEM18': 'Contains 100 munny.',
                   'KEY_ITEM19': 'Contains 1,000 munny.',
                   'KEY_ITEM20': 'Use Gate Opener in the Gummiphone to permanently open one locked Garden world door. Cannot open the final portal.',
                   SHARD: 'A fragment of Ultima Weapon. Collect every required shard to receive the completed Keyblade.'}


def battle_key(token):
    if token in GOALS:
        return 'ap_goal_' + token[8:]
    return {'KEY_ITEM18': 'munny_pouch', 'KEY_ITEM19': 'large_munny_pouch', 'KEY_ITEM20': 'gate_opener',
            SHARD: 'ultima_weapon_shard'}.get(
                token, 'ap_' + token[8:] if token.startswith('ITEM_AP_') else None)


def text_ids(token):
    key = battle_key(token)
    return (f'kh3_item.battle.{key}', f'kh3_item.battle.detail_{key}') if key else (token, token + '_DETAIL')


class Locres:
    def __init__(self, data):
        r = Reader(data)
        self.header = r.take(17)
        if self.header[16] != 1:
            raise AssetError('Expected a compact UE4 locres resource')
        offset = r.unpack('i')
        self.reserved = r.take(4)
        count = r.unpack('i')
        self.namespaces = {}
        if not 0 <= count <= len(data) // 8 or not 29 <= offset <= len(data) - 4:
            raise AssetError('Invalid locres header')
        for _ in range(count):
            namespace = r.string()
            keys = {}
            for _ in range(r.unpack('i')):
                key = r.string()
                keys[key] = [r.unpack('I'), r.unpack('i')]
            if namespace in self.namespaces:
                raise AssetError('Duplicate locres namespace')
            self.namespaces[namespace] = keys
        if r.pos != offset:
            raise AssetError('Invalid locres string array offset')
        self.strings, self.offsets, self.wide = [], {}, []
        for index in range(r.unpack('i')):
            self.offsets[r.pos + 4] = index
            self.wide.append(struct.unpack_from('<i', data, r.pos)[0] < 0)
            self.strings.append(r.string())
        if r.pos != len(data):
            raise AssetError('Unparsed locres data')
        for keys in self.namespaces.values():
            if any(not 0 <= entry[1] < len(self.strings) for entry in keys.values()):
                raise AssetError('Invalid locres localized string index')

    def set(self, namespace, key, value):
        keys = self.namespaces.setdefault(namespace, {})
        if key in keys:
            # Do not change other keys that share the original localized string.
            index = keys[key][1]
            if self.strings[index] == value:
                return
            keys[key][1] = len(self.strings)
        else:
            keys[key] = [0, len(self.strings)]
        self.strings.append(value)
        self.wide.append(not value.isascii())

    def reports(self, hints):
        for i, offset in enumerate(REPORT_OFFSETS, 2):
            if offset not in self.offsets:
                raise AssetError(f'Missing Secret Report {i - 1} text in bundled mobile locres')
            self.strings[self.offsets[offset]] = '<br><br>'.join(hints[f'SecretReport{i:02}'])

    def item_labels(self, tokens, names, details):
        for token in sorted(set(tokens) | {'AP_ITEM_000'}):
            display = names.get(token, SPECIAL_NAMES.get(token, 'Archipelago Item'))
            detail = details.get(token, SPECIAL_DETAILS.get(token, 'An Archipelago item.'))
            for namespace in NAMESPACES:
                self.set(namespace, token, display)
                self.set(namespace, token + '_DETAIL', detail)
            for key, value in (('Name', display), ('DisplayName', display), ('Detail', detail)):
                self.set(token, key, value)
            key = battle_key(token)
            if key:
                self.set('kh3_item.battle', key, display)
                self.set('kh3_item.battle', 'detail_' + key, detail)

    def encode(self):
        output = bytearray(self.header + b'\0' * 4 + self.reserved + struct.pack('<i', len(self.namespaces)))
        for namespace, keys in self.namespaces.items():
            output.extend(fstring(namespace) if namespace else struct.pack('<i', 1) + b'\0')
            output.extend(struct.pack('<i', len(keys)))
            for key, (hashed, index) in keys.items():
                output.extend(fstring(key) + struct.pack('<Ii', hashed, index))
        struct.pack_into('<i', output, 17, len(output))
        output.extend(struct.pack('<i', len(self.strings)))
        for value, wide in zip(self.strings, self.wide):
            output.extend(fstring(value, wide) if value else struct.pack('<i', 1) + b'\0')
        return bytes(output)


def hint_location(placement, category=False, slot=0):
    table, row = placement.get('source_table', ''), placement.get('source_id', '')
    world = placement.get('world', '')
    description = (placement.get('location_description') or placement.get('ap_location_name') or '').rstrip('.')
    remote = placement.get('is_remote_location', placement.get('location_player_slot', slot) != slot)
    if remote:
        game = placement.get('location_game') or world
        player = placement.get('location_player_name')
        world = f"{player}'s {game} world" if player and game else f'the {game} world' if game else 'another Archipelago world'
        return f'in {world}.' if category or not description else f'at {description} in {world}.'
    if table == 'LevelUp':
        return "on Sora's Level Ups." if category else f"on Sora's Level Up {row}."
    if table == 'LuckyMark':
        return 'on Lucky Emblem Milestones.' if category else f'on Lucky Emblem Milestone {row}.'
    if category:
        return f'in {world}.' if world else 'at an Archipelago location.'
    if table.startswith('Treasure'):
        return f'in {world} at {description}.' if world and description else f'in {world}.'
    if table == 'VBonus' and description:
        return f'at {description}.'
    return (f'at {description} in {world}.' if description and world else f'at {description}.' if description
            else f'in {world}.' if world else 'at an Archipelago location.')


def make_hints(seed, config, metadata):
    settings = config.get('hint', {})
    hint_type = 'None' if metadata.get('slot_data', {}).get('race_mode') else settings.get('type', 'Verbose')
    if 'Category' in hint_type:
        hint_type = 'Category'
    elif 'Vague' in hint_type:
        hint_type = 'Vague'
    if hint_type not in ('None', 'Verbose', 'Vague', 'Category'):
        raise ValueError(f'Unsupported hint type: {hint_type}')
    summary = metadata['placement_summary']
    placements = summary.get('hint_locations', summary['locations'])
    important = set(settings.get('checks', list(SPECIAL_NAMES.values()) +
                         [f'Secret Report {n}' for n in range(1, 14)] +
                         ['Magic: ' + name for name in ('Fire', 'Blizzard', 'Thunder', 'Aero', 'Water', 'Cure')]))
    slot = int(metadata['slot'])
    grouped = {}
    if hint_type != 'None':
        for placement in placements:
            if int(placement.get('recipient_slot', slot)) != slot or placement['item_name'] not in important:
                continue
            name = placement['item_name']
            if name.startswith('Magic: '):
                name = name[7:] + ' Element'
            elif ': ' in name and name.startswith(('Ability:', 'Bonus:')):
                name = name.split(': ', 1)[1]
            location = hint_location(placement, hint_type in ('Vague', 'Category'), slot)
            text = f'{name} is {location}' if hint_type == 'Verbose' else (
                f'There is 1 important check {location}' if hint_type == 'Vague' else location)
            grouped.setdefault(text, []).append(placement)
    rng = SeedRandom(seed)
    if hint_type == 'Category':
        categories = list(dict.fromkeys(hint_location(p, True, slot) for p in placements))
        rng.shuffle(categories)
        for category in categories:
            if len(grouped) >= 13:
                break
            grouped.setdefault(category, [])
    values = {f'SecretReport{n:02}': [] for n in range(2, 15)}
    targets = {report: [] for report in values}
    for text, sources in grouped.items():
        batches = [sources] if hint_type == 'Category' else [[source] for source in sources]
        if hint_type == 'Category' and all(f'Secret Report {n}' in {p['item_name'] for p in sources}
                                          for n in range(1, 14)):
            # A cross-world category can contain every report. Split its hint targets
            # so each group still has a report that will not point to its own item.
            batches = [[p for p in sources if p['item_name'] != 'Secret Report 13'],
                       [p for p in sources if p['item_name'] == 'Secret Report 13']]
        for batch in batches:
            hinted = {p['item_name'] for p in batch}
            eligible = [report for report in values if f'Secret Report {int(report[12:]) - 1}' not in hinted]
            if not eligible:
                raise ValueError('Secret Report category would hint every report, including itself')
            minimum = min(len(values[report]) for report in eligible)
            report = rng.choice([report for report in eligible if len(values[report]) == minimum])
            if hint_type == 'Category':
                count = len(sources)
                text = f'There is 1 important check {text}' if count == 1 else f'There are {count} important checks {text}'
            values[report].append(text)
            targets[report].extend(batch)
    maximum = max(map(len, values.values()), default=0)
    for hints in values.values():
        hints.extend([''] * (maximum - len(hints)))
        rng.shuffle(hints)
    reports = {}
    for report, sources in targets.items():
        seen = set()
        records = []
        for source in sources:
            identity = (int(source['location_player_slot']), int(source['ap_location_id']))
            if identity in seen:
                continue
            seen.add(identity)
            records.append(dict(location=identity[1], player=identity[0],
                                location_name=source.get('location_description') or source.get('ap_location_name', ''),
                                player_name=source.get('location_player_name', ''), item_name=source['item_name']))
        reports[str(int(report[12:]) - 1)] = records
    metadata['secret_report_hints'] = dict(format_version=1, reports=reports)
    return values
