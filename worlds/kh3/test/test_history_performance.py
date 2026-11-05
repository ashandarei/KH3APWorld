"""History growth, immutable publication, and durable incremental normalization."""
import json
from unittest.mock import patch

import pytest

from ..bridge_support.files import atomic_write_json, load_optional_json_view
from ..client_support.json_snapshot import chunked_json_array, encode_json, freeze_json
from ..transport.pipe_transport import SET, register, unregister


def test_encoded_chunks_reuse_history_and_remain_canonical_with_unicode_and_synthetic_items():
    old = freeze_json([{"index": 0, "item_name": "ポーション", "nested": {"a": [1, None]}}])
    first = freeze_json({"items": chunked_json_array((old,)), "protocol": 1})
    first_bytes = encode_json(first)
    new = freeze_json([{"index": 1, "item_name": "Ether"}])
    synthetic = freeze_json([{"index": 999, "item_name": "Shard"}])
    second = freeze_json({"items": chunked_json_array((old, new, synthetic)), "protocol": 1})
    dumps = json.dumps
    with patch("json.dumps", wraps=dumps) as serializer:
        second_bytes = encode_json(second)
    assert not any(call.args[0] is old for call in serializer.call_args_list)
    assert encode_json(first) is first_bytes
    assert encode_json(second) is second_bytes
    assert second_bytes == dumps(second, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    assert json.loads(first_bytes)["items"] == old
    replacement = freeze_json({"items": chunked_json_array((old, new)), "protocol": 1})
    assert len(json.loads(encode_json(replacement))["items"]) == 2


def test_persistent_publication_shares_encoding_and_snapshot_and_preserves_durability(tmp_path):
    pipe = register(tmp_path)
    path = tmp_path / "ap_items.json"
    try:
        payload = freeze_json({"items": [{"index": 1, "name": "你好"}]})
        atomic_write_json(path, payload)
        assert pipe.values[path.name] is pipe.state.entries[path.name][0]
        assert load_optional_json_view(path) is payload
        assert pipe.state.read_view(path.name) is payload
        with patch("os.fsync", side_effect=OSError("disk unavailable")):
            with pytest.raises(OSError):
                atomic_write_json(path, freeze_json({"items": [{"index": 2}]}))
        assert load_optional_json_view(path) is payload
        editable = pipe.read(path.name)
        editable["items"][0]["index"] = 99
        assert load_optional_json_view(path)["items"][0]["index"] == 1
    finally:
        unregister(pipe)


def test_read_only_event_cache_tracks_head_and_delete(tmp_path):
    pipe = register(tmp_path)
    name = "runtime_receipt_ack_event.json"
    try:
        pipe.accept([(SET, name, 1, b'{"index":1}'), (SET, name, 2, b'{"index":2}')])
        first = pipe.read_view(name)
        assert first is pipe.read_view(name)
        with pytest.raises(TypeError):
            first["index"] = 100
        pipe.delete(name)
        assert pipe.read_view(name)["index"] == 2
        assert first["index"] == 1
        pipe.delete(name)
        assert pipe.read_view(name) is None
    finally:
        unregister(pipe)
