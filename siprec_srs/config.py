"""
Configuration management for SIPREC SRS server.
"""

import os
import yaml
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class ServerConfig:
    """Server configuration settings."""
    listen_address: str = "0.0.0.0"
    sip_port_udp: int = 5060
    sip_port_tcp: int = 5060
    sip_port_tls: int = 5061
    tls_cert: Optional[str] = None
    tls_key: Optional[str] = None
    user_agent: str = "SIPREC-SRS/1.0"
    max_sessions: int = 100
    session_timeout: int = 3600


@dataclass
class StorageConfig:
    """Storage configuration settings."""
    local_path: str = "./vcons"
    filename_pattern: str = "{timestamp}_{call_id}.vcon.json"
    create_directories: bool = True
    cleanup_temp_files: bool = True


@dataclass
class WebhookEndpoint:
    """Webhook endpoint configuration."""
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    retry_attempts: int = 3
    timeout: int = 30
    backoff_factor: float = 2.0


@dataclass
class WebhookConfig:
    """Webhook configuration settings."""
    enabled: bool = True
    endpoints: List[WebhookEndpoint] = field(default_factory=list)


@dataclass
class RTPConfig:
    """RTP configuration settings."""
    buffer_size: int = 65536
    supported_codecs: List[str] = field(default_factory=lambda: [
        "PCMU/8000", "PCMA/8000", "G722/8000", "opus/48000"
    ])
    audio_format: str = "wav"
    sample_rate: int = 8000
    channels: int = 1


@dataclass
class LoggingConfig:
    """Logging configuration settings."""
    level: str = "INFO"
    file: Optional[str] = None
    max_size: str = "10MB"
    backup_count: int = 5
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


