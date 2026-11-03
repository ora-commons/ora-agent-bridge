"""Ora Agent Bridge core package: the source and session-format versions.

SPDX-License-Identifier: CC0-1.0
"""

#: Version of the on-disk session record format (`Bridge-Format:` in SESSION.md).
BRIDGE_FORMAT = 2

#: Release version of this source tree. This is release 1.7.0.
VERSION = "1.7.0"

__version__ = VERSION

__all__ = ["BRIDGE_FORMAT", "VERSION", "__version__"]
