"""Privacy regression fixtures use synthetic credentials, never live player data."""

import json
import xml.etree.ElementTree as ET

import pytest

from ..client_support.debug_redaction import DebugRedactor


@pytest.fixture
def redact(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Private Person")
    monkeypatch.delenv("HOME", raising=False)
    return DebugRedactor(save_dir=tmp_path / "saves")


@pytest.mark.parametrize("line", [
    '-AUTH_PASSWORD=private-credential -AUTH_TYPE=exchangecode',
    '-AUTH_PASSWORD="private-credential" -epicapp=KH3',
    "-AUTH_PASSWORD='private-credential' -EpicPortal",
    '--access-token private-credential --mode test',
    "args=['--password', 'private-credential', '--mode', 'test']",
    "request={'password': 'private-credential', 'status': 'failed'}",
    'payload={"refresh_token":"private-credential","status":"failed"}',
    'server_password = private-credential',
    'apiKey: private-credential',
    'Authorization: Bearer private-credential',
    'Cookie: session=private-credential; other=private-credential',
    'GET https://private-credential@example.invalid/api?token=private-credential&ok=yes',
    'GET https://example.invalid/file?X-Amz-Signature=private-credential&X-Amz-Credential=private-credential',
    'contact=private-credential@example.invalid',
])
def test_secrets_in_text_are_removed(redact, line):
    result = redact.content(line, ".log")
    assert b"private-credential" not in result
    assert b"[REDACTED]" in result


def test_nested_json_redaction_keeps_diagnostics_and_valid_syntax(redact):
    payload = {"pid": 12, "protocol_version": 3, "request_id": "request-7", "status": "failed",
               "nested": [{"auth_password": "private-secret", "EpicAccountId": "private-account",
                           "headers": {"Authorization": "Bearer private-bearer"},
                           "command": '-AUTH_PASSWORD="private-command" -EpicPortal',
                           "path": r"C:\Users\Private Person\game\UE4SS.log"}]}
    result = redact.content(json.dumps(payload), ".json")
    assert b"private-" not in result
    assert b"Private Person" not in result
    parsed = json.loads(result)
    assert parsed["pid"] == 12 and parsed["protocol_version"] == 3
    assert parsed["request_id"] == "request-7" and parsed["status"] == "failed"
    assert parsed["nested"][0]["path"] == r"[USER_HOME]\game\UE4SS.log"
    assert payload["nested"][0]["auth_password"] == "private-secret"


@pytest.mark.parametrize("count", [1, 2])
def test_jsonl_remains_one_valid_record_per_line(redact, count):
    data = "".join(json.dumps({"token": "private-secret", "index": i}) + "\n" for i in range(count))
    lines = redact.content(data, ".jsonl").splitlines()
    assert len(lines) == count
    assert [json.loads(line)["index"] for line in lines] == list(range(count))
    assert all(b"private-secret" not in line for line in lines)


def test_xml_redacts_entities_identity_tags_and_attributes(redact):
    data = ('<Crash><CommandLine>-AUTH_PASSWORD=&quot;private-secret&quot; -EpicPortal</CommandLine>'
            '<UserName>private-user</UserName><EpicAccountId>private-id</EpicAccountId>'
            '<Context token="private-token" module="UE4SS.dll"/><Error>bad &amp; broken</Error></Crash>')
    result = redact.content(data, ".runtime-xml")
    assert b"private-" not in result
    root = ET.fromstring(result)
    assert root.find("Error").text == "bad & broken"
    assert root.find("Context").get("module") == "UE4SS.dll"
    assert "-EpicPortal" in root.find("CommandLine").text


@pytest.mark.parametrize("path", [
    r"C:\Users\Private Person\log.txt", r"C:\\Users\\Private Person\\log.txt",
    "C:/Users/Private Person/log.txt", r"D:\Users\Another Person\log.txt",
    "/home/another-person/log.txt", "/Users/another-person/log.txt",
])
def test_profile_paths_are_redacted(redact, path):
    result = redact.text(path)
    assert all(name not in result for name in ("Private Person", "Another Person", "another-person"))
    assert result.endswith("log.txt")


def test_save_root_and_store_account_paths_are_redacted(redact, tmp_path):
    result = redact.text(str(tmp_path / "saves" / "ChoicesMade.sav"))
    assert result.startswith("[SAVE_DIR]") and result.endswith("ChoicesMade.sav")
    result = redact.text(r"D:\Documents\KINGDOM HEARTS III\1234567890abcdef1234567890abcdef\SaveGames")
    assert "1234567890abcdef" not in result


def test_utf16_logs_and_opaque_content(redact):
    assert b"private-secret" not in redact.content('password=private-secret'.encode("utf-16"), ".log")
    assert redact.content(b"\x00private-secret", ".log") is None
    assert redact.content(b"private-secret", ".wal") is None
    assert redact.content(b"MDMPprivate-secret", ".dmp") is None
    assert redact.content('<Crash password="private-secret"', ".runtime-xml") is None
    assert redact.content('{"password": "private-secret', ".json") is None
    assert redact.content('{"index": 1}\n{"password": "private-secret', ".jsonl") is None


def test_argument_arrays_and_unquoted_ini_passwords(redact):
    arguments = ["-AUTH_PASSWORD", "private-secret", "-epicapp", "KH3"]
    result = json.loads(redact.content(json.dumps({"argv": arguments}), ".json"))
    assert result["argv"] == ["-AUTH_PASSWORD", "[REDACTED]", "-epicapp", "KH3"]
    result = redact.content("Password = private secret with spaces\nMaxObjects=123\n", ".ini")
    assert b"private" not in result and b"secret with spaces" not in result
    assert b"MaxObjects=123" in result


def test_redacting_exported_text_again_preserves_placeholders(redact):
    value = 'password=private-secret\nAuthorization: Bearer private-token\n'
    result = redact.text(value)
    assert result == 'password=[REDACTED]\nAuthorization: [REDACTED]\n'
    assert redact.text(result) == result
    assert redact.content('password=private-secret', '.ini') == b'password=[REDACTED]'
