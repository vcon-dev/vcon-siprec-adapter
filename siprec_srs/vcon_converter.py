"""
vCon converter that maps SIPREC data to vCon format.
"""

import logging
import base64
import json
import tempfile
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from pathlib import Path
from vcon import Vcon
from vcon.party import Party
from vcon.dialog import Dialog
from .rtp_handler import RTPHandler
from .config import LawfulBasisConfig
from .vcon_extensions import (
    add_lawful_basis_attachment,
    add_sip_message_trace,
    annotate_dialog_with_sip,
)

logger = logging.getLogger(__name__)


class VConConverter:
    """Converts SIPREC sessions to vCon format."""
    
    def __init__(self, lawful_basis_config: Optional[LawfulBasisConfig] = None):
        self.lawful_basis_config = lawful_basis_config or LawfulBasisConfig()

    def convert_session_to_vcon(self, session_data: Dict[str, Any],
                               rtp_handler: RTPHandler) -> Optional[Vcon]:
        """Convert a SIPREC session to vCon format."""
        try:
            vcon = Vcon.build_new()
            # build_new() does not set the syntax version; spec requires "0.4.0"
            vcon.vcon_dict["vcon"] = "0.4.0"

            vcon.add_tag("source", "siprec")
            vcon.add_tag("call_id", session_data.get('call_id', ''))
            vcon.add_tag("recording_session_id", session_data.get('recording_session_id', ''))
            vcon.add_tag("session_id", session_data.get('session_id', ''))
            vcon.add_tag("conversion_timestamp", datetime.now(timezone.utc).isoformat())

            self._add_participants(vcon, session_data.get('participants', []))
            self._add_audio_dialogs(vcon, session_data, rtp_handler)
            self._add_session_metadata_attachment(vcon, session_data)
            self._add_sip_signaling(vcon, session_data)
            self._add_lawful_basis(vcon, session_data)

            self._strip_default_empty_fields(vcon)

            # Note: vcon.is_valid() rejects extension attachments that legally
            # use `type:` (lawful_basis) instead of `purpose:`. Spec compliance
            # is enforced by the test suite; this method does not gate output
            # on the lib's validator.
            logger.info(f"Successfully converted session {session_data.get('session_id')} to vCon")
            return vcon
            
        except Exception as e:
            logger.error(f"Error converting session to vCon: {e}")
            return None
    
    def _add_participants(self, vcon: Vcon, participants: List[Dict[str, Any]]):
        """Add participants to the vCon.

        Only spec-defined Party fields (name, tel, mailto) are passed to the
        Party constructor. Non-spec hints (role, domain, uri, internal id)
        go into `party.meta` so they survive serialization without polluting
        spec-typed fields.
        """
        try:
            for participant in participants:
                party = Party(
                    name=participant.get('name', '') or None,
                    tel=participant.get('tel', '') or None,
                    mailto=participant.get('mailto', '') or None,
                )

                meta = {}
                for key in ('role', 'domain', 'uri', 'id'):
                    if participant.get(key):
                        meta[key] = participant[key]
                if meta:
                    party.meta = meta

                vcon.add_party(party)

        except Exception as e:
            logger.error(f"Error adding participants: {e}")
    
    def _add_audio_dialogs(self, vcon: Vcon, session_data: Dict[str, Any], 
                          rtp_handler: RTPHandler):
        """Add audio dialogs from captured RTP streams."""
        try:
            # Get audio files from RTP handler
            audio_files = rtp_handler.get_audio_files()
            
            if not audio_files:
                logger.warning("No audio files found for session")
                return
            
            # Get session timing
            start_time = session_data.get('start_time', datetime.now(timezone.utc).isoformat())
            end_time = session_data.get('end_time', datetime.now(timezone.utc).isoformat())
            
            participant_count = len(session_data.get('participants', []))
            all_party_indices = list(range(participant_count))

            # Sort streams for deterministic dialog ordering / party mapping.
            for stream_idx, (stream_id, audio_file_path) in enumerate(
                sorted(audio_files.items())
            ):
                if not Path(audio_file_path).exists():
                    logger.warning(f"Audio file not found: {audio_file_path}")
                    continue

                audio_data = self._read_audio_file(audio_file_path)
                if not audio_data:
                    continue

                mime_type = self._get_audio_mime_type(audio_file_path)
                duration = self._get_audio_duration(audio_file_path)

                # SIPREC streams are typically per-participant. If stream
                # count matches participant count, map 1:1; otherwise the
                # stream is treated as covering all parties (best effort
                # until the sip_signaling extension carries true mapping).
                if participant_count and len(audio_files) == participant_count:
                    parties_for_stream = [stream_idx]
                    originator = stream_idx
                else:
                    parties_for_stream = all_party_indices
                    originator = 0 if participant_count else None

                dialog_kwargs = dict(
                    type="recording",
                    start=start_time,
                    parties=parties_for_stream,
                    mediatype=mime_type,
                    body=audio_data,
                    encoding="base64url",
                    filename=Path(audio_file_path).name,
                )
                if duration is not None:
                    dialog_kwargs["duration"] = duration
                if originator is not None:
                    dialog_kwargs["originator"] = originator

                vcon.add_dialog(Dialog(**dialog_kwargs))

                # Stream provenance (RTP capture origin, raw stream id) lives
                # in an attachment, not on the Dialog object — Dialog has no
                # `metadata` field in the spec.
                dialog_index = len(vcon.dialog) - 1
                vcon.vcon_dict.setdefault("attachments", []).append({
                    "purpose": "stream_provenance",
                    "party": parties_for_stream[0] if parties_for_stream else 0,
                    "dialog": dialog_index,
                    "encoding": "json",
                    "body": json.dumps({
                        "stream_id": stream_id,
                        "source": "rtp_capture",
                    }),
                })
                
        except Exception as e:
            logger.error(f"Error adding audio dialogs: {e}")
    
    def _add_session_metadata_attachment(self, vcon: Vcon, session_data: Dict[str, Any]):
        """Add SIPREC session metadata as a vCon attachment.

        Per draft-ietf-vcon-vcon-core-02, structured metadata belongs in
        attachments[] (with `purpose`), not as a synthetic text dialog.
        """
        try:
            session_info = {
                'session_id': session_data.get('session_id'),
                'call_id': session_data.get('call_id'),
                'recording_session_id': session_data.get('recording_session_id'),
                'start_time': session_data.get('start_time'),
                'end_time': session_data.get('end_time'),
                'remote_uri': session_data.get('remote_uri'),
                'local_uri': session_data.get('local_uri'),
                'media_streams': session_data.get('media_streams', []),
                'source': 'siprec',
            }

            # The vcon lib's add_attachment() rejects encoding="json"; build
            # the attachment dict directly per the speckit guidance.
            vcon.vcon_dict.setdefault("attachments", []).append({
                "purpose": "session_metadata",
                "party": 0,
                "dialog": 0,
                "encoding": "json",
                "body": json.dumps(session_info),
            })

        except Exception as e:
            logger.error(f"Error adding session metadata attachment: {e}")
    
    def _strip_default_empty_fields(self, vcon: Vcon):
        """Remove lib-emitted default empty fields the spec discourages.

        Per the speckit: `group` is reserved (drop empty list), `redacted`
        should be omitted when empty (don't emit `{}`).
        """
        d = vcon.vcon_dict
        if d.get("group") == []:
            d.pop("group", None)
        if d.get("redacted") == {}:
            d.pop("redacted", None)

    def _add_sip_signaling(self, vcon: Vcon, session_data: Dict[str, Any]):
        """Emit sip-signaling extension data (draft-howe-vcon-sip-signaling).

        Adds a `sip-message-trace` attachment summarizing the SIPREC INVITE
        and stamps `sip_call_id` onto every recording Dialog.
        """
        try:
            call_id = session_data.get('call_id') or ''
            if not call_id:
                return  # nothing to emit

            add_sip_message_trace(
                vcon.vcon_dict,
                call_id=call_id,
                recording_session_id=session_data.get('recording_session_id'),
                remote_uri=session_data.get('remote_uri'),
                local_uri=session_data.get('local_uri'),
                media_streams=session_data.get('media_streams', []),
                start=session_data.get('start_time'),
            )

            # Stamp Dialog Object extension parameters on every recording.
            for dialog in vcon.dialog:
                if dialog.get('type') == 'recording':
                    annotate_dialog_with_sip(dialog, sip_call_id=call_id)

        except Exception as e:
            logger.error(f"Error adding sip_signaling extension data: {e}")

    def _add_lawful_basis(self, vcon: Vcon, session_data: Dict[str, Any]):
        """Emit a lawful_basis attachment per draft-howe-vcon-lawful-basis."""
        if not self.lawful_basis_config.enabled:
            return
        try:
            add_lawful_basis_attachment(
                vcon.vcon_dict,
                lawful_basis=self.lawful_basis_config.lawful_basis,
                purposes=self.lawful_basis_config.purposes,
                expiration=self.lawful_basis_config.expiration,
                justification=self.lawful_basis_config.justification,
                granted_at=session_data.get('start_time')
                or datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            logger.error(f"Error adding lawful_basis attachment: {e}")

    def _read_audio_file(self, file_path: str) -> Optional[str]:
        """Read audio file and return base64url-encoded data (per vCon spec)."""
        try:
            with open(file_path, 'rb') as f:
                audio_data = f.read()

            # vCon spec uses base64url (RFC 4648 §5), not standard base64.
            # urlsafe_b64encode produces base64url; strip padding per common usage.
            encoded_data = base64.urlsafe_b64encode(audio_data).decode('ascii')
            return encoded_data

        except Exception as e:
            logger.error(f"Error reading audio file {file_path}: {e}")
            return None
    
    def _get_audio_mime_type(self, file_path: str) -> str:
        """Get MIME type based on file extension."""
        extension = Path(file_path).suffix.lower()
        
        mime_types = {
            '.wav': 'audio/wav',
            '.mp3': 'audio/mpeg',
            '.ogg': 'audio/ogg',
            '.webm': 'audio/webm',
            '.m4a': 'audio/x-m4a',
            '.aac': 'audio/aac'
        }
        
        return mime_types.get(extension, 'audio/wav')
    
    def _get_audio_duration(self, file_path: str) -> Optional[float]:
        """Get audio duration in seconds."""
        try:
            import wave
            with wave.open(file_path, 'rb') as wav_file:
                frames = wav_file.getnframes()
                sample_rate = wav_file.getframerate()
                duration = frames / float(sample_rate)
                return duration
        except Exception as e:
            logger.warning(f"Could not determine audio duration for {file_path}: {e}")
            return None
    
    def merge_audio_streams(self, audio_files: Dict[str, str], 
                          output_path: str) -> Optional[str]:
        """Merge multiple audio streams into a single file."""
        try:
            from pydub import AudioSegment
            
            merged_audio = None
            
            for stream_id, file_path in audio_files.items():
                if not Path(file_path).exists():
                    continue
                
                # Load audio file
                audio = AudioSegment.from_wav(file_path)
                
                if merged_audio is None:
                    merged_audio = audio
                else:
                    # Mix with existing audio
                    merged_audio = merged_audio.overlay(audio)
            
            if merged_audio:
                # Export merged audio
                merged_audio.export(output_path, format="wav")
                return output_path
            
            return None
            
        except Exception as e:
            logger.error(f"Error merging audio streams: {e}")
            return None
    
    def create_summary_vcon(self, session_data: Dict[str, Any], 
                          rtp_handler: RTPHandler) -> Optional[Vcon]:
        """Create a summary vCon with merged audio."""
        try:
            # Create base vCon
            vcon = self.convert_session_to_vcon(session_data, rtp_handler)
            if not vcon:
                return None
            
            # Get audio files
            audio_files = rtp_handler.get_audio_files()
            
            if len(audio_files) > 1:
                # Merge audio streams
                temp_merged = tempfile.mktemp(suffix='_merged.wav')
                merged_file = self.merge_audio_streams(audio_files, temp_merged)
                
                if merged_file:
                    # Replace individual audio dialogs with merged one
                    self._replace_with_merged_audio(vcon, merged_file, session_data)
            
            return vcon
            
        except Exception as e:
            logger.error(f"Error creating summary vCon: {e}")
            return None
    
    def _replace_with_merged_audio(self, vcon: Vcon, merged_file: str, 
                                 session_data: Dict[str, Any]):
        """Replace individual audio dialogs with merged audio."""
        try:
            # Remove existing audio dialogs
            dialogs_to_remove = []
            for i, dialog_dict in enumerate(vcon.dialog):
                if dialog_dict.get('type') == 'recording':
                    dialogs_to_remove.append(i)
            
            # Remove in reverse order to maintain indices
            for i in reversed(dialogs_to_remove):
                vcon.dialog.pop(i)
            
            audio_data = self._read_audio_file(merged_file)
            if audio_data:
                start_time = session_data.get(
                    'start_time', datetime.now(timezone.utc).isoformat()
                )
                participant_count = len(session_data.get('participants', []))

                dialog = Dialog(
                    type="recording",
                    start=start_time,
                    parties=list(range(participant_count)),
                    mimetype="audio/wav",
                    body=audio_data,
                    encoding="base64url",
                    filename=Path(merged_file).name,
                    originator=0 if participant_count else None,
                )

                vcon.add_dialog(dialog)

                dialog_index = len(vcon.dialog) - 1
                vcon.vcon_dict.setdefault("attachments", []).append({
                    "purpose": "stream_provenance",
                    "party": 0,
                    "dialog": dialog_index,
                    "encoding": "json",
                    "body": json.dumps({
                        "kind": "merged_audio",
                        "source": "rtp_capture",
                    }),
                })
            
            # Clean up temporary file
            Path(merged_file).unlink(missing_ok=True)
            
        except Exception as e:
            logger.error(f"Error replacing with merged audio: {e}")
    
    def validate_vcon(self, vcon: Vcon) -> bool:
        """Validate a vCon object.

        Skips vcon.is_valid() because that validator rejects extension-defined
        attachments (e.g. lawful_basis attachments use `type:` per their
        draft, not `purpose:`).
        """
        try:
            if vcon.vcon_dict.get("vcon") != "0.4.0":
                logger.error("vCon syntax version is not 0.4.0")
                return False

            if not vcon.parties:
                logger.error("vCon has no parties")
                return False
            
            if not vcon.dialog:
                logger.error("vCon has no dialogs")
                return False
            
            # Check for at least one audio dialog
            has_audio = any(
                dialog.get('type') == 'recording' 
                for dialog in vcon.dialog
            )
            
            if not has_audio:
                logger.warning("vCon has no audio dialogs")
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating vCon: {e}")
            return False
