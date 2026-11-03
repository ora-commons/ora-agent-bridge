"""Let the package be run directly: `python3 -m ora_agent_bridge <command> ...`.

SPDX-License-Identifier: CC0-1.0
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
