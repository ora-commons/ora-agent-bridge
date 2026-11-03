"""Create one courier session or add one neutral note without calling a target.

This is the only local writer besides the runner; both record kinds use the
same validation, session lock, sequence allocation, envelopes, and atomic
publication as a target call.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import os
from typing import Optional, Sequence, Tuple

from . import session as session_module
from .connectors import HARNESS_IDS
from .errors import BridgeError, Failure
from .locking import session_lock

RECORD_KINDS = ("session-create", "note")


def _require(value: Optional[str], what: str) -> str:
    if not value:
        raise BridgeError(
            Failure.USAGE_ERROR, detail="{0} is required".format(what)
        )
    return value


def _require_peer(value: Optional[str]) -> str:
    identifier = _require(value, "--peer")
    if identifier not in HARNESS_IDS:
        raise BridgeError(Failure.UNKNOWN_HARNESS, detail=identifier)
    return identifier


def _directory_path(option: str, path: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    if not os.path.isabs(path):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="{0} must be an absolute existing directory".format(option),
        )
    if not os.path.isdir(path):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="{0} is not an existing directory: {1}".format(option, path),
        )
    if "\n" in path or "\r" in path or path != path.strip():
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="{0} cannot contain line breaks or surrounding whitespace".format(
                option
            ),
        )
    return path


def _access_paths(paths: Optional[Sequence[str]]) -> Tuple[str, ...]:
    """Validate every declared access directory, refusing repeats."""
    if paths is None:
        return ()
    validated = []
    for path in paths:
        checked = _directory_path("--access-path", path)
        if checked in validated:
            raise BridgeError(
                Failure.USAGE_ERROR,
                detail="--access-path repeats the directory {0}".format(checked),
            )
        validated.append(checked)
    return tuple(validated)


def _create_session(
    session_dir: str,
    body: str,
    initiator: Optional[str],
    peer: Optional[str],
    project: Optional[str],
    mode: Optional[str] = None,
    access_paths: Optional[Sequence[str]] = None,
) -> str:
    """Write the immutable session record and allocate no number.

    Without `mode` this is the Format 2 session exactly as before; a named
    mode writes Format 3 and may record access directories and no project.
    """
    initiator_label = session_module.validate_initiator(
        _require(initiator, "--initiator")
    )
    peer_id = _require_peer(peer)
    project_directory = _directory_path("--project", project)
    if access_paths and mode is None:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--access-path requires an explicit --mode session",
        )
    declared_access = _access_paths(access_paths)
    try:
        os.makedirs(session_module.messages_dir(session_dir), exist_ok=True)
    except OSError as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))
    with session_lock(session_dir):
        if os.path.exists(session_module.session_file(session_dir)):
            raise BridgeError(Failure.SESSION_EXISTS, detail=session_dir)
        return session_module.publish(
            session_module.session_file(session_dir),
            session_module.session_text(
                initiator_label,
                peer_id,
                body,
                project=project_directory,
                mode=mode,
                access_paths=declared_access,
            ),
        )


def _publish_note(
    session_dir: str, record: "session_module.SessionRecord", body: str
) -> str:
    sequence = session_module.next_sequence(session_dir)
    return session_module.publish(
        session_module.message_path(
            session_dir, sequence, session_module.INITIATOR_RECORD_SUFFIX
        ),
        session_module.initiator_record_text(
            sequence, "note", record.initiator, body
        ),
    )


def record(
    session_dir: str,
    kind: str,
    body: str,
    initiator: Optional[str] = None,
    peer: Optional[str] = None,
    project: Optional[str] = None,
    mode: Optional[str] = None,
    access_paths: Optional[Sequence[str]] = None,
) -> str:
    """Create a session or add a note, returning the canonical path."""
    if kind not in RECORD_KINDS:
        raise BridgeError(Failure.UNKNOWN_RECORD_KIND, detail=kind)
    if not body or not body.strip():
        raise BridgeError(Failure.USAGE_ERROR, detail="the record body was empty")

    if kind == "session-create":
        return _create_session(
            session_dir, body, initiator, peer, project, mode, access_paths
        )

    if (
        initiator is not None
        or peer is not None
        or project is not None
        or mode is not None
        or access_paths is not None
    ):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="note accepts no --initiator, --peer, --project, --mode, "
            "or --access-path argument",
        )
    session_record = session_module.read_session(session_dir)
    with session_lock(session_dir):
        return _publish_note(session_dir, session_record, body)
