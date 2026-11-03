"""The session folder: how it is written, and how it is read back.

A session is a directory of Markdown files, and that is the whole of what
Agent Bridge remembers. Three ideas hold this module together. Publication
either happens or it does not: a message is written to a temporary file,
forced to the disk, and renamed into its canonical name, so a reader sees
either no file or a complete one. Numbering is derived, not remembered: the
next sequence is one higher than the highest number on disk. And the body is
inert, structurally: headers are read only from the block between the title
line and the first blank line; the text under `## Body` is never looked at.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from . import BRIDGE_FORMAT
from .errors import BridgeError, Failure
from .peer import stopped_by_signal

SESSION_FILENAME = "SESSION.md"
MESSAGES_DIRNAME = "messages"

#: The session formats this runtime reads. Format 2 is the original courier
#: shape and always reads as review mode; Format 3 adds the working-mode and
#: access-directory fields and the extended request headers. Bridge is the
#: only writer, so reading is loose about hand-edit noise and strict about
#: the facts the session's safety rests on (see `_session_fields`).
SUPPORTED_FORMATS = ("2", "3")
FORMAT_3 = "3"

#: The working modes a Format 3 session may record. A Format 2 session is
#: always read as review mode.
MODES = ("review", "work")

#: Every temporary file this module creates while publishing carries this
#: prefix, so a check can assert that none of them survived a failure.
TEMP_PREFIX = ".agent-bridge-publish-"

#: The three canonical message filename shapes.
INITIATOR_TO_PEER_SUFFIX = "-initiator-to-peer.md"
PEER_TO_INITIATOR_SUFFIX = "-peer-to-initiator.md"
INITIATOR_RECORD_SUFFIX = "-initiator-record.md"

BODY_HEADING = "## Body"

_SEQUENCE_PATTERN = re.compile(r"^(\d{4,})-")
_INITIATOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)


class SessionRecord(NamedTuple):
    """What `SESSION.md` said, read fresh from disk.

    Written once at creation and never changed: the inert initiator label,
    the target, the project when there is one, and - for Format 3 - the
    immutable working mode and declared access directories.
    """

    bridge_format: str
    initiator: str
    peer: str
    project: Optional[str]
    mode: str = "review"
    access_paths: Tuple[str, ...] = ()


# -- paths ------------------------------------------------------------------


def session_file(session_dir: str) -> str:
    return os.path.join(session_dir, SESSION_FILENAME)


def messages_dir(session_dir: str) -> str:
    return os.path.join(session_dir, MESSAGES_DIRNAME)


def format_sequence(sequence: int) -> str:
    """Sequence numbers are padded to at least four digits, and never fewer."""
    return "{0:04d}".format(sequence)


def message_path(session_dir: str, sequence: int, suffix: str) -> str:
    return os.path.join(
        messages_dir(session_dir), format_sequence(sequence) + suffix
    )


# -- atomic publication -----------------------------------------------------


def _fsync_directory(directory: str) -> None:
    """Force the directory entry itself out, so the rename survives a crash."""
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def publish(path: str, text: str) -> str:
    """Write one canonical file so that it is either whole or absent.

    The text goes into a task-owned temporary file beside the destination, is
    flushed and forced to disk, and only then renamed onto the canonical
    name; the rename is the moment of publication. A stop is deferred for
    the whole life of the temporary file - its creation, the writing, the
    rename, and the clearing away - so no stop can end the process while a
    temporary file exists with nothing arranging to clear it away.

    On a failure the temporary file is removed first; a removal that fails
    is `CLEANUP_FAILURE`, naming the leftover file, and outranks everything
    else. Then a deferred stop is raised as itself. Otherwise one
    `PUBLICATION_FAILURE` honestly covers both a rename that failed and one
    whose outcome could not be established: the message may not have been
    moved into place, and the caller is told to look for the file before
    running again. Forcing the folder entry out to the disk happens after
    all of that, outside the deferral; its failure is
    `PUBLICATION_NOT_FLUSHED`, because by then the message is published.
    """
    directory = os.path.dirname(os.path.abspath(path))
    with stopped_by_signal() as watch:
        with watch.deferring():
            try:
                handle, temp_path = tempfile.mkstemp(
                    prefix=TEMP_PREFIX, dir=directory
                )
            except OSError as exc:
                raise BridgeError(Failure.PUBLICATION_FAILURE, detail=str(exc))
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, path)
            except BaseException as exc:
                # The handling stays deferred throughout, so a stop landing
                # anywhere in here cannot abandon the tidying half done.
                removal = None  # type: Optional[OSError]
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
                except OSError as failed:
                    removal = failed
                if removal is not None:
                    raise BridgeError(
                        Failure.CLEANUP_FAILURE,
                        detail=(
                            "the temporary file {0} could not be removed after "
                            "publishing {1} failed ({2}): {3}".format(
                                temp_path, path, exc, removal
                            )
                        ),
                    )
                watch.raise_if_stopped()
                raise BridgeError(
                    Failure.PUBLICATION_FAILURE,
                    detail="{0}: {1}".format(path, exc),
                )
        # Nothing is left to abandon: a written-down stop is raised now, and
        # the flush below runs outside the deferral, because after it nothing
        # would ever read a written-down stop back.
        watch.raise_if_stopped()
        try:
            _fsync_directory(directory)
        except OSError as exc:
            raise BridgeError(
                Failure.PUBLICATION_NOT_FLUSHED,
                detail="{0}: {1}".format(path, exc),
            )
    return path


# -- envelopes --------------------------------------------------------------


def _compose(title: str, header_lines: Sequence[str], body: str) -> str:
    """Title, then the header block, then the body under its own heading."""
    header = "".join(line + "\n" for line in header_lines)
    return "{0}\n{1}\n{2}\n\n{3}".format(title, header, BODY_HEADING, body)


def session_text(
    initiator: str,
    peer: str,
    body: str,
    project: Optional[str] = None,
    mode: Optional[str] = None,
    access_paths: Sequence[str] = (),
) -> str:
    """`SESSION.md`, written once and never edited.

    Without `mode` this is the Format 2 session, byte for byte as before; a
    named mode writes Format 3 with the immutable working mode and access
    directories.
    """
    header_lines = [
        "Bridge-Format: {0}".format(BRIDGE_FORMAT if mode is None else FORMAT_3),
        "Initiator: {0}".format(initiator),
        "Peer: {0}".format(peer),
    ]
    if mode is not None:
        header_lines.append("Mode: {0}".format(mode))
    if project:
        header_lines.append("Project: {0}".format(project))
    for path in access_paths:
        header_lines.append("Access-Path: {0}".format(path))
    return "# Session\n" + _compose("", header_lines, body)


def initiator_to_peer_text(
    sequence: int,
    initiator: str,
    peer: str,
    body: str,
    note_ref: Optional[str] = None,
    purpose: Optional[str] = None,
    attachments: Sequence[str] = (),
) -> str:
    """A request going out to the peer, its optional inert metadata above the
    byte-exact body."""
    header_lines = ["From: {0}".format(initiator), "To: {0}".format(peer)]
    if note_ref is not None:
        header_lines.append("Note-Ref: {0}".format(note_ref))
    if purpose is not None:
        header_lines.append("Purpose: {0}".format(purpose))
    for attachment in attachments:
        header_lines.append("Attachment: {0}".format(attachment))
    return _compose(
        "# Message {0}".format(format_sequence(sequence)), header_lines, body
    )


def peer_to_initiator_text(
    sequence: int,
    peer: str,
    initiator: str,
    body: str,
    answers: Optional[int] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    effort: Optional[str] = None,
    requested_model: Optional[str] = None,
    requested_effort: Optional[str] = None,
) -> str:
    """A peer's final answer, copied through unchanged, with the reported
    identity, choices, and requested values as optional header lines only."""
    header_lines = ["From: {0}".format(peer), "To: {0}".format(initiator)]
    if answers is not None:
        header_lines.append("Answers: {0}".format(format_sequence(answers)))
    if model is not None:
        header_lines.append("Model: {0}".format(model))
    if effort is not None:
        header_lines.append("Effort: {0}".format(effort))
    if provider is not None:
        header_lines.append("Provider: {0}".format(provider))
    if requested_model is not None:
        header_lines.append("Requested-Model: {0}".format(requested_model))
    if requested_effort is not None:
        header_lines.append(
            "Requested-Effort: {0}".format(requested_effort)
        )
    return _compose(
        "# Message {0}".format(format_sequence(sequence)), header_lines, body
    )


def initiator_record_text(
    sequence: int,
    kind: str,
    initiator: str,
    body: str,
) -> str:
    """An application-neutral note: no recipient and no interpreted fields."""
    header_lines = ["Record: {0}".format(kind), "From: {0}".format(initiator)]
    return _compose(
        "# Message {0}".format(format_sequence(sequence)), header_lines, body
    )


# -- cold reading -----------------------------------------------------------


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _session_fields(text: str) -> Dict[str, object]:
    """Parse the session envelope for Format 2 or Format 3.

    Bridge is the only writer and validates everything it writes, so reading
    is loose about what only a hand edit could disturb and strict about the
    facts the session's safety rests on. Unrecognized header lines are
    ignored, repeated fields take the last value, spacing around names and
    values is tolerated, and either format's fields in either file are read
    by that file's own rules (a Format 2 session always reads as review
    mode with no access paths, whatever extra lines it carries). Refused:
    an unsupported `Bridge-Format`, a Format 3 file with no usable `Mode`,
    and - in `read_session` - a missing required field, an unknown target,
    and a non-absolute recorded directory. Nothing below the first blank
    line is ever read - a whitespace-only line ends the header too - so
    body text that looks like a header cannot change the session.
    """
    lines = _normalise(text).split("\n")
    if lines[0] != "# Session":
        raise BridgeError(
            Failure.SESSION_INVALID, detail="SESSION.md must start with # Session"
        )
    if len(lines) < 7 or lines[1] != "":
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="SESSION.md does not have the session header layout",
        )

    fields = {}  # type: Dict[str, object]
    known = {
        "Bridge-Format",
        "Initiator",
        "Peer",
        "Project",
        "Mode",
        "Access-Path",
    }
    access_paths = []  # type: List[str]
    index = 2
    while index < len(lines) and lines[index].strip():
        name, separator, value = lines[index].partition(":")
        name = name.strip()
        if separator and name in known:
            parsed = value.strip()
            if name == "Access-Path":
                if parsed:
                    access_paths.append(parsed)
            elif parsed:
                fields[name] = parsed
        index += 1

    declared = fields.get("Bridge-Format")
    if declared not in SUPPORTED_FORMATS:
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="unsupported Bridge-Format: {0}".format(declared),
        )
    if declared == FORMAT_3 and fields.get("Mode") not in MODES:
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="a Format 3 SESSION.md has no usable Mode line",
        )
    if declared != FORMAT_3:
        fields.pop("Mode", None)
        access_paths = []

    if index + 2 >= len(lines):
        raise BridgeError(
            Failure.SESSION_INVALID, detail="SESSION.md has no body"
        )
    if lines[index : index + 3] != ["", BODY_HEADING, ""]:
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="SESSION.md does not have the session body layout",
        )
    body = "\n".join(lines[index + 3 :])
    if not body.strip():
        raise BridgeError(
            Failure.SESSION_INVALID, detail="SESSION.md has an empty body"
        )
    if access_paths:
        fields["Access-Path"] = access_paths
    return fields


def validate_initiator(label: str, failure: Failure = Failure.USAGE_ERROR) -> str:
    """Return an inert ASCII initiator slug, or reject it in the caller's terms."""
    if not _INITIATOR_PATTERN.fullmatch(label):
        raise BridgeError(
            failure,
            detail="initiator must be an ASCII slug beginning with a letter or digit",
        )
    return label


