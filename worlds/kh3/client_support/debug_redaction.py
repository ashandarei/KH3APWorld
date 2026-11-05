"""Export-only redaction. Never modify source logs, checkpoints, or crash files."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


REDACTED = "[REDACTED]"
_PRIVATE_KEY = re.compile(
    r"(?:authlogin|.*password|.*passwd|pwd|.*token|.*secret|.*apikey|"
    r"(?:proxy)?authorization|cookie|setcookie|epicusername|epicuserid|epicaccountid|"
    r"username|userid|accountid|loginid|steamid(?:64)?|machineid|computername|"
    r"accesskey|signature|xamzcredential|xamzsignature|xgoogcredential|xgoogsignature)", re.I)
_KEY = (r"(?:auth[-_.]?login|[\w.-]*password|[\w.-]*passwd|pwd|[\w.-]*token|[\w.-]*secret|"
        r"[\w.-]*api[-_.]?key|(?:proxy[-_.]?)?authorization|(?:set[-_.]?)?cookie|"
        r"epic[-_.]?(?:username|userid|accountid)|user[-_.]?(?:name|id)|account[-_.]?id|"
        r"login[-_.]?id|steam[-_.]?id(?:64)?|machine[-_.]?id|computer[-_.]?name|"
        r"access[-_.]?key|signature|x[-_.]?(?:amz|goog)[-_.]?(?:credential|signature))")
_ASSIGNMENT = re.compile(
    rf"(?P<prefix>(?<![\w.-])[-/]?[\"']?(?P<key>{_KEY})[\"']?\s*[:=]\s*)"
    r"(?P<value>\[REDACTED\]|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s&,;<>\"'}\]]+)", re.I)
_ARGUMENT = re.compile(rf"(?P<prefix>(?<!\S)--?(?P<key>{_KEY})\s+)"
                       r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s]+)", re.I)
_ARGUMENT_LIST = re.compile(rf"(?P<prefix>[\"']--?(?P<key>{_KEY})[\"']\s*,\s*)"
                            r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')", re.I)
_INI_SECRET = re.compile(rf"(?im)^(?P<prefix>\s*(?P<key>{_KEY})\s*=\s*)(?P<value>[^\r\n]*)")
_AUTH_HEADER = re.compile(r"(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*)[^\r\n]+")
_URL_USERINFO = re.compile(r"(?i)(\b(?:https?|wss?)://)[^\s/@]+@")
_PROFILE = re.compile(r"(?i)(\b[A-Z]:[/\\]+Users[/\\]+)[^/\\\r\n\"<>]+|"
                      r"(?i:/(?:home|Users)/)[^/\r\n\"<>]+")
_ACCOUNT_PATH = re.compile(r"(?i)([/\\]+KINGDOM HEARTS III[/\\]+)[0-9a-f]{16,32}(?=[/\\])")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_TEXT_SUFFIXES = {".json", ".jsonl", ".log", ".txt", ".ini", ".lua", ".xml", ".runtime-xml", ".sav"}


def _private_key(key: str) -> bool:
    return _PRIVATE_KEY.fullmatch(re.sub(r"[^a-z0-9]", "", key.lower())) is not None


class DebugRedactor:
    def __init__(self, *, save_dir: Path):
        # Longest roots first; retain useful relative module/config paths.
        roots = [(str(save_dir), "[SAVE_DIR]")]
        for variable in ("USERPROFILE", "HOME"):
            value = os.environ.get(variable)
            if value:
                roots.append((value, "[USER_HOME]"))
        self.roots = sorted(roots, key=lambda entry: len(entry[0]), reverse=True)

    def text(self, value: str) -> str:
        def assignment(match):
            if not _private_key(match["key"]):
                return match[0]
            quote = match["value"][:1] if match["value"].startswith(('"', "'")) else ""
            return match["prefix"] + quote + REDACTED + quote

        value = _AUTH_HEADER.sub(lambda match: match[1] + REDACTED, value)
        value = _ASSIGNMENT.sub(assignment, value)
        value = _ARGUMENT.sub(assignment, value)
        value = _ARGUMENT_LIST.sub(assignment, value)
        value = _URL_USERINFO.sub(lambda match: match[1] + REDACTED + "@", value)
        for root, replacement in self.roots:
            for spelling in {root, root.replace("\\", "/"), root.replace("\\", "\\\\")}:
                value = re.sub(re.escape(spelling) + r"(?=$|[/\\\s\"'])",
                               lambda _: replacement, value, flags=re.I)
        value = _PROFILE.sub(lambda match: (match[1] or "/home/") + "[USER]", value)
        value = _ACCOUNT_PATH.sub(lambda match: match[1] + "[ACCOUNT]", value)
        return _EMAIL.sub(REDACTED, value)

    def structured(self, value):
        if isinstance(value, dict):
            return {self.text(key): REDACTED if _private_key(key) else self.structured(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [REDACTED if index and isinstance(value[index - 1], str)
                    and value[index - 1].startswith("-") and _private_key(value[index - 1].lstrip("-"))
                    else self.structured(item) for index, item in enumerate(value)]
        return self.text(value) if isinstance(value, str) else value

    def content(self, data: bytes | str, suffix: str) -> bytes | None:
        if suffix not in _TEXT_SUFFIXES:
            return None
        if isinstance(data, bytes):
            try:
                encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
                text = data.decode(encoding)
            except UnicodeError:
                return None
        else:
            text = data
        if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
            return None
        if suffix == ".jsonl":
            lines = []
            for line in text.splitlines():
                try:
                    value = json.loads(line)
                except ValueError:
                    if line.strip():
                        return None
                    lines.append(line)
                else:
                    redacted = self.structured(value)
                    lines.append(json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
                                 if redacted != value else line)
            return ("\n".join(lines) + ("\n" if text.endswith("\n") else "")).encode("utf-8")
        try:
            value = json.loads(text)
        except ValueError:
            if suffix == ".json":
                return None
        else:
            redacted = self.structured(value)
            if redacted != value:
                return json.dumps(redacted, ensure_ascii=False, indent=2).encode("utf-8")
            return text.encode("utf-8")
        if suffix in {".xml", ".runtime-xml"}:
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                # Malformed XML may hide encoded credentials; do not export it.
                return None
            for element in root.iter():
                if _private_key(element.tag.rsplit("}", 1)[-1]):
                    element.clear()
                    element.text = REDACTED
                elif element.text:
                    element.text = self.text(element.text)
                element.attrib = self.structured(element.attrib)
                if element.tail:
                    element.tail = self.text(element.tail)
            return ET.tostring(root, encoding="utf-8")
        if suffix == ".ini":
            text = _INI_SECRET.sub(lambda match: match["prefix"] + REDACTED, text)
        return self.text(text).encode("utf-8")


class DebugBundleArchive:
    """One export boundary for disk files, snapshots, histories, and metadata."""
    def __init__(self, archive: zipfile.ZipFile, redactor: DebugRedactor):
        self.archive, self.redactor = archive, redactor
        self.omitted: list[dict[str, str]] = []

    def writestr(self, name: str, data: bytes | str) -> bool:
        name = self.redactor.text(name)
        redacted = self.redactor.content(data, Path(name).suffix.lower())
        if redacted is None:
            self.omitted.append({"archive": name, "reason": "opaque_or_unsupported_content"})
            return False
        self.archive.writestr(name, redacted)
        return True

    def write(self, path: Path, name: str, *, raw_dump: bool = False) -> bool:
        if path.is_symlink():
            self.omitted.append({"archive": self.redactor.text(name), "reason": "symlink"})
            return False
        if raw_dump and path.suffix.lower() == ".dmp":
            self.archive.write(path, self.redactor.text(name))
            return True
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            self.omitted.append({"archive": self.redactor.text(name), "reason": "opaque_or_unsupported_content"})
            return False
        return self.writestr(name, path.read_bytes())
