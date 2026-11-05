"""Read authored viewing-box centers from CUE4Parse map exports.

These are camera placement candidates, never evidence of a completed photo.
Map the static transforms into the inspected live marker's rigid frame so room
translations/rotations do not turn a source coordinate into a stale position.
"""
import math


def rotation(angles):
    p, y, r = map(math.radians, angles)
    cp, sp, cy, sy, cr, sr = math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r)
    return [[cp*cy, sr*sp*cy-cr*sy, -cr*sp*cy-sr*sy],
            [cp*sy, sr*sp*sy+cr*cy, sr*cy-cr*sp*sy],
            [sp, -sr*cp, cr*cp]]


def multiply(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def apply(matrix, vector):
    return [sum(row[j]*vector[j] for j in range(3)) for row in matrix]


def vector(value, keys, default):
    result = [value.get(k, default) for k in keys]
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in result):
        raise ValueError('Invalid authored transform')
    return result


def viewing_areas(exports, actor_index, native_id, live_mark):
    cache = {}
    visiting = set()
    identity = rotation([0, 0, 0])
    actor_path = exports[actor_index]['Properties']['RootComponent']['ObjectPath'].rsplit('.', 1)[0]

    def index(reference):
        path, suffix = reference['ObjectPath'].rsplit('.', 1)
        if path != actor_path:
            raise ValueError('External component parent needs a live transform')
        n = int(suffix)
        if not 0 <= n < len(exports):
            raise ValueError('Component index is outside its package')
        return n

    def transform(n):
        if n in cache:
            return cache[n]
        if n in visiting:
            raise ValueError('Cyclic component attachment')
        visiting.add(n)
        props = exports[n].get('Properties', {})
        parent = transform(index(props['AttachParent'])) if props.get('AttachParent') else ([0, 0, 0], identity, [1, 1, 1])
        location = vector(props.get('RelativeLocation', {}), 'XYZ', 0)
        local_rotation = rotation(vector(props.get('RelativeRotation', {}), ('Pitch', 'Yaw', 'Roll'), 0))
        local_scale = vector(props.get('RelativeScale3D', {}), 'XYZ', 1)
        offset = apply(parent[1], [v*s for v, s in zip(location, parent[2])])
        result = ([v+d for v, d in zip(parent[0], offset)], multiply(parent[1], local_rotation),
                  [a*b for a, b in zip(parent[2], local_scale)])
        visiting.remove(n)
        cache[n] = result
        return result

    def owned(item):
        outer = item.get('Outer')
        return outer and index(outer) == actor_index

    markers = [i for i, item in enumerate(exports) if item.get('Type') == 'TresHiddenMickeyComponent'
               and owned(item) and str(item.get('Properties', {}).get('m_MarkID')) == str(native_id)]
    if len(markers) != 1:
        raise ValueError('Authored marker must be unique within the exact actor')
    mark_position, mark_rotation, _ = transform(markers[0])
    live_rotation = rotation(live_mark['rotation'])
    delta_rotation = multiply(live_rotation, list(map(list, zip(*mark_rotation))))
    result = []
    for i, item in enumerate(exports):
        if item.get('Type') != 'TresDetectMarkerValidBoxComponent' or not owned(item):
            continue
        position, box_rotation, scale = transform(i)
        relative = apply(delta_rotation, [a-b for a, b in zip(position, mark_position)])
        live_position = [a+b for a, b in zip(live_mark['position'], relative)]
        if any(not math.isfinite(v) or abs(v) > 10000000 for v in live_position):
            raise ValueError('Viewing area is outside the native teleport bounds')
        result.append(dict(name=item['Name'], position=live_position,
            box_extent=vector(item.get('Properties', {}).get('BoxExtent', {}), 'XYZ', 32), scale=scale,
            rotation_matrix=multiply(delta_rotation, box_rotation)))
    return result
