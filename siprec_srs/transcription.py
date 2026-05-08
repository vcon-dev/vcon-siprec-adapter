"""Pluggable transcription pipeline.

Per draft-howe-vcon-wtf-extension, transcripts go in `analysis[]` (NOT
`attachments[]`) with:
  * `vendor` — REQUIRED, identifies the ASR (e.g. "openai-whisper").
  * `product` — model name.
  * `schema` — WTF draft URL.
  * `encoding: "json"` and `body = json.dumps(wtf_doc)`.
  * `dialog: [<index>]` of the recording dialog this transcribes.

The top-level `extensions[]` list MUST include `"wtf_transcription"`.

This module defines a Protocol so adapters can plug in any ASR vendor
without changing the converter. The default `NoopTranscriptionProvider`
disables transcription cleanly. A real implementation (Whisper, Deepgram,
etc.) lives in a separate module / future PR.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .vcon_extensions import declare_extension

logger = logging.getLogger(__name__)

WTF_TRANSCRIPTION_EXTENSION = "wtf_transcription"
WTF_SCHEMA_URL = (
    "https://datatracker.ietf.org/doc/html/draft-howe-vcon-wtf-extension"
)


@runtime_checkable
class TranscriptionProvider(Protocol):
    """A pluggable ASR backend.

    `transcribe(audio_path)` returns a WTF-shaped JSON-serializable dict
    or `None` if transcription was skipped or failed. Implementations are
    expected to be synchronous-from-the-converter's-POV; long-running
    network calls should be performed in a thread or pre-staged.
    """

    vendor: str
    product: str

    def transcribe(self, audio_path: str) -> Optional[Dict[str, Any]]:
        ...


class NoopTranscriptionProvider:
    """Default: no transcription is performed. Used when none configured."""

    vendor = "none"
    product = "none"

    def transcribe(self, audio_path: str) -> Optional[Dict[str, Any]]:
        return None


def add_transcription_analysis(
    vcon_dict: Dict[str, Any],
    *,
    provider: TranscriptionProvider,
    dialog_index: int,
    audio_path: str,
) -> bool:
    """Run `provider` against `audio_path`, attach as `analysis[]` entry.

    Returns True iff an analysis entry was added. Declares the
    `wtf_transcription` extension only when at least one analysis is added.
    """
    if isinstance(provider, NoopTranscriptionProvider):
        return False

    try:
        wtf_doc = provider.transcribe(audio_path)
    except Exception as e:
        logger.error(f"Transcription provider {provider.vendor!r} failed for "
                     f"{audio_path}: {e}")
        return False

    if not wtf_doc:
        return False

    declare_extension(vcon_dict, WTF_TRANSCRIPTION_EXTENSION)

    analysis_entry = {
        "type": "transcript",
        "vendor": provider.vendor,
        "product": provider.product,
        "schema": WTF_SCHEMA_URL,
        "dialog": [dialog_index],
        "encoding": "json",
        "body": json.dumps(wtf_doc),
    }
    vcon_dict.setdefault("analysis", []).append(analysis_entry)
    return True
