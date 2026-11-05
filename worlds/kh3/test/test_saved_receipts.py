
import pytest

from ..bridge_support.saved_receipts import SavedReceipts
from ..bridge_support.store import BridgeStore
from ..BridgeSession import KH3BridgeSession


def session_at(root):
    store = BridgeStore(root)
    store.write_json("slot_data.json", dict(protocol_name="kh3_ap_file_bridge", protocol_version=1,
        bridge=dict(protocol_name="kh3_ap_file_bridge", protocol_version=1, locations=[])))
    store.write_json("ap_items.json", dict(protocol_name="kh3_ap_file_bridge", protocol_version=1,
        items=[dict(index=i, item_name="Potion", location_id=-1, kh3_bridge_delivery_required=True)
               for i in range(4)]))
    return KH3BridgeSession(root)


def applied(session, indices, at="2026-09-27T10:00:00+00:00"):
    from .test_native_delivery_snapshots import write_native
    native = session.native_reconciliation_state()
    write_native(session.bridge_dir, "item_delivery_state.json", native,
        applied_item_indices=indices, last_applied_actions=[])
    write_native(session.bridge_dir, "item_delivery_ledger_summary.json", native,
        applied_indices=indices, applied_count=len(indices), total_count=4, pending_count=4-len(indices))


def test_snapshot_canonicalizes_without_filling_gaps():
    saved = SavedReceipts(19).add([21, 23, -1, 21])
    assert saved.payload() == dict(version=1, through=19, extra=[21, 23])
    assert saved.add([20]).payload() == dict(version=1, through=21, extra=[23])
    assert not saved.contains(20)
    with pytest.raises(ValueError, match="Missing native"):
        SavedReceipts.read({"last_applied_index_at_save": 19})


@pytest.mark.parametrize("bad", [None, [], "invalid", dict(version=True, through=3, extra=[]),
    dict(version=2, through=3, extra=[]),
    dict(version=1, through=-2, extra=[]), dict(version=1, through=3, extra=["5"])])
def test_invalid_snapshot_cannot_fall_back_to_larger_cutoff(bad):
    with pytest.raises(ValueError):
        SavedReceipts.read(dict(saved_receipts=bad, last_applied_index_at_save=100))
