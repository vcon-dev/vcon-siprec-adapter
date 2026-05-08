#!/usr/bin/env python3
"""
SIPREC SRS to vCon Server

Main entry point for the SIPREC Session Recording Server that converts
SIP conversations into vCon format.
"""

import asyncio
import logging
import signal
import sys
import argparse
from pathlib import Path
from typing import Optional

from siprec_srs.config import ConfigManager, Config
from siprec_srs.sip_server import SIPRECServer
from siprec_srs.vcon_converter import VConConverter
from siprec_srs.storage_handler import StorageHandler
from siprec_srs.webhook_delivery import WebhookDelivery
from siprec_srs.health_server import HealthServer
from siprec_srs.signing import Signer, SigningError


class SIPRECSRSApp:
    """Main application class for SIPREC SRS server."""
    
    def __init__(self, config: Config):
        self.config = config
        self.sip_server: Optional[SIPRECServer] = None
        self.vcon_converter = VConConverter(
            lawful_basis_config=config.lawful_basis,
            media_config=config.media,
        )
        self.storage_handler = StorageHandler(config.storage)
        self.webhook_delivery = WebhookDelivery(config.webhooks)

        # Signing is loaded eagerly so missing/invalid keys fail at startup,
        # not on the first session.
        self.signer: Optional[Signer] = None
        if config.signing.enabled:
            if not config.signing.private_key_path:
                raise SigningError(
                    "signing.enabled is true but signing.private_key_path is unset"
                )
            password = (
                config.signing.private_key_password.encode('utf-8')
                if config.signing.private_key_password else None
            )
            self.signer = Signer.from_pem_file(
                config.signing.private_key_path, password=password
            )
            logging.info(f"Signing enabled (key: {config.signing.private_key_path})")

        self.health_server: Optional[HealthServer] = (
            HealthServer(
                host=config.health.host,
                port=config.health.port,
                webhook_stats_provider=self.webhook_delivery.get_stats,
            )
            if config.health.enabled
            else None
        )
        self.running = False
        
        # Set up logging
        self._setup_logging()
        
        # Set up signal handlers
        self._setup_signal_handlers()
    
    def _setup_logging(self):
        """Set up logging configuration."""
        log_level = getattr(logging, self.config.logging.level.upper(), logging.INFO)
        
        # Configure root logger
        logging.basicConfig(
            level=log_level,
            format=self.config.logging.format,
            handlers=[]
        )
        
        # Add console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(logging.Formatter(self.config.logging.format))
        logging.getLogger().addHandler(console_handler)
        
        # Add file handler if configured
        if self.config.logging.file:
            try:
                log_file = Path(self.config.logging.file)
                log_file.parent.mkdir(parents=True, exist_ok=True)
                
                file_handler = logging.FileHandler(log_file)
                file_handler.setLevel(log_level)
                file_handler.setFormatter(logging.Formatter(self.config.logging.format))
                logging.getLogger().addHandler(file_handler)
                
                logging.info(f"Logging to file: {log_file}")
            except Exception as e:
                logging.warning(f"Could not set up file logging: {e}")
        
        logging.info(f"Logging level: {self.config.logging.level}")
    
    def _setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logging.info(f"Received signal {signum}, shutting down...")
            asyncio.create_task(self.stop())
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def start(self):
        """Start the SIPREC SRS server."""
        try:
            logging.info("Starting SIPREC SRS server...")
            
            # Start webhook delivery
            await self.webhook_delivery.start()

            # Start health/metrics server
            if self.health_server is not None:
                await self.health_server.start()
            
            # Start SIP server
            self.sip_server = SIPRECServer(self.config)
            self.sip_server.set_session_callback(self._on_session_created)
            await self.sip_server.start()
            
            self.running = True
            logging.info("SIPREC SRS server started successfully")
            
            # Keep running until stopped
            while self.running:
                await asyncio.sleep(1)
            
        except Exception as e:
            logging.error(f"Error starting server: {e}")
            raise
    
    async def stop(self):
        """Stop the SIPREC SRS server."""
        try:
            logging.info("Stopping SIPREC SRS server...")
            
            self.running = False
            
            # Stop SIP server
            if self.sip_server:
                await self.sip_server.stop()
            
            # Stop health server
            if self.health_server is not None:
                await self.health_server.stop()

            # Stop webhook delivery
            await self.webhook_delivery.stop()
            
            logging.info("SIPREC SRS server stopped")
            
        except Exception as e:
            logging.error(f"Error stopping server: {e}")
    
    async def _on_session_created(self, session):
        """Handle new SIPREC session creation."""
        try:
            logging.info(f"New SIPREC session created: {session.session_id}")
            
            # Wait for session to complete (in a real implementation, 
            # this would be triggered by session end events)
            await asyncio.sleep(5)  # Simulate session duration
            
            # Convert session to vCon
            session_data = {
                'session_id': session.session_id,
                'call_id': session.call_id,
                'recording_session_id': session.recording_session_id,
                'participants': session.participants,
                'start_time': session.start_time.isoformat(),
                'end_time': session.end_time.isoformat() if session.end_time else None,
                'media_streams': session.media_streams
            }
            
            # Convert to vCon
            vcon = self.vcon_converter.convert_session_to_vcon(
                session_data, session.rtp_handler
            )
            
            if not vcon:
                logging.error(f"Failed to convert session {session.session_id} to vCon")
                return

            # Sign before storage and webhook delivery so all downstream
            # consumers see the JWS-wrapped form. Signing mutates in place.
            if self.signer is not None:
                try:
                    self.signer.sign(vcon)
                except SigningError as e:
                    logging.error(f"Signing failed for session {session.session_id}: {e}")
                    return

            # Save to local storage
            file_path = self.storage_handler.save_vcon(
                vcon, session.session_id, session.call_id
            )
            
            if file_path:
                logging.info(f"Saved vCon to: {file_path}")
            else:
                logging.error(f"Failed to save vCon for session {session.session_id}")
                return
            
            # Deliver via webhook
            if self.config.webhooks.enabled:
                delivery_result = await self.webhook_delivery.deliver_vcon(
                    vcon, session.session_id, session.call_id
                )
                
                logging.info(f"Webhook delivery result: {delivery_result}")
            
            # Clean up temporary files
            if self.config.storage.cleanup_temp_files and session.rtp_handler:
                await self._cleanup_temp_files(session.rtp_handler)
            
        except Exception as e:
            logging.error(f"Error processing session {session.session_id}: {e}")
    
    async def _cleanup_temp_files(self, rtp_handler):
        """Clean up temporary files created during recording."""
        try:
            # Get audio files from RTP handler
            audio_files = rtp_handler.get_audio_files()
            
            for stream_id, file_path in audio_files.items():
                try:
                    Path(file_path).unlink(missing_ok=True)
                    logging.debug(f"Cleaned up temp file: {file_path}")
                except Exception as e:
                    logging.warning(f"Could not clean up {file_path}: {e}")
                    
        except Exception as e:
            logging.warning(f"Error cleaning up temp files: {e}")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="SIPREC SRS to vCon Server")
    parser.add_argument("--config", "-c", help="Path to configuration file")
    parser.add_argument("--env-file", "-e", help="Path to environment file")
    parser.add_argument("--log-level", "-l",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="Logging level")

    args = parser.parse_args()
    
    try:
        # Load configuration
        config_manager = ConfigManager()
        
        if args.config:
            config = config_manager.load_from_file(args.config)
        elif args.env_file:
            # Load environment variables from file
            from dotenv import load_dotenv
            load_dotenv(args.env_file)
            config = config_manager.load_from_env()
        else:
            # Try to load from default config file
            config_file = Path("config.yaml")
            if config_file.exists():
                config = config_manager.load_from_file(str(config_file))
            else:
                # Load from environment variables
                config = config_manager.load_from_env()
        
        # Override log level if specified
        if args.log_level:
            config.logging.level = args.log_level
        
        # Validate configuration
        if not config_manager.validate_config(config):
            logging.error("Invalid configuration")
            sys.exit(1)
        
        # Create and start application
        app = SIPRECSRSApp(config)
        await app.start()
        
    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
