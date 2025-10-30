"""
RTP media capture and audio stream handling.
"""

import asyncio
import logging
import socket
import struct
import wave
import tempfile
import os
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from pathlib import Path
import pydub
from pydub import AudioSegment
from .config import RTPConfig

logger = logging.getLogger(__name__)


class RTPPacket:
    """Represents an RTP packet."""
    
    def __init__(self, data: bytes):
        self.data = data
        self.version = (data[0] >> 6) & 0x3
        self.padding = (data[0] >> 5) & 0x1
        self.extension = (data[0] >> 4) & 0x1
        self.csrc_count = data[0] & 0xF
        self.marker = (data[1] >> 7) & 0x1
        self.payload_type = data[1] & 0x7F
        self.sequence_number = struct.unpack('>H', data[2:4])[0]
        self.timestamp = struct.unpack('>I', data[4:8])[0]
        self.ssrc = struct.unpack('>I', data[8:12])[0]
        
        # Calculate header length
        self.header_length = 12 + (self.csrc_count * 4)
        if self.extension:
            # Extension header length is in the next 2 bytes
            ext_length = struct.unpack('>H', data[self.header_length:self.header_length+2])[0]
            self.header_length += 2 + (ext_length * 4)
        
        # Payload data
        self.payload = data[self.header_length:]
    
    def is_valid(self) -> bool:
        """Check if the RTP packet is valid."""
        return (self.version == 2 and 
                len(self.data) >= 12 and 
                len(self.data) >= self.header_length)


class AudioStream:
    """Handles audio stream capture and processing."""
    
    def __init__(self, stream_id: str, config: RTPConfig):
        self.stream_id = stream_id
        self.config = config
        self.socket: Optional[socket.socket] = None
        self.local_port: Optional[int] = None
        self.remote_addr: Optional[tuple] = None
        self.codec: str = "PCMU"
        self.sample_rate: int = 8000
        self.channels: int = 1
        self.is_capturing = False
        self.audio_data: List[bytes] = []
        self.temp_file: Optional[str] = None
        self.wave_file: Optional[wave.Wave_write] = None
        self.sequence_number: Optional[int] = None
        self.timestamp: Optional[int] = None
        self.packet_count = 0
        self.bytes_received = 0
        
    def configure(self, local_port: int, remote_addr: str, codec: str = "PCMU"):
        """Configure the audio stream."""
        self.local_port = local_port
        self.remote_addr = self._parse_address(remote_addr)
        self.codec = codec
        
        # Set sample rate based on codec
        if codec.startswith("PCMU") or codec.startswith("PCMA"):
            self.sample_rate = 8000
        elif codec.startswith("G722"):
            self.sample_rate = 8000
        elif codec.startswith("opus"):
            self.sample_rate = 48000
        else:
            self.sample_rate = 8000
        
        logger.info(f"Configured audio stream {self.stream_id}: "
                   f"port={local_port}, remote={remote_addr}, codec={codec}")
    
    def _parse_address(self, addr_str: str) -> tuple:
        """Parse address string into (host, port) tuple."""
        try:
            if ':' in addr_str:
                host, port = addr_str.rsplit(':', 1)
                return (host, int(port))
            else:
                return (addr_str, 5060)  # Default port
        except:
            return (addr_str, 5060)
    
    async def start_capture(self):
        """Start capturing RTP packets."""
        try:
            # Create UDP socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.bind(('0.0.0.0', self.local_port))
            self.socket.settimeout(1.0)  # 1 second timeout
            
            # Create temporary file for audio data
            self.temp_file = tempfile.mktemp(suffix='.wav')
            self.wave_file = wave.open(self.temp_file, 'wb')
            self.wave_file.setnchannels(self.channels)
            self.wave_file.setsampwidth(2)  # 16-bit samples
            self.wave_file.setframerate(self.sample_rate)
            
            self.is_capturing = True
            logger.info(f"Started RTP capture for stream {self.stream_id}")
            
            # Start capture loop
            asyncio.create_task(self._capture_loop())
            
        except Exception as e:
            logger.error(f"Error starting RTP capture: {e}")
            raise
    
    async def stop_capture(self):
        """Stop capturing RTP packets."""
        try:
            self.is_capturing = False
            
            if self.wave_file:
                self.wave_file.close()
                self.wave_file = None
            
            if self.socket:
                self.socket.close()
                self.socket = None
            
            logger.info(f"Stopped RTP capture for stream {self.stream_id}. "
                       f"Received {self.packet_count} packets, {self.bytes_received} bytes")
            
        except Exception as e:
            logger.error(f"Error stopping RTP capture: {e}")
    
    async def _capture_loop(self):
        """Main capture loop for RTP packets."""
        while self.is_capturing:
            try:
                # Receive RTP packet
                data, addr = self.socket.recvfrom(self.config.buffer_size)
                
                # Parse RTP packet
                rtp_packet = RTPPacket(data)
                
                if not rtp_packet.is_valid():
                    logger.warning(f"Invalid RTP packet received on stream {self.stream_id}")
                    continue
                
                # Process audio payload
                await self._process_audio_payload(rtp_packet)
                
                self.packet_count += 1
                self.bytes_received += len(data)
                
            except socket.timeout:
                # Timeout is normal, continue
                continue
            except Exception as e:
                if self.is_capturing:
                    logger.error(f"Error in RTP capture loop: {e}")
                break
    
    async def _process_audio_payload(self, rtp_packet: RTPPacket):
        """Process audio payload from RTP packet."""
        try:
            # Decode audio based on codec
            if self.codec.startswith("PCMU"):
                audio_data = self._decode_pcmu(rtp_packet.payload)
            elif self.codec.startswith("PCMA"):
                audio_data = self._decode_pcma(rtp_packet.payload)
            elif self.codec.startswith("G722"):
                audio_data = self._decode_g722(rtp_packet.payload)
            else:
                # Default to PCMU
                audio_data = self._decode_pcmu(rtp_packet.payload)
            
            # Write to WAV file
            if self.wave_file and audio_data:
                self.wave_file.writeframes(audio_data)
            
        except Exception as e:
            logger.warning(f"Error processing audio payload: {e}")
    
    def _decode_pcmu(self, payload: bytes) -> bytes:
        """Decode μ-law (PCMU) audio."""
        try:
            # Convert μ-law to linear PCM
            decoded = []
            for byte in payload:
                # Simple μ-law to linear conversion
                sign = 1 if (byte & 0x80) == 0 else -1
                exponent = (byte & 0x70) >> 4
                mantissa = byte & 0x0F
                
                if exponent == 0:
                    sample = (mantissa << 1) + 1
                else:
                    sample = ((mantissa << 1) + 33) << (exponent - 1)
                
                sample = sign * sample
                decoded.append(sample)
            
            # Convert to 16-bit PCM
            pcm_data = struct.pack('<' + 'h' * len(decoded), *decoded)
            return pcm_data
            
        except Exception as e:
            logger.warning(f"Error decoding PCMU: {e}")
            return b''
    
    def _decode_pcma(self, payload: bytes) -> bytes:
        """Decode A-law (PCMA) audio."""
        try:
            # Convert A-law to linear PCM
            decoded = []
            for byte in payload:
                # Simple A-law to linear conversion
                sign = 1 if (byte & 0x80) == 0 else -1
                exponent = (byte & 0x70) >> 4
                mantissa = byte & 0x0F
                
                if exponent == 0:
                    sample = (mantissa << 1) + 1
                else:
                    sample = ((mantissa << 1) + 33) << (exponent - 1)
                
                sample = sign * sample
                decoded.append(sample)
            
            # Convert to 16-bit PCM
            pcm_data = struct.pack('<' + 'h' * len(decoded), *decoded)
            return pcm_data
            
        except Exception as e:
            logger.warning(f"Error decoding PCMA: {e}")
            return b''
    
    def _decode_g722(self, payload: bytes) -> bytes:
        """Decode G.722 audio (simplified)."""
        try:
            # G.722 is more complex, for now just return raw data
            # In a real implementation, you'd use a proper G.722 decoder
            return payload
            
        except Exception as e:
            logger.warning(f"Error decoding G.722: {e}")
            return b''
    
    def get_audio_file(self) -> Optional[str]:
        """Get the path to the captured audio file."""
        return self.temp_file
    
    def get_stats(self) -> Dict[str, Any]:
        """Get stream statistics."""
        return {
            'stream_id': self.stream_id,
            'packet_count': self.packet_count,
            'bytes_received': self.bytes_received,
            'codec': self.codec,
            'sample_rate': self.sample_rate,
            'channels': self.channels,
            'is_capturing': self.is_capturing
        }


