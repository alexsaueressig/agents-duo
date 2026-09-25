#!/usr/bin/env python3
"""Install the agents-duo skills for Claude Code and/or Codex.

    python install.py              # both (~/.claude/skills and ~/.codex/skills)
    python install.py --claude     # Claude Code only
    python install.py --codex      # Codex only
    python install.py --dest DIR   # any other skills directory

Shared files (agents_bus.py, protocol.md) live in skills/duo-orchestrator and are
synced into skills/duo-assistant and skills/duo-start first, so each installed skill
is self-contained.
"""
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILLS = ROOT / "skills"
NAMES = ("duo-orchestrator", "duo-assistant", "duo-start")
SHARED = ("scripts/agents_bus.py", "references/protocol.md")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.tmp")


def sync_shared():
    src = SKILLS / "duo-orchestrator"
    for name in NAMES[1:]:
        dst = SKILLS / name
        for rel in SHARED:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dst / rel)


def install(target: Path):
    target.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        dst = target / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(SKILLS / name, dst, ignore=IGNORE)
        print(f"installed {name} -> {dst}")


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

    sync_shared()
    for t in targets:
        install(t)


if __name__ == "__main__":
    main()
