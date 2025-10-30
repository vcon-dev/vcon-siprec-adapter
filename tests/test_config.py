"""
Tests for configuration management.
"""

import pytest
import tempfile
import yaml
from pathlib import Path
from siprec_srs.config import ConfigManager, Config


class TestConfigManager:
    """Test cases for ConfigManager."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.config_manager = ConfigManager()
    
    def test_load_from_file(self):
        """Test loading configuration from YAML file."""
        config_data = {
            'server': {
                'listen_address': '127.0.0.1',
                'sip_port_udp': 5060
            },
            'storage': {
                'local_path': '/tmp/vcons'
            }
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(config_data, f)
            config_file = f.name
        
        try:
            config = self.config_manager.load_from_file(config_file)
            
            assert config.server.listen_address == '127.0.0.1'
            assert config.server.sip_port_udp == 5060
            assert config.storage.local_path == '/tmp/vcons'
            
        finally:
            Path(config_file).unlink()
    
    def test_validate_config(self):
        """Test configuration validation."""
        config = Config()
        
        # Valid config should pass
        assert self.config_manager.validate_config(config) is True
        
        # Invalid port should fail
        config.server.sip_port_udp = -1
        assert self.config_manager.validate_config(config) is False
    
    def test_load_from_env(self):
        """Test loading configuration from environment variables."""
        import os
        
        # Set environment variables
        os.environ['SIPREC_LISTEN_ADDRESS'] = '192.168.1.100'
        os.environ['SIPREC_UDP_PORT'] = '5061'
        os.environ['SIPREC_STORAGE_PATH'] = '/custom/vcons'
        
        try:
            config = self.config_manager.load_from_env()
            
            assert config.server.listen_address == '192.168.1.100'
            assert config.server.sip_port_udp == 5061
            assert config.storage.local_path == '/custom/vcons'
            
        finally:
            # Clean up environment
            for key in ['SIPREC_LISTEN_ADDRESS', 'SIPREC_UDP_PORT', 'SIPREC_STORAGE_PATH']:
                os.environ.pop(key, None)
