from pathlib import Path
import copy
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from emblem_viewpoints import apply, rotation, viewing_areas


def ref(n):
    return dict(ObjectPath=f'Map.{n}')


def fixture():
    return [
        dict(Type='Actor', Name='MarkActor', Properties=dict(RootComponent=ref(1))),
        dict(Type='SceneComponent', Properties=dict(RelativeLocation=dict(X=10, Y=20, Z=30),
             RelativeRotation=dict(Yaw=90), RelativeScale3D=dict(X=2, Y=2, Z=2))),
        dict(Type='TresHiddenMickeyComponent', Outer=ref(0), Properties=dict(m_MarkID='88',
             AttachParent=ref(1), RelativeLocation=dict(X=1))),
        dict(Type='TresDetectMarkerValidBoxComponent', Name='Area', Outer=ref(0),
             Properties=dict(AttachParent=ref(1), RelativeLocation=dict(X=5))),
        dict(Type='Actor', Name='OtherActor', Properties={}),
        dict(Type='TresDetectMarkerValidBoxComponent', Name='OtherArea', Outer=ref(4), Properties={}),
    ]


@pytest.mark.parametrize('angles,vector,expected', [
    ([0, 90, 0], [1, 0, 0], [0, 1, 0]),
    ([90, 0, 0], [1, 0, 0], [0, 0, 1]),
    ([0, 0, 90], [0, 1, 0], [0, 0, -1]),
])
def test_unreal_rotation_basis(angles, vector, expected):
    assert apply(rotation(angles), vector) == pytest.approx(expected)


def test_scaled_parent_and_live_room_rotation_move_the_correct_owned_area():
    areas = viewing_areas(fixture(), 0, 88, dict(position=[100, 200, 300], rotation=[0, 180, 0]))
    assert len(areas) == 1
    assert areas[0]['name'] == 'Area'
    assert areas[0]['position'] == pytest.approx([92, 200, 300])
    assert areas[0]['scale'] == [2, 2, 2]
    assert apply(areas[0]['rotation_matrix'], [1, 0, 0]) == pytest.approx([-1, 0, 0])
    assert apply(areas[0]['rotation_matrix'], [0, 0, 1]) == pytest.approx([0, 0, 1])


@pytest.mark.parametrize('change', ['cycle', 'external', 'nonfinite', 'duplicate'])
def test_invalid_source_geometry_is_rejected(change):
    data = fixture()
    if change == 'cycle':
        data[1]['Properties']['AttachParent'] = ref(3)
    elif change == 'external':
        data[1]['Properties']['AttachParent'] = dict(ObjectPath='OtherMap.1')
    elif change == 'nonfinite':
        data[3]['Properties']['RelativeLocation']['X'] = float('nan')
    else:
        data.append(copy.deepcopy(data[2]))
    with pytest.raises(ValueError):
        viewing_areas(data, 0, 88, dict(position=[100, 200, 300], rotation=[0, 180, 0]))
