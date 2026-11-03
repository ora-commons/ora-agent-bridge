"""The one internal list of ways an Ora Agent Bridge turn can fail.

Connectors map vendor behavior into exactly one of these members; only the
runner turns a member into words, so every failure reads the same way: what
happened, and the one thing to do next. The names are not a public surface.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import enum
from typing import Dict, Optional, Tuple


class Failure(enum.Enum):
    """Every way an Ora Agent Bridge turn is allowed to fail."""

    # Reaching and qualifying a peer harness.
    MISSING_CLI = "MISSING_CLI"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    UNREPORTABLE_VERSION = "UNREPORTABLE_VERSION"
    RESTRICTIONS_UNAVAILABLE = "RESTRICTIONS_UNAVAILABLE"

    # Running one bounded turn.
    BUSY_SESSION = "BUSY_SESSION"
    TIMEOUT = "TIMEOUT"
    PEER_FAILURE = "PEER_FAILURE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    CLEANUP_FAILURE = "CLEANUP_FAILURE"

    # Calling the commands correctly.
    USAGE_ERROR = "USAGE_ERROR"
    UNKNOWN_HARNESS = "UNKNOWN_HARNESS"
    UNKNOWN_RECORD_KIND = "UNKNOWN_RECORD_KIND"

    # Reading and writing the session record.
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_INVALID = "SESSION_INVALID"
    SESSION_EXISTS = "SESSION_EXISTS"
    PUBLICATION_FAILURE = "PUBLICATION_FAILURE"
    PUBLICATION_NOT_FLUSHED = "PUBLICATION_NOT_FLUSHED"


# For each failure: what happened, then the single next action.
_GUIDANCE: Dict[Failure, Tuple[str, str]] = {
    Failure.MISSING_CLI: (
        "The peer harness's command-line program could not be found on this "
        "computer.",
        "Install that harness's official command-line program, make sure it is "
        "on PATH, and run the readiness check again.",
    ),
    Failure.AUTHENTICATION_REQUIRED: (
        "The peer harness is installed but is not signed in.",
        "Sign in using that harness's own login command, then run the readiness "
        "check again.",
    ),
    Failure.UNREPORTABLE_VERSION: (
        "The peer harness's command-line program did not print a version that "
        "could be read, so there is no way to tell whether it is a tested one.",
        "Run that program's version command by hand; if it still prints nothing "
        "readable, this harness cannot be qualified and must not be used on a "
        "real project.",
    ),
    Failure.RESTRICTIONS_UNAVAILABLE: (
        "The peer harness does not offer a switch or one-shot input/output "
        "mechanic required by Ora Agent Bridge's fixed invocation.",
        "Inspect the named missing mechanic and update or correct the harness "
        "before checking it again.",
    ),
    Failure.BUSY_SESSION: (
        "Another Ora Agent Bridge turn is already holding this session's lock.",
        "Wait for that turn to finish and run the command again; nothing in the "
        "session was changed.",
    ),
    Failure.TIMEOUT: (
        "A bounded readiness check or a caller's bounded wait ended before "
        "the peer produced an answer; the work itself has no deadline and a "
        "wait never stopped it.",
        "Inspect the retained session evidence named in the detail - the "
        "request, any captured partial output, and the peer session "
        "reference - then collect the answer or run the turn again when "
        "you can wait for it.",
    ),
    Failure.PEER_FAILURE: (
        "The peer harness's program exited with a failure.",
        "Read the peer's own error output, fix the problem inside that harness, "
        "then run the turn again.",
    ),
    Failure.EMPTY_RESPONSE: (
        "The peer produced no text at all, so there is nothing to publish.",
        "Check the peer harness by hand, then run the turn again.",
    ),
    Failure.CLEANUP_FAILURE: (
        "A file or process this turn created could not be removed, so the turn "
        "cannot be called finished.",
        "Remove the reported path or process by hand and confirm nothing of "
        "this turn is still running before continuing.",
    ),
    Failure.USAGE_ERROR: (
        "The command was called with missing, conflicting, or empty arguments.",
        "Correct the command line, including the text supplied on standard "
        "input, and run it again.",
    ),
    Failure.UNKNOWN_HARNESS: (
        "The named target is not one of the six Ora Agent Bridge knows about.",
        "Name one of: codex, claude, zcode, hermes, minimax, qwen.",
    ),
    Failure.UNKNOWN_RECORD_KIND: (
        "That record kind is not one of the two the record command accepts.",
        "Name one of: session-create, note.",
    ),
    Failure.SESSION_NOT_FOUND: (
        "There is no session record at the given directory.",
        "Create the session first with record --kind session-create, or correct "
        "the --session path.",
    ),
    Failure.SESSION_INVALID: (
        "The session directory exists, but its record could not be read as a "
        "valid session.",
        "Open the session folder and inspect SESSION.md and messages/; repair "
        "it, or start a new session.",
    ),
    Failure.SESSION_EXISTS: (
        "A session record already exists at that directory, and creating "
        "another one there would overwrite it.",
        "Continue in the existing session, or choose a new empty --session "
        "directory.",
    ),
    Failure.PUBLICATION_FAILURE: (
        "The message could not be written in full and moved into place, so "
        "it may not have arrived at its recorded name.",
        "Look in the session's messages folder for the reported file before "
        "running the command again: if it is there the message is complete; "
        "if it is absent, check that the session directory is writable and "
        "has free space, then run the command again.",
    ),
    Failure.PUBLICATION_NOT_FLUSHED: (
        "The message was written and moved into place, so it is published, but "
        "the folder entry naming it could not be forced onto the disk, so a "
        "machine failure could still lose it.",
        "Confirm the reported file is there and readable, and treat this turn "
        "as unfinished until the session directory's disk is behaving.",
    ),
}


def render(failure: Failure, detail: Optional[str] = None) -> str:
    """One failure as the message a person reads: reason, then next action."""
    reason, next_action = _GUIDANCE[failure]
    if detail:
        reason = "{0} ({1})".format(reason, detail)
    return "{0} Next action: {1}".format(reason, next_action)


def guidance(failure: Failure) -> Tuple[str, str]:
    """The plain reason and the single next action for one failure."""
    return _GUIDANCE[failure]


class BridgeError(Exception):
    """One failure, carried out to whoever renders the message."""

    def __init__(self, failure: Failure, detail: Optional[str] = None) -> None:
        super().__init__(render(failure, detail))
        self.failure = failure
        self.detail = detail
