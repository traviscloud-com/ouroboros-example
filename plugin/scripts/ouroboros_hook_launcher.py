#!/usr/bin/env python3
"""Ouroboros hook launcher — invoked by the plugin's hooks for every event
listed in hooks.json (SessionStart ... Stop); the SDK dispatches on
`hook_event_name`.

Delegates to the ouroboros-client SDK's hook entrypoint when installed and is
completely inert otherwise. Contract: stdlib-only, zero network, no output,
exit 0 under every failure mode — an installed plugin must never be able to
disrupt a Claude Code session (design D1/D2 of add-claude-plugin).
"""

import sys


def main() -> int:
    try:
        from ouroboros_client.hook import main as hook_main

        return hook_main()
    except BaseException:
        return 0


if __name__ == "__main__":
    sys.exit(main())
