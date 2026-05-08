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
from .siprec_parser import SIPRECParser
from .rtp_handler import RTPHandler

logger = logging.getLogger(__name__)


class VConConverter:
    """Converts SIPREC sessions to vCon format."""
    
    def __init__(self):
        self.siprec_parser = SIPRECParser()
    
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
            
            # Validate the vCon
            is_valid, errors = vcon.is_valid()
            if not is_valid:
                logger.error(f"Generated invalid vCon: {errors}")
                return None
            
            logger.info(f"Successfully converted session {session_data.get('session_id')} to vCon")
            return vcon
            
        except Exception as e:
            logger.error(f"Error converting session to vCon: {e}")
            return None
    
    def _add_participants(self, vcon: Vcon, participants: List[Dict[str, Any]]):
        """Add participants to the vCon."""
        try:
            for participant in participants:
                party = Party(
                    name=participant.get('name', ''),
                    tel=participant.get('tel', ''),
                    mailto=participant.get('mailto', ''),
                    role=participant.get('role', 'participant'),
                    uuid=participant.get('id', '')
                )
                
                # Add custom metadata
                if participant.get('domain'):
                    party.meta = party.meta or {}
                    party.meta['domain'] = participant['domain']
                
                if participant.get('uri'):
                    party.meta = party.meta or {}
                    party.meta['uri'] = participant['uri']
                
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
            
            # Add each audio stream as a dialog
            for stream_id, audio_file_path in audio_files.items():
                if not Path(audio_file_path).exists():
                    logger.warning(f"Audio file not found: {audio_file_path}")
                    continue
                
                # Read and encode audio file
                audio_data = self._read_audio_file(audio_file_path)
                if not audio_data:
                    continue
                
                # Determine MIME type based on file extension
                mime_type = self._get_audio_mime_type(audio_file_path)
                
                # Create audio dialog
                dialog = Dialog(
                    type="recording",
                    start=start_time,
                    parties=list(range(len(session_data.get('participants', [])))),
                    mimetype=mime_type,
                    body=audio_data,
                    encoding="base64url",
                    filename=Path(audio_file_path).name
                )
                
                # Add stream metadata
                dialog.metadata = dialog.metadata or {}
                dialog.metadata['stream_id'] = stream_id
                dialog.metadata['source'] = 'rtp_capture'
                
                # Add duration if available
                duration = self._get_audio_duration(audio_file_path)
                if duration:
                    dialog.metadata['duration'] = duration
                
                vcon.add_dialog(dialog)
                
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
            
            # Add merged audio dialog
            audio_data = self._read_audio_file(merged_file)
            if audio_data:
                start_time = session_data.get('start_time', datetime.now(timezone.utc).isoformat())
                
                dialog = Dialog(
                    type="recording",
                    start=start_time,
                    parties=list(range(len(session_data.get('participants', [])))),
                    mimetype="audio/wav",
                    body=audio_data,
                    encoding="base64url",
                    filename=Path(merged_file).name
                )
                
                dialog.metadata = dialog.metadata or {}
                dialog.metadata['type'] = 'merged_audio'
                dialog.metadata['source'] = 'rtp_capture'
                
                vcon.add_dialog(dialog)
            
            # Clean up temporary file
            Path(merged_file).unlink(missing_ok=True)
            
        except Exception as e:
            logger.error(f"Error replacing with merged audio: {e}")
    
    def validate_vcon(self, vcon: Vcon) -> bool:
        """Validate a vCon object."""
        try:
            is_valid, errors = vcon.is_valid()
            if not is_valid:
                logger.error(f"vCon validation failed: {errors}")
                return False
            
            # Additional custom validation
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
