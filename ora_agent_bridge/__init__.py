"""Ora Agent Bridge core package.

Ora Agent Bridge connects coding-agent harnesses through their official callable
command-line interfaces. This module exposes the source release and on-disk
session-format versions. It imports nothing, spawns nothing, and does no work
when it is imported.

SPDX-License-Identifier: CC0-1.0
"""

#: Version of the on-disk session record format. `SESSION.md` records it as
#: `Bridge-Format:` so a later reader can tell which layout it is looking at.
BRIDGE_FORMAT = 2

#: Release version of this source tree. This is release 1.6.0.
VERSION = "1.6.0"

__version__ = VERSION

__all__ = ["BRIDGE_FORMAT", "VERSION", "__version__"]
