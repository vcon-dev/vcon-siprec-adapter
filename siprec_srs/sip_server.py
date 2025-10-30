"""
SIP Server implementation using PJSIP for handling SIPREC sessions.
"""

import asyncio
import logging
import socket
import ssl
from typing import Dict, Optional, Callable, Any
from datetime import datetime
import pjsua2 as pj
from .config import Config
from .siprec_parser import SIPRECParser
from .rtp_handler import RTPHandler

logger = logging.getLogger(__name__)


class SIPRECSession:
    """Represents a single SIPREC recording session."""
    
    def __init__(self, session_id: str, call_id: str, recording_session_id: str):
        self.session_id = session_id
        self.call_id = call_id
        self.recording_session_id = recording_session_id
        self.start_time = datetime.now()
        self.end_time: Optional[datetime] = None
        self.participants: Dict[str, Dict[str, Any]] = {}
        self.rtp_handler: Optional[RTPHandler] = None
        self.call: Optional[pj.Call] = None
        self.media_streams: Dict[str, Any] = {}
        self.status = "active"  # active, paused, stopped, error
    
    def add_participant(self, participant_id: str, participant_data: Dict[str, Any]):
        """Add a participant to the session."""
        self.participants[participant_id] = participant_data
    
    def stop_session(self):
        """Stop the recording session."""
        self.end_time = datetime.now()
        self.status = "stopped"
        if self.rtp_handler:
            self.rtp_handler.stop_capture()


class SIPRECServer:
    """SIPREC Session Recording Server using PJSIP."""
    
    def __init__(self, config: Config):
        self.config = config
        self.endpoint: Optional[pj.Endpoint] = None
        self.transport_udp: Optional[pj.Transport] = None
        self.transport_tcp: Optional[pj.Transport] = None
        self.transport_tls: Optional[pj.Transport] = None
        self.account: Optional[pj.Account] = None
        self.sessions: Dict[str, SIPRECSession] = {}
        self.siprec_parser = SIPRECParser()
        self.rtp_handler = RTPHandler(config.rtp)
        self.session_callback: Optional[Callable[[SIPRECSession], None]] = None
        
    async def start(self):
        """Start the SIP server."""
        try:
            # Initialize PJSIP endpoint
            self.endpoint = pj.Endpoint()
            self.endpoint.libCreate()
            
            # Configure logging
            self.endpoint.libInit(pj.Endpoint().defaultConfig())
            
            # Configure transport
            await self._setup_transports()
            
            # Start the library
            self.endpoint.libStart()
            
            # Create account for receiving calls
            await self._setup_account()
            
            logger.info("SIPREC server started successfully")
            logger.info(f"Listening on UDP:{self.config.server.sip_port_udp}, "
                       f"TCP:{self.config.server.sip_port_tcp}, "
                       f"TLS:{self.config.server.sip_port_tls}")
            
        except Exception as e:
            logger.error(f"Failed to start SIP server: {e}")
            raise
    
    async def stop(self):
        """Stop the SIP server."""
        try:
            # Stop all active sessions
            for session in self.sessions.values():
                session.stop_session()
            
            # Destroy endpoint
            if self.endpoint:
                self.endpoint.libDestroy()
            
            logger.info("SIPREC server stopped")
            
        except Exception as e:
            logger.error(f"Error stopping SIP server: {e}")
    
    async def _setup_transports(self):
        """Set up SIP transports (UDP, TCP, TLS)."""
        transport_config = pj.TransportConfig()
        
        # UDP Transport
        transport_config.setPort(self.config.server.sip_port_udp)
        self.transport_udp = self.endpoint.transportCreate(
            pj.PJSIP_TRANSPORT_UDP, transport_config
        )
        
        # TCP Transport
        transport_config.setPort(self.config.server.sip_port_tcp)
        self.transport_tcp = self.endpoint.transportCreate(
            pj.PJSIP_TRANSPORT_TCP, transport_config
        )
        
        # TLS Transport
        if self.config.server.tls_cert and self.config.server.tls_key:
            transport_config.setPort(self.config.server.sip_port_tls)
            transport_config.setTlsSetting(pj.PJSIP_TLS_SETTING_CERT_FILE, 
                                        self.config.server.tls_cert)
            transport_config.setTlsSetting(pj.PJSIP_TLS_SETTING_PRIV_KEY_FILE, 
                                        self.config.server.tls_key)
            self.transport_tls = self.endpoint.transportCreate(
                pj.PJSIP_TRANSPORT_TLS, transport_config
            )
    
    async def _setup_account(self):
        """Set up SIP account for receiving calls."""
        account_config = pj.AccountConfig()
        account_config.setIdUri(f"sip:recorder@{self.config.server.listen_address}")
        
        self.account = pj.Account()
        self.account.create(account_config)
    
    def set_session_callback(self, callback: Callable[[SIPRECSession], None]):
        """Set callback for when a new session is created."""
        self.session_callback = callback
    
    def get_session(self, session_id: str) -> Optional[SIPRECSession]:
        """Get a session by ID."""
        return self.sessions.get(session_id)
    
    def get_sessions(self) -> Dict[str, SIPRECSession]:
        """Get all active sessions."""
        return self.sessions.copy()
    
    def stop_session(self, session_id: str) -> bool:
        """Stop a specific session."""
        session = self.sessions.get(session_id)
        if session:
            session.stop_session()
            return True
        return False


