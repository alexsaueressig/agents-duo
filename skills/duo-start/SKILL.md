---
name: duo-start
description: DEPRECATED. /duo-orchestrator now starts a fresh duo session by itself. Use it when the user types /duo-start or $duo-start, only to point them to /duo-orchestrator.
---

# duo-start is deprecated

Tell the user in one or two lines:

- `/duo-orchestrator` (or `$duo-orchestrator`) now starts a fresh session by itself. It clears the old `.agents-duo/` whenever no assistant in it is still active, and reports what it cleared.
- To keep the old session, start the orchestrator with `--keep`. To wipe it by hand, run `python <duo-orchestrator>/scripts/agents_bus.py reset`.

Don't run anything yourself.