def validate_inline_value(name: str, value: str) -> str:
    """Accept one inert single-line string a caller wants stored in a header."""
    if (
        not value
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or len(value) > 256
    ):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="{0} must be one nonempty line of at most 256 characters "
            "without surrounding whitespace".format(name),
        )
    return value


def _read_text(path: str, missing: Failure) -> str:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except FileNotFoundError:
        raise BridgeError(missing, detail=path)
    except (UnicodeDecodeError, OSError) as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))


def read_session(session_dir: str) -> SessionRecord:
    """Read `SESSION.md` back from disk, every time it is needed."""
    if not os.path.isdir(session_dir):
        raise BridgeError(Failure.SESSION_NOT_FOUND, detail=session_dir)
    text = _read_text(session_file(session_dir), Failure.SESSION_NOT_FOUND)
    fields = _session_fields(text)
    for required in ("Bridge-Format", "Initiator", "Peer"):
        if not fields.get(required):
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="SESSION.md has no {0} line".format(required),
            )
    validate_initiator(fields["Initiator"], failure=Failure.SESSION_INVALID)
    from .connectors import HARNESS_IDS

    if fields["Peer"] not in HARNESS_IDS:
        raise BridgeError(
            Failure.SESSION_INVALID,
            detail="SESSION.md names an unsupported peer: {0}".format(fields["Peer"]),
        )
    project = fields.get("Project") or None
    for path in ([project] if project is not None else []) + list(
        fields.get("Access-Path") or ()  # type: ignore[arg-type]
    ):
        if not os.path.isabs(path):
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="SESSION.md records a non-absolute directory: {0}".format(
                    path
                ),
            )
    if not os.path.isdir(messages_dir(session_dir)):
        raise BridgeError(
            Failure.SESSION_INVALID, detail=messages_dir(session_dir)
        )
    return SessionRecord(
        bridge_format=fields["Bridge-Format"],
        initiator=fields["Initiator"],
        peer=fields["Peer"],
        project=project,
        mode=fields.get("Mode") or "review",
        access_paths=tuple(fields.get("Access-Path") or ()),
    )


def next_sequence(session_dir: str) -> int:
    """One higher than the highest number on disk, worked out by looking."""
    try:
        names = os.listdir(messages_dir(session_dir))
    except OSError as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))
    highest = 0
    for name in names:
        match = _SEQUENCE_PATTERN.match(name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1
