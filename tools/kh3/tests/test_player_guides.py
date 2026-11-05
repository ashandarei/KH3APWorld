"""Every local link from a packaged player guide must also ship in the APWorld."""
from pathlib import Path
import re
from urllib.parse import unquote

import pathspec

ROOT = Path(__file__).resolve().parents[3] / "worlds/kh3"


def test_player_guides_are_self_contained():
    ignored = pathspec.GitIgnoreSpec.from_lines((ROOT / ".apignore").read_text(encoding="utf-8").splitlines())
    guides = [path for path in (ROOT / "docs").glob("*.md")
              if not ignored.match_file(path.relative_to(ROOT).as_posix())]
    assert guides
    for guide in guides:
        for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", guide.read_text(encoding="utf-8")):
            target = unquote(link.split("#", 1)[0].strip("<>"))
            if not target or re.match(r"[a-z]+:", target):
                continue
            assert not target.startswith("/"), (guide.name, target)
            path = (guide.parent / target).resolve()
            assert path.is_relative_to(ROOT.resolve()), (guide.name, target)
            assert path.is_file(), (guide.name, target)
            assert not ignored.match_file(path.relative_to(ROOT).as_posix()), (guide.name, target)