class RTPHandler:
    """Main RTP handler for managing multiple audio streams."""
    
    def __init__(self, config: RTPConfig):
        self.config = config
        self.streams: Dict[str, AudioStream] = {}
        self.is_running = False
    
    def configure_stream(self, stream_id: str, local_port: int, 
                        remote_addr: str, codec: str = "PCMU") -> AudioStream:
        """Configure a new audio stream."""
        stream = AudioStream(stream_id, self.config)
        stream.configure(local_port, remote_addr, codec)
        self.streams[stream_id] = stream
        return stream
    
    async def start_capture(self):
        """Start capturing all configured streams."""
        try:
            self.is_running = True
            
            # Start all streams
            for stream in self.streams.values():
                await stream.start_capture()
            
            logger.info(f"Started RTP capture for {len(self.streams)} streams")
            
        except Exception as e:
            logger.error(f"Error starting RTP capture: {e}")
            raise
    
    async def stop_capture(self):
        """Stop capturing all streams."""
        try:
            self.is_running = False
            
            # Stop all streams
            for stream in self.streams.values():
                await stream.stop_capture()
            
            logger.info("Stopped RTP capture for all streams")
            
        except Exception as e:
            logger.error(f"Error stopping RTP capture: {e}")
    
    def get_stream(self, stream_id: str) -> Optional[AudioStream]:
        """Get a specific stream by ID."""
        return self.streams.get(stream_id)
    
    def get_all_streams(self) -> Dict[str, AudioStream]:
        """Get all streams."""
        return self.streams.copy()
    
    def get_audio_files(self) -> Dict[str, str]:
        """Get all captured audio files."""
        files = {}
        for stream_id, stream in self.streams.items():
            audio_file = stream.get_audio_file()
            if audio_file:
                files[stream_id] = audio_file
        return files
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for all streams."""
        stats = {
            'total_streams': len(self.streams),
            'is_running': self.is_running,
            'streams': {}
        }
        
        for stream_id, stream in self.streams.items():
            stats['streams'][stream_id] = stream.get_stats()
        
        return stats
