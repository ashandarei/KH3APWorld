"""On-demand, bounded collection of player-build KH0.2 movie error evidence."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..kh02.installation import EXE


def capture_movie_evidence(win64: Path) -> tuple[dict, dict[str, str]]:
    files: dict[str, str] = {}
    summary: dict = {"files": {}, "provider_result_matches_failure": None}

    def capture(path: Path, archive: str, limit: int = 256 * 1024) -> dict | None:
        details = {"path": str(path), "archive": archive}
        summary["files"][archive] = details
        try:
            with path.open("rb") as stream:
                raw = stream.read(limit + 1)
                details["mtime_utc"] = datetime.fromtimestamp(
                    os.fstat(stream.fileno()).st_mtime, timezone.utc).isoformat()
            if len(raw) > limit:
                details["status"] = "too_large"
                return None
            value = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("Expected a JSON object")
            files[archive] = json.dumps(value, ensure_ascii=False, indent=2)
            details["status"] = "captured"
            return value
        except FileNotFoundError:
            details["status"] = "missing"
        except (OSError, ValueError) as error:
            details.update(status="unavailable", error=str(error))
        return None

    failure = capture(win64 / "kh02_movie_failure.json", "kh02/host_movie_failure.json")
    config = capture(win64 / "kh02_portal_config.json", "kh02/portal_config.json", 16 * 1024)
    if not config or not isinstance(config.get("source_executable"), str):
        return summary, files
    executable_text = config["source_executable"]
    # The installer maps Linux absolute paths through Wine's Z: drive.
    if os.name != "nt" and executable_text[:3].casefold() in ("z:\\", "z:/"):
        executable_text = executable_text[2:].replace("\\", "/")
    executable = Path(executable_text)
    if not executable.is_absolute() or executable.name.casefold() != EXE.casefold():
        summary["source_status"] = "unsupported_source_path"
        return summary, files
    # Resolve only the selected provider's known result. Never scan its saves,
    # search other installations, or execute anything from its configuration.
    result = capture(executable.parent / "kh02_bridge/movie_probe_result.json",
                     "kh02/provider_movie_result.json")
    if failure and result:
        summary["provider_result_matches_failure"] = (
            failure.get("schema") == "kh3ap.kh02_movie_failure.v1"
            and bool(failure.get("control_request_id"))
            and str(result.get("id")) == failure["control_request_id"]
            and result.get("pid") == failure.get("source_pid")
        )
    return summary, files