@dataclass
class Config:
    """Main configuration container."""
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    webhooks: WebhookConfig = field(default_factory=WebhookConfig)
    rtp: RTPConfig = field(default_factory=RTPConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


class ConfigManager:
    """Configuration manager for loading and validating settings."""
    
    def __init__(self):
        self.config: Optional[Config] = None
    
    def load_from_file(self, config_path: str) -> Config:
        """Load configuration from YAML file."""
        try:
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f)
            return self._parse_config(config_data)
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML in configuration file: {e}")
            raise
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise
    
    def load_from_env(self) -> Config:
        """Load configuration from environment variables."""
        config_data = self._extract_env_config()
        return self._parse_config(config_data)
    
    def _extract_env_config(self) -> Dict[str, Any]:
        """Extract configuration from environment variables."""
        config = {}
        
        # Server configuration
        server_config = {}
        if addr := os.getenv('SIPREC_LISTEN_ADDRESS'):
            server_config['listen_address'] = addr
        if port := os.getenv('SIPREC_UDP_PORT'):
            server_config['sip_port_udp'] = int(port)
        if port := os.getenv('SIPREC_TCP_PORT'):
            server_config['sip_port_tcp'] = int(port)
        if port := os.getenv('SIPREC_TLS_PORT'):
            server_config['sip_port_tls'] = int(port)
        if cert := os.getenv('SIPREC_TLS_CERT'):
            server_config['tls_cert'] = cert
        if key := os.getenv('SIPREC_TLS_KEY'):
            server_config['tls_key'] = key
        
        if server_config:
            config['server'] = server_config
        
        # Storage configuration
        storage_config = {}
        if path := os.getenv('SIPREC_STORAGE_PATH'):
            storage_config['local_path'] = path
        if pattern := os.getenv('SIPREC_FILENAME_PATTERN'):
            storage_config['filename_pattern'] = pattern
        
        if storage_config:
            config['storage'] = storage_config
        
        # Webhook configuration
        webhook_config = {}
        if enabled := os.getenv('SIPREC_WEBHOOK_ENABLED'):
            webhook_config['enabled'] = enabled.lower() == 'true'
        
        if url := os.getenv('SIPREC_WEBHOOK_URL'):
            endpoint = {'url': url}
            if token := os.getenv('SIPREC_WEBHOOK_TOKEN'):
                endpoint['headers'] = {'Authorization': f'Bearer {token}'}
            if retries := os.getenv('SIPREC_WEBHOOK_RETRY_ATTEMPTS'):
                endpoint['retry_attempts'] = int(retries)
            if timeout := os.getenv('SIPREC_WEBHOOK_TIMEOUT'):
                endpoint['timeout'] = int(timeout)
            
            webhook_config['endpoints'] = [endpoint]
        
        if webhook_config:
            config['webhooks'] = webhook_config
        
        # Logging configuration
        logging_config = {}
        if level := os.getenv('SIPREC_LOG_LEVEL'):
            logging_config['level'] = level
        if log_file := os.getenv('SIPREC_LOG_FILE'):
            logging_config['file'] = log_file
        
        if logging_config:
            config['logging'] = logging_config
        
        return config
    
    def _parse_config(self, config_data: Dict[str, Any]) -> Config:
        """Parse configuration data into Config objects."""
        config = Config()
        
        # Parse server configuration
        if 'server' in config_data:
            server_data = config_data['server']
            config.server = ServerConfig(
                listen_address=server_data.get('listen_address', config.server.listen_address),
                sip_port_udp=server_data.get('sip_port_udp', config.server.sip_port_udp),
                sip_port_tcp=server_data.get('sip_port_tcp', config.server.sip_port_tcp),
                sip_port_tls=server_data.get('sip_port_tls', config.server.sip_port_tls),
                tls_cert=server_data.get('tls_cert', config.server.tls_cert),
                tls_key=server_data.get('tls_key', config.server.tls_key),
                user_agent=server_data.get('user_agent', config.server.user_agent),
                max_sessions=server_data.get('max_sessions', config.server.max_sessions),
                session_timeout=server_data.get('session_timeout', config.server.session_timeout)
            )
        
        # Parse storage configuration
        if 'storage' in config_data:
            storage_data = config_data['storage']
            config.storage = StorageConfig(
                local_path=storage_data.get('local_path', config.storage.local_path),
                filename_pattern=storage_data.get('filename_pattern', config.storage.filename_pattern),
                create_directories=storage_data.get('create_directories', config.storage.create_directories),
                cleanup_temp_files=storage_data.get('cleanup_temp_files', config.storage.cleanup_temp_files)
            )
        
        # Parse webhook configuration
        if 'webhooks' in config_data:
            webhook_data = config_data['webhooks']
            endpoints = []
            for endpoint_data in webhook_data.get('endpoints', []):
                endpoint = WebhookEndpoint(
                    url=endpoint_data['url'],
                    headers=endpoint_data.get('headers', {}),
                    retry_attempts=endpoint_data.get('retry_attempts', 3),
                    timeout=endpoint_data.get('timeout', 30),
                    backoff_factor=endpoint_data.get('backoff_factor', 2.0)
                )
                endpoints.append(endpoint)
            
            config.webhooks = WebhookConfig(
                enabled=webhook_data.get('enabled', True),
                endpoints=endpoints
            )
        
        # Parse RTP configuration
        if 'rtp' in config_data:
            rtp_data = config_data['rtp']
            config.rtp = RTPConfig(
                buffer_size=rtp_data.get('buffer_size', config.rtp.buffer_size),
                supported_codecs=rtp_data.get('supported_codecs', config.rtp.supported_codecs),
                audio_format=rtp_data.get('audio_format', config.rtp.audio_format),
                sample_rate=rtp_data.get('sample_rate', config.rtp.sample_rate),
                channels=rtp_data.get('channels', config.rtp.channels)
            )
        
        # Parse logging configuration
        if 'logging' in config_data:
            logging_data = config_data['logging']
            config.logging = LoggingConfig(
                level=logging_data.get('level', config.logging.level),
                file=logging_data.get('file', config.logging.file),
                max_size=logging_data.get('max_size', config.logging.max_size),
                backup_count=logging_data.get('backup_count', config.logging.backup_count),
                format=logging_data.get('format', config.logging.format)
            )
        
        return config
    
    def validate_config(self, config: Config) -> bool:
        """Validate configuration settings."""
        errors = []
        
        # Validate server configuration
        if config.server.sip_port_udp <= 0 or config.server.sip_port_udp > 65535:
            errors.append("Invalid UDP port number")
        if config.server.sip_port_tcp <= 0 or config.server.sip_port_tcp > 65535:
            errors.append("Invalid TCP port number")
        if config.server.sip_port_tls <= 0 or config.server.sip_port_tls > 65535:
            errors.append("Invalid TLS port number")
        
        # Validate TLS configuration
        if config.server.tls_cert and not Path(config.server.tls_cert).exists():
            errors.append(f"TLS certificate file not found: {config.server.tls_cert}")
        if config.server.tls_key and not Path(config.server.tls_key).exists():
            errors.append(f"TLS key file not found: {config.server.tls_key}")
        
        # Validate storage configuration
        if config.storage.create_directories:
            try:
                Path(config.storage.local_path).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create storage directory: {e}")
        
        # Validate webhook configuration
        for endpoint in config.webhooks.endpoints:
            if not endpoint.url.startswith(('http://', 'https://')):
                errors.append(f"Invalid webhook URL: {endpoint.url}")
        
        if errors:
            for error in errors:
                logger.error(f"Configuration validation error: {error}")
            return False
        
        return True
    
    def get_config(self) -> Config:
        """Get the current configuration."""
        if self.config is None:
            raise RuntimeError("Configuration not loaded")
        return self.config
    
    def set_config(self, config: Config) -> None:
        """Set the current configuration."""
        if not self.validate_config(config):
            raise ValueError("Invalid configuration")
        self.config = config