class SIPRECCall(pj.Call):
    """Custom call class for handling SIPREC sessions."""
    
    def __init__(self, acc: pj.Account, call_id: int = pj.PJSUA_INVALID_ID):
        super().__init__(acc, call_id)
        self.server: Optional[SIPRECServer] = None
        self.session: Optional[SIPRECSession] = None
    
    def set_server(self, server: SIPRECServer):
        """Set the server instance."""
        self.server = server
    
    def onIncomingCall(self, prm: pj.OnIncomingCallParam):
        """Handle incoming SIPREC call."""
        try:
            # Accept the call
            call_info = self.getInfo()
            logger.info(f"Incoming SIPREC call: {call_info.remoteUri}")
            
            # Parse SIPREC metadata
            siprec_data = self.server.siprec_parser.parse_invite(call_info)
            
            if not siprec_data:
                logger.warning("Failed to parse SIPREC data, rejecting call")
                self.hangup(pj.CallOpParam())
                return
            
            # Create session
            session_id = siprec_data.get('session_id', f"session_{call_info.callId}")
            recording_session_id = siprec_data.get('recording_session_id', '')
            
            session = SIPRECSession(
                session_id=session_id,
                call_id=call_info.callId,
                recording_session_id=recording_session_id
            )
            
            # Add participants
            for participant in siprec_data.get('participants', []):
                session.add_participant(
                    participant['id'], 
                    participant
                )
            
            # Set up RTP handler
            session.rtp_handler = RTPHandler(self.server.config.rtp)
            session.call = self
            self.session = session
            
            # Store session
            self.server.sessions[session_id] = session
            
            # Answer the call
            call_param = pj.CallOpParam()
            call_param.statusCode = pj.PJSIP_SC_OK
            self.answer(call_param)
            
            # Start RTP capture
            asyncio.create_task(self._start_rtp_capture(session))
            
            # Notify callback
            if self.server.session_callback:
                self.server.session_callback(session)
            
            logger.info(f"Started SIPREC session: {session_id}")
            
        except Exception as e:
            logger.error(f"Error handling incoming call: {e}")
            self.hangup(pj.CallOpParam())
    
    def _start_rtp_capture(self, session: SIPRECSession):
        """Start RTP media capture for the session."""
        try:
            # Get call info
            call_info = self.getInfo()
            
            # Set up RTP streams
            for media_idx in range(call_info.media.size()):
                media_info = call_info.media[media_idx]
                if media_info.type == pj.PJMEDIA_TYPE_AUDIO:
                    # Configure RTP handler for this stream
                    session.rtp_handler.configure_stream(
                        stream_id=f"{session.session_id}_stream_{media_idx}",
                        local_port=media_info.localRtpPort,
                        remote_addr=call_info.remoteContact,
                        codec=media_info.audioCodecName
                    )
                    
            # Start capture
            asyncio.create_task(session.rtp_handler.start_capture())
            
        except Exception as e:
            logger.error(f"Error starting RTP capture: {e}")
    
    def onCallState(self, prm: pj.OnCallStateParam):
        """Handle call state changes."""
        try:
            call_info = self.getInfo()
            logger.info(f"Call state changed: {call_info.stateText}")
            
            if call_info.state == pj.PJSIP_INV_STATE_DISCONNECTED:
                if self.session:
                    self.session.stop_session()
                    logger.info(f"Session ended: {self.session.session_id}")
            
        except Exception as e:
            logger.error(f"Error handling call state change: {e}")
    
    def onCallMediaState(self, prm: pj.OnCallMediaStateParam):
        """Handle media state changes."""
        try:
            call_info = self.getInfo()
            
            for media_idx in range(call_info.media.size()):
                media_info = call_info.media[media_idx]
                if media_info.type == pj.PJMEDIA_TYPE_AUDIO:
                    if media_info.status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                        logger.info(f"Audio stream {media_idx} is active")
                        # Additional media handling can be added here
                    elif media_info.status == pj.PJSUA_CALL_MEDIA_ERROR:
                        logger.error(f"Audio stream {media_idx} error")
            
        except Exception as e:
            logger.error(f"Error handling media state change: {e}")


class SIPRECAccount(pj.Account):
    """Custom account class for SIPREC server."""
    
    def __init__(self):
        super().__init__()
        self.server: Optional[SIPRECServer] = None
    
    def set_server(self, server: SIPRECServer):
        """Set the server instance."""
        self.server = server
    
    def onIncomingCall(self, prm: pj.OnIncomingCallParam):
        """Handle incoming calls by creating SIPRECCall instances."""
        try:
            call = SIPRECCall(self, prm.callId)
            call.set_server(self.server)
            call.onIncomingCall(prm)
        except Exception as e:
            logger.error(f"Error creating SIPREC call: {e}")
