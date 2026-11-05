"""Transient evidence must never become a gameplay disk dependency."""
import json
from pathlib import Path
from unittest.mock import Mock

from ..bridge_support import diagnostic_history as history
from ..bridge_support.files import append_json_line, load_json_lines, read_text_tail_lines
from ..bridge_support.observability import append_runtime_item_latency_trace_event
from ..transport.pipe_transport import ACK, SET, register, unregister


def test_history_bounds_count_and_utf8_bytes_and_reports_drops(monkeypatch):
    monkeypatch.setattr(history, "MAX_RECORDS", 3)
    monkeypatch.setattr(history, "MAX_BYTES", 100)
    buffer = history.DiagnosticHistory()
    name = "current_state_write_polyhook_events.jsonl"
    for i in range(5):
        buffer.append(name, {"i": i})
    assert [json.loads(line)["i"] for line in buffer.lines(name)] == [2, 3, 4]
    assert buffer.snapshot()[name]["dropped_count"] == 2
    for i in range(5, 10):
        buffer.append(name, {"i": i, "text": "\u00e9" * 20})
    snapshot = buffer.snapshot()[name]
    assert snapshot["retained_count"] == 1
    assert snapshot["retained_bytes"] <= 100
    buffer.append(name, {"text": "x" * 200})
    assert json.loads(buffer.lines(name)[-1])["i"] == 9
    assert buffer.snapshot()[name]["dropped_count"] == 10


def test_native_events_ack_and_dedupe_without_open_stat_or_fsync(tmp_path, monkeypatch):
    name = "current_state_write_polyhook_events.jsonl"
    (tmp_path / name).write_text('{"old_process":true}\n')
    original_open = Path.open
    def reject_history_open(path, *args, **kwargs):
        if path.name in history.HISTORIES:
            raise AssertionError("old disk history must not be read")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", reject_history_open)
    pipe = register(tmp_path)
    try:
        pipe.epoch = 123
        assert pipe.lines(name) == ()
        with monkeypatch.context() as guards:
            guards.setattr(Path, "open", Mock(side_effect=AssertionError("disk open")))
            guards.setattr(Path, "stat", Mock(side_effect=AssertionError("disk stat")))
            guards.setattr("os.fsync", Mock(side_effect=AssertionError("disk flush")))
            for _ in range(2):
                pipe.accept([(SET, name, 7, b'{"state_class":"GameOver"}')])
            assert len(pipe.lines(name)) == 1
            assert pipe.acks == [(ACK, name, 7, b"")] * 2
            assert load_json_lines(tmp_path / name)[0]["state_class"] == "GameOver"
            assert read_text_tail_lines(tmp_path / name, max_lines=1) == pipe.lines(name)
    finally:
        unregister(pipe)


def test_client_traces_are_memory_only_even_if_directory_is_missing(tmp_path, monkeypatch):
    root = tmp_path / "missing"
    with monkeypatch.context() as guards:
        guards.setattr(Path, "open", Mock(side_effect=AssertionError("disk open")))
        guards.setattr(Path, "stat", Mock(side_effect=AssertionError("disk stat")))
        append_json_line(root / "runtime_grant_profile.jsonl", {"event": "granted"})
        assert append_runtime_item_latency_trace_event(root, "apply_ack")
    assert json.loads(history.history_for(root).lines("runtime_grant_profile.jsonl")[0])["event"] == "granted"
    assert json.loads(history.history_for(root).lines("runtime_item_latency_trace.jsonl")[0])["event"] == "apply_ack"
    assert not root.exists()
