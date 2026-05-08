"""
SIPREC metadata parser for extracting information from SIP headers and SDP.
"""

import re
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import pjsua2 as pj

logger = logging.getLogger(__name__)


class SIPRECParser:
    """Parser for SIPREC metadata from SIP messages and SDP."""
    
    def __init__(self):
        self.siprec_headers = [
            'Recording-Session-ID',
            'Recording-URI',
            'Recording-Content-Type',
            'Recording-Content-Disposition',
            'Recording-Content-Encoding',
            'Recording-Content-Length',
            'Recording-Content-Language',
            'Recording-Content-Location',
            'Recording-Content-Range',
            'Recording-Content-Type',
            'Recording-Content-Encoding',
            'Recording-Content-Language',
            'Recording-Content-Location',
            'Recording-Content-Range',
            'Recording-Content-Type',
            'Recording-Content-Encoding',
            'Recording-Content-Language',
            'Recording-Content-Location',
            'Recording-Content-Range'
        ]
    
    def parse_invite(self, call_info: pj.CallInfo) -> Optional[Dict[str, Any]]:
        """Parse SIPREC metadata from incoming INVITE."""
        try:
            siprec_data = {
                'session_id': call_info.callId,
                'call_id': call_info.callId,
                'recording_session_id': '',
                'participants': [],
                'media_streams': [],
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'remote_uri': call_info.remoteUri,
                'local_uri': call_info.localUri
            }
            
            # Parse SIP headers
            self._parse_sip_headers(call_info, siprec_data)
            
            # Parse SDP
            self._parse_sdp(call_info, siprec_data)
            
            # Extract participants from headers and SDP
            self._extract_participants(call_info, siprec_data)
            
            return siprec_data
            
        except Exception as e:
            logger.error(f"Error parsing SIPREC INVITE: {e}")
            return None
    
    def _parse_sip_headers(self, call_info: pj.CallInfo, siprec_data: Dict[str, Any]):
        """Parse SIPREC-specific headers."""
        try:
            # Get raw SIP message (this is a simplified approach)
            # In a real implementation, you'd need to access the raw SIP message
            # For now, we'll extract what we can from the call info
            
            # Recording Session ID (most important header)
            if hasattr(call_info, 'recordingSessionId'):
                siprec_data['recording_session_id'] = call_info.recordingSessionId
            
            # Extract from remote URI if it contains session info
            remote_uri = call_info.remoteUri
            if 'session=' in remote_uri:
                match = re.search(r'session=([^;&]+)', remote_uri)
                if match:
                    siprec_data['recording_session_id'] = match.group(1)
            
            # Extract from local URI
            local_uri = call_info.localUri
            if 'recorder' in local_uri.lower():
                siprec_data['recording_uri'] = local_uri
            
        except Exception as e:
            logger.warning(f"Error parsing SIP headers: {e}")
    
    def _parse_sdp(self, call_info: pj.CallInfo, siprec_data: Dict[str, Any]):
        """Parse SDP information."""
        try:
            media_streams = []
            
            for media_idx in range(call_info.media.size()):
                media_info = call_info.media[media_idx]
                
                if media_info.type == pj.PJMEDIA_TYPE_AUDIO:
                    stream_info = {
                        'type': 'audio',
                        'index': media_idx,
                        'local_port': media_info.localRtpPort,
                        'remote_port': media_info.remoteRtpPort,
                        'codec': media_info.audioCodecName,
                        'clock_rate': media_info.audioClockRate,
                        'channels': media_info.audioChannelCount,
                        'status': media_info.status
                    }
                    media_streams.append(stream_info)
                
                elif media_info.type == pj.PJMEDIA_TYPE_VIDEO:
                    stream_info = {
                        'type': 'video',
                        'index': media_idx,
                        'local_port': media_info.localRtpPort,
                        'remote_port': media_info.remoteRtpPort,
                        'codec': media_info.videoCodecName,
                        'width': media_info.videoWidth,
                        'height': media_info.videoHeight,
                        'fps': media_info.videoFps,
                        'status': media_info.status
                    }
                    media_streams.append(stream_info)
            
            siprec_data['media_streams'] = media_streams
            
        except Exception as e:
            logger.warning(f"Error parsing SDP: {e}")
    
    def _extract_participants(self, call_info: pj.CallInfo, siprec_data: Dict[str, Any]):
        """Extract participant information from call info."""
        try:
            participants = []
            
            # Extract from remote URI
            remote_uri = call_info.remoteUri
            participant = self._parse_uri_for_participant(remote_uri, 'remote')
            if participant:
                participants.append(participant)
            
            # Extract from local URI
            local_uri = call_info.localUri
            participant = self._parse_uri_for_participant(local_uri, 'local')
            if participant:
                participants.append(participant)
            
            # If we don't have enough participants, create default ones
            if len(participants) < 2:
                # Create caller participant
                caller = {
                    'id': 'caller',
                    'role': 'caller',
                    'uri': remote_uri,
                    'name': self._extract_name_from_uri(remote_uri),
                    'tel': self._extract_phone_from_uri(remote_uri),
                    'mailto': self._extract_email_from_uri(remote_uri)
                }
                participants.append(caller)
                
                # Create callee participant
                callee = {
                    'id': 'callee',
                    'role': 'callee',
                    'uri': local_uri,
                    'name': self._extract_name_from_uri(local_uri),
                    'tel': self._extract_phone_from_uri(local_uri),
                    'mailto': self._extract_email_from_uri(local_uri)
                }
                participants.append(callee)
            
            siprec_data['participants'] = participants
            
        except Exception as e:
            logger.warning(f"Error extracting participants: {e}")
    
    def _parse_uri_for_participant(self, uri: str, role: str) -> Optional[Dict[str, Any]]:
        """Parse a SIP URI to extract participant information."""
        try:
            # Remove sip: prefix
            if uri.startswith('sip:'):
                uri = uri[4:]
            
            # Split on @ to separate user and domain
            if '@' in uri:
                user_part, domain_part = uri.split('@', 1)
            else:
                user_part = uri
                domain_part = ''
            
            # Extract display name if present
            name = ''
            if '<' in user_part and '>' in user_part:
                match = re.match(r'([^<]+)<([^>]+)>', user_part)
                if match:
                    name = match.group(1).strip().strip('"')
                    user_part = match.group(2)
            
            # Extract phone number or email
            tel = ''
            mailto = ''
            if re.match(r'^\+?[\d\-\(\)\s]+$', user_part):
                tel = user_part
            elif '@' in user_part:
                mailto = user_part
            
            return {
                'id': f"{role}_{user_part}",
                'role': role,
                'uri': f"sip:{uri}",
                'name': name or user_part,
                'tel': tel,
                'mailto': mailto,
                'domain': domain_part
            }
            
        except Exception as e:
            logger.warning(f"Error parsing URI {uri}: {e}")
            return None
    
    def _extract_name_from_uri(self, uri: str) -> str:
        """Extract display name from SIP URI."""
        try:
            if '<' in uri and '>' in uri:
                match = re.match(r'([^<]+)<([^>]+)>', uri)
                if match:
                    return match.group(1).strip().strip('"')
            return ''
        except:
            return ''
    
    def _extract_phone_from_uri(self, uri: str) -> str:
        """Extract phone number from SIP URI."""
        try:
            # Remove sip: prefix and display name
            clean_uri = uri
            if clean_uri.startswith('sip:'):
                clean_uri = clean_uri[4:]
            
            if '<' in clean_uri and '>' in clean_uri:
                match = re.match(r'([^<]+)<([^>]+)>', clean_uri)
                if match:
                    clean_uri = match.group(2)
            
            # Extract user part before @
            if '@' in clean_uri:
                user_part = clean_uri.split('@')[0]
            else:
                user_part = clean_uri
            
            # Check if it looks like a phone number
            if re.match(r'^\+?[\d\-\(\)\s]+$', user_part):
                return user_part
            
            return ''
        except:
            return ''
    
    def _extract_email_from_uri(self, uri: str) -> str:
        """Extract email from SIP URI."""
        try:
            # Remove sip: prefix and display name
            clean_uri = uri
            if clean_uri.startswith('sip:'):
                clean_uri = clean_uri[4:]
            
            if '<' in clean_uri and '>' in clean_uri:
                match = re.match(r'([^<]+)<([^>]+)>', clean_uri)
                if match:
                    clean_uri = match.group(2)
            
            # Extract user part before @
            if '@' in clean_uri:
                user_part = clean_uri.split('@')[0]
                domain_part = clean_uri.split('@')[1]
                
                # Check if it looks like an email
                if '@' in user_part or '.' in domain_part:
                    return f"{user_part}@{domain_part}"
            
            return ''
        except:
            return ''
    
    def parse_bye(self, call_info: pj.CallInfo) -> Optional[Dict[str, Any]]:
        """Parse BYE message for session termination."""
        try:
            return {
                'session_id': call_info.callId,
                'call_id': call_info.callId,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'reason': 'call_ended'
            }
        except Exception as e:
            logger.error(f"Error parsing BYE: {e}")
            return None
