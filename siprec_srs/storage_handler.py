"""
Storage handler for saving vCons to local filesystem and managing files.
"""

import os
import logging
import tempfile
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path
import json
from vcon import Vcon
from .config import StorageConfig

logger = logging.getLogger(__name__)


class StorageHandler:
    """Handles local filesystem storage of vCon files."""
    
    def __init__(self, config: StorageConfig):
        self.config = config
        self.storage_path = Path(config.local_path)
        self._ensure_storage_directory()
    
    def _ensure_storage_directory(self):
        """Ensure the storage directory exists."""
        try:
            if self.config.create_directories:
                self.storage_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Storage directory: {self.storage_path.absolute()}")
            else:
                if not self.storage_path.exists():
                    raise FileNotFoundError(f"Storage directory does not exist: {self.storage_path}")
        except Exception as e:
            logger.error(f"Error setting up storage directory: {e}")
            raise
    
    def save_vcon(self, vcon: Vcon, session_id: str, 
                  call_id: str = None, custom_filename: str = None) -> Optional[str]:
        """Save a vCon to the filesystem."""
        try:
            # Generate filename
            if custom_filename:
                filename = custom_filename
            else:
                filename = self._generate_filename(session_id, call_id)
            
            # Ensure filename has .json extension
            if not filename.endswith('.json'):
                filename += '.json'
            
            # Create full path
            file_path = self.storage_path / filename
            
            # Save vCon
            vcon.save_to_file(str(file_path))
            
            logger.info(f"Saved vCon to: {file_path}")
            return str(file_path)
            
        except Exception as e:
            logger.error(f"Error saving vCon: {e}")
            return None
    
    def _generate_filename(self, session_id: str, call_id: str = None) -> str:
        """Generate filename based on configuration pattern."""
        try:
            # Get current timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Format filename using pattern
            filename = self.config.filename_pattern.format(
                timestamp=timestamp,
                session_id=session_id,
                call_id=call_id or session_id,
                date=datetime.now().strftime("%Y%m%d"),
                time=datetime.now().strftime("%H%M%S")
            )
            
            # Sanitize filename
            filename = self._sanitize_filename(filename)
            
            return filename
            
        except Exception as e:
            logger.error(f"Error generating filename: {e}")
            # Fallback filename
            return f"vcon_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to be filesystem-safe."""
        # Replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        
        # Remove multiple underscores
        while '__' in filename:
            filename = filename.replace('__', '_')
        
        # Ensure it's not too long
        if len(filename) > 255:
            name, ext = os.path.splitext(filename)
            filename = name[:255-len(ext)] + ext
        
        return filename
    
    def load_vcon(self, file_path: str) -> Optional[Vcon]:
        """Load a vCon from file."""
        try:
            vcon = Vcon.load_from_file(file_path)
            logger.info(f"Loaded vCon from: {file_path}")
            return vcon
        except Exception as e:
            logger.error(f"Error loading vCon from {file_path}: {e}")
            return None
    
    def list_vcons(self, pattern: str = "*") -> List[str]:
        """List vCon files in storage directory."""
        try:
            files = list(self.storage_path.glob(f"{pattern}.json"))
            return [str(f) for f in files]
        except Exception as e:
            logger.error(f"Error listing vCons: {e}")
            return []
    
    def get_vcon_info(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get information about a vCon file."""
        try:
            vcon = self.load_vcon(file_path)
            if not vcon:
                return None
            
            file_stat = Path(file_path).stat()
            
            return {
                'file_path': file_path,
                'file_size': file_stat.st_size,
                'created_at': datetime.fromtimestamp(file_stat.st_ctime).isoformat(),
                'modified_at': datetime.fromtimestamp(file_stat.st_mtime).isoformat(),
                'vcon_uuid': vcon.uuid,
                'vcon_version': vcon.vcon,
                'parties_count': len(vcon.parties),
                'dialogs_count': len(vcon.dialog),
                'attachments_count': len(vcon.attachments),
                'analysis_count': len(vcon.analysis)
            }
            
        except Exception as e:
            logger.error(f"Error getting vCon info for {file_path}: {e}")
            return None
    
    def cleanup_old_files(self, max_age_days: int = 30) -> int:
        """Clean up old vCon files."""
        try:
            import time
            current_time = time.time()
            max_age_seconds = max_age_days * 24 * 60 * 60
            
            cleaned_count = 0
            for file_path in self.storage_path.glob("*.json"):
                file_age = current_time - file_path.stat().st_mtime
                if file_age > max_age_seconds:
                    try:
                        file_path.unlink()
                        cleaned_count += 1
                        logger.info(f"Cleaned up old file: {file_path}")
                    except Exception as e:
                        logger.warning(f"Could not delete {file_path}: {e}")
            
            logger.info(f"Cleaned up {cleaned_count} old files")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"Error cleaning up old files: {e}")
            return 0
    
    def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        try:
            files = list(self.storage_path.glob("*.json"))
            total_size = sum(f.stat().st_size for f in files)
            
            return {
                'total_files': len(files),
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'storage_path': str(self.storage_path.absolute()),
                'oldest_file': min((f.stat().st_mtime for f in files), default=0),
                'newest_file': max((f.stat().st_mtime for f in files), default=0)
            }
            
        except Exception as e:
            logger.error(f"Error getting storage stats: {e}")
            return {}
    
    def create_backup(self, backup_path: str) -> bool:
        """Create a backup of all vCon files."""
        try:
            import shutil
            
            backup_dir = Path(backup_path)
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy all vCon files
            for file_path in self.storage_path.glob("*.json"):
                shutil.copy2(file_path, backup_dir)
            
            logger.info(f"Created backup at: {backup_dir}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating backup: {e}")
            return False
    
    def search_vcons(self, query: str, search_in_content: bool = False) -> List[Dict[str, Any]]:
        """Search for vCons by filename or content."""
        try:
            results = []
            
            for file_path in self.storage_path.glob("*.json"):
                # Search in filename
                if query.lower() in file_path.name.lower():
                    info = self.get_vcon_info(str(file_path))
                    if info:
                        results.append(info)
                    continue
                
                # Search in content if requested
                if search_in_content:
                    try:
                        vcon = self.load_vcon(str(file_path))
                        if vcon and self._search_vcon_content(vcon, query):
                            info = self.get_vcon_info(str(file_path))
                            if info:
                                results.append(info)
                    except Exception as e:
                        logger.warning(f"Error searching content in {file_path}: {e}")
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching vCons: {e}")
            return []
    
    def _search_vcon_content(self, vcon: Vcon, query: str) -> bool:
        """Search for query in vCon content."""
        try:
            query_lower = query.lower()
            
            # Search in parties
            for party in vcon.parties:
                if (query_lower in (party.name or '').lower() or
                    query_lower in (party.tel or '').lower() or
                    query_lower in (party.mailto or '').lower()):
                    return True
            
            # Search in dialogs
            for dialog in vcon.dialog:
                if dialog.get('type') == 'text':
                    body = dialog.get('body', '')
                    if query_lower in body.lower():
                        return True
            
            # Search in tags
            for tag_value in vcon.tags.values():
                if query_lower in str(tag_value).lower():
                    return True
            
            return False
            
        except Exception as e:
            logger.warning(f"Error searching vCon content: {e}")
            return False
    
    def organize_by_date(self) -> bool:
        """Organize vCon files into date-based subdirectories."""
        try:
            organized_count = 0
            
            for file_path in self.storage_path.glob("*.json"):
                try:
                    # Get file modification date
                    mod_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                    date_dir = mod_time.strftime("%Y-%m-%d")
                    
                    # Create date directory
                    date_path = self.storage_path / date_dir
                    date_path.mkdir(exist_ok=True)
                    
                    # Move file
                    new_path = date_path / file_path.name
                    file_path.rename(new_path)
                    organized_count += 1
                    
                except Exception as e:
                    logger.warning(f"Could not organize {file_path}: {e}")
            
            logger.info(f"Organized {organized_count} files by date")
            return True
            
        except Exception as e:
            logger.error(f"Error organizing files by date: {e}")
            return False
