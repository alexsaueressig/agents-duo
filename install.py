#!/usr/bin/env python3
"""Install the agents-duo skills for Claude Code and/or Codex.

    python install.py              # both (~/.claude/skills and ~/.codex/skills)
    python install.py --claude     # Claude Code only
    python install.py --codex      # Codex only
    python install.py --dest DIR   # any other skills directory

Shared files (agents_bus.py, protocol.md) live in skills/duo-orchestrator and are
synced into skills/duo-assistant first, so each installed skill is self-contained.
duo-start is deprecated: it is still installed, as a stub that overwrites older copies.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILLS = ROOT / "skills"
NAMES = ("duo-orchestrator", "duo-assistant", "duo-start")
SHARED = ("scripts/agents_bus.py", "references/protocol.md")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.tmp")


def sync_shared():
    src = SKILLS / "duo-orchestrator"
    missing = [rel for rel in SHARED if not (src / rel).is_file()]
    if missing:
        sys.exit(f"ERROR: missing shared files in {src}: {', '.join(missing)}")
    for name in ("duo-assistant",):
        dst = SKILLS / name
        for rel in SHARED:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            tmp = (dst / rel).with_name((dst / rel).name + ".tmp")
            shutil.copy2(src / rel, tmp)
            os.replace(tmp, dst / rel)  # atomic per file: never a half-written copy


def install(target: Path):
    target.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        dst, new, old = target / name, target / f"{name}.new", target / f"{name}.old"
        for p in (new, old):
            if p.exists():
                shutil.rmtree(p)
        shutil.copytree(SKILLS / name, new, ignore=IGNORE)  # copy fully first; the old install stays intact
        if dst.exists():
            dst.rename(old)
        new.rename(dst)
        if old.exists():
            shutil.rmtree(old)
        print(f"installed {name} -> {dst}")


def check_target(target: Path):
    t = target.resolve()
    if t == SKILLS or SKILLS in t.parents:
        sys.exit(f"ERROR: {target} is inside this repo's skills/ folder; installing there would overwrite the sources.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--claude", action="store_true", help="install into ~/.claude/skills")
    p.add_argument("--codex", action="store_true", help="install into ~/.codex/skills")
    p.add_argument("--dest", action="append", default=[], help="extra skills directory (repeatable)")
    a = p.parse_args()

    targets = [Path(d).expanduser() for d in a.dest]
    if a.claude or not (a.codex or a.dest):
        targets.append(Path.home() / ".claude" / "skills")
    if a.codex or not (a.claude or a.dest):
        targets.append(Path.home() / ".codex" / "skills")

    for t in targets:
        check_target(t)
    sync_shared()
    for t in targets:
        install(t)


if __name__ == "__main__":
    main()
