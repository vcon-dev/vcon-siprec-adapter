"""
Helpers that emit vCon extension data per the howe-vcon-* drafts.

Each helper appends to `vcon.vcon_dict` directly rather than going through
the `vcon` library's typed APIs, because:

  * The `add_attachment()` helper rejects `encoding="json"` (lib quirk
    documented in the global CLAUDE.md).
  * Extension-defined attachment shapes (e.g. lawful_basis using `type:`
    instead of `purpose:`) don't fit the lib's core attachment model.

Always declare the extension in the top-level `extensions[]` list.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# extensions[] declaration
# ---------------------------------------------------------------------------

def declare_extension(vcon_dict: Dict[str, Any], name: str) -> None:
    """Add `name` to the vCon's top-level `extensions[]` if not present."""
    extensions = vcon_dict.setdefault("extensions", [])
    if name not in extensions:
        extensions.append(name)


# ---------------------------------------------------------------------------
# sip-signaling extension (draft-howe-vcon-sip-signaling)
# ---------------------------------------------------------------------------

SIP_SIGNALING_EXTENSION = "sip-signaling"


def add_sip_message_trace(
    vcon_dict: Dict[str, Any],
    *,
    call_id: str,
    recording_session_id: Optional[str] = None,
    remote_uri: Optional[str] = None,
    local_uri: Optional[str] = None,
    media_streams: Optional[List[Dict[str, Any]]] = None,
    dialog_index: int = 0,
    party_index: int = 0,
    start: Optional[str] = None,
) -> None:
    """Emit a `sip-message-trace` attachment summarizing SIPREC signaling.

    The sip-signaling draft defines `sip-invite`/`sip-response` for raw
    messages and `sip-message-trace` for parsed-summary form. We don't
    have raw SIP messages from pjsua2's CallInfo, so the trace form is
    the right fit.
    """
    declare_extension(vcon_dict, SIP_SIGNALING_EXTENSION)

    body = {
        "version": "1.0",
        "call_id": call_id,
        "recording_session_id": recording_session_id,
        "remote_uri": remote_uri,
        "local_uri": local_uri,
        "media_streams": media_streams or [],
    }

    attachment = {
        "purpose": "sip-message-trace",
        "party": party_index,
        "dialog": dialog_index,
        "mediatype": "application/json",
        "encoding": "json",
        "body": json.dumps(body),
    }
    if start:
        attachment["start"] = start

    vcon_dict.setdefault("attachments", []).append(attachment)


def annotate_dialog_with_sip(
    dialog: Dict[str, Any],
    *,
    sip_call_id: Optional[str] = None,
    sip_from_tag: Optional[str] = None,
    sip_to_tag: Optional[str] = None,
    sip_cseq: Optional[int] = None,
) -> None:
    """Stamp the SIP dialog identifiers from the sip-signaling extension
    onto a Dialog Object dict (per draft §Dialog Object Extension Parameters).
    """
    if sip_call_id is not None:
        dialog["sip_call_id"] = sip_call_id
    if sip_from_tag is not None:
        dialog["sip_from_tag"] = sip_from_tag
    if sip_to_tag is not None:
        dialog["sip_to_tag"] = sip_to_tag
    if sip_cseq is not None:
        dialog["sip_cseq"] = sip_cseq


# ---------------------------------------------------------------------------
# lawful_basis extension (draft-howe-vcon-lawful-basis)
# ---------------------------------------------------------------------------

LAWFUL_BASIS_EXTENSION = "lawful_basis"

VALID_LAWFUL_BASES = {
    "consent",
    "contract",
    "legal_obligation",
    "vital_interests",
    "public_task",
    "legitimate_interests",
}


def add_lawful_basis_attachment(
    vcon_dict: Dict[str, Any],
    *,
    lawful_basis: str,
    purposes: Iterable[str] = ("recording",),
    expiration: Optional[str] = None,
    party_index: int = 0,
    dialog_index: int = 0,
    granted_at: Optional[str] = None,
    justification: Optional[str] = None,
) -> None:
    """Append a `type: "lawful_basis"` attachment per draft-howe-vcon-lawful-basis.

    The lawful_basis attachment uses `type:` not `purpose:` — this is the
    documented exception to the core "use purpose" rule (see global CLAUDE.md).
    """
    if lawful_basis not in VALID_LAWFUL_BASES:
        raise ValueError(
            f"Invalid lawful_basis {lawful_basis!r}; must be one of "
            f"{sorted(VALID_LAWFUL_BASES)}"
        )

    declare_extension(vcon_dict, LAWFUL_BASIS_EXTENSION)

    granted_at = granted_at or datetime.now(timezone.utc).isoformat()

    purpose_grants = [
        {
            "purpose": purpose,
            "granted": True,
            "granted_at": granted_at,
        }
        for purpose in purposes
    ]

    body: Dict[str, Any] = {
        "lawful_basis": lawful_basis,
        "expiration": expiration,
        "purpose_grants": purpose_grants,
    }
    if justification:
        body["justification"] = justification

    attachment = {
        "type": "lawful_basis",
        "party": party_index,
        "dialog": dialog_index,
        "encoding": "json",
        "body": json.dumps(body),
        "start": granted_at,
    }
    vcon_dict.setdefault("attachments", []).append(attachment)
