"""
Integration tests for SIPREC SRS server.
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from siprec_srs.config import Config, ServerConfig, StorageConfig, WebhookConfig
from siprec_srs.vcon_converter import VConConverter
from siprec_srs.storage_handler import StorageHandler


class TestIntegration:
    """Integration test cases."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        
        # Create test configuration
        self.config = Config()
        self.config.storage.local_path = self.temp_dir
        self.config.webhooks.enabled = False  # Disable webhooks for testing
        
        self.converter = VConConverter()
        self.storage_handler = StorageHandler(self.config.storage)
    
    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_vcon_creation_and_storage(self):
        """Test creating a vCon and storing it."""
        session_data = {
            'session_id': 'integration_test_123',
            'call_id': 'call_integration@test.com',
            'recording_session_id': 'rec_integration',
            'participants': [
                {
                    'id': 'caller',
                    'name': 'Test Caller',
                    'tel': '+1234567890',
                    'role': 'caller'
                },
                {
                    'id': 'callee',
                    'name': 'Test Callee',
                    'tel': '+1987654321',
                    'role': 'callee'
                }
            ],
            'start_time': '2023-01-01T12:00:00Z',
            'end_time': '2023-01-01T12:05:00Z',
            'media_streams': []
        }
        
        # Convert to vCon
        vcon = self.converter.convert_session_to_vcon(session_data, None)
        assert vcon is not None
        
        # Validate vCon
        is_valid = self.converter.validate_vcon(vcon)
        assert is_valid is True
        
        # Save to storage
        file_path = self.storage_handler.save_vcon(
            vcon, session_data['session_id'], session_data['call_id']
        )
        assert file_path is not None
        assert Path(file_path).exists()
        
        # Load from storage
        loaded_vcon = self.storage_handler.load_vcon(file_path)
        assert loaded_vcon is not None
        assert loaded_vcon.uuid == vcon.uuid
    
    def test_storage_operations(self):
        """Test storage handler operations."""
        # Test directory creation
        assert Path(self.temp_dir).exists()
        
        # Test file listing
        files = self.storage_handler.list_vcons()
        assert isinstance(files, list)
        
        # Test stats
        stats = self.storage_handler.get_storage_stats()
        assert 'total_files' in stats
        assert 'total_size_bytes' in stats
    
    def test_configuration_validation(self):
        """Test configuration validation."""
        from siprec_srs.config import ConfigManager
        
        config_manager = ConfigManager()
        
        # Valid configuration
        assert config_manager.validate_config(self.config) is True
        
        # Test with invalid configuration
        invalid_config = Config()
        invalid_config.server.sip_port_udp = -1
        assert config_manager.validate_config(invalid_config) is False
