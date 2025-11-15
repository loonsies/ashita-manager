"""
Package Tracker
Manages the ashita-packages.json file to track installed packages
"""

import json
from pathlib import Path
from datetime import datetime

class PackageTracker:
    def __init__(self, ashita_root):
        self.ashita_root = Path(ashita_root)
        self.tracker_file = self.ashita_root / 'ashita-packages.json'
        self.packages = self._load_packages()
    
    def _load_packages(self):
        """Load packages from ashita-packages.json"""
        if self.tracker_file.exists():
            try:
                with open(self.tracker_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return self._create_empty_structure()
        else:
            return self._create_empty_structure()
    
    def _create_empty_structure(self):
        """Create empty package structure"""
        return {
            'version': '1.0',
            'last_updated': datetime.now().isoformat(),
            'addons': {},
            'plugins': {},
            'settings': {}
        }
    
    def save_packages(self):
        """Save packages to ashita-packages.json"""
        self.packages['last_updated'] = datetime.now().isoformat()
        try:
            with open(self.tracker_file, 'w', encoding='utf-8') as f:
                json.dump(self.packages, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving packages: {e}")
            return False

    def is_first_launch(self):
        has_ashita_path = bool(self.get_setting('ashita_path', ''))
        has_packages = len(self.packages.get('addons', {})) > 0 or len(self.packages.get('plugins', {})) > 0
        return has_ashita_path and not has_packages
    
    def add_package(self, name, pkg_type, package_info):
        if pkg_type not in ['addon', 'plugin']:
            return False
        
        type_key = f"{pkg_type}s"  # 'addons' or 'plugins'
        
        self.packages[type_key][name] = package_info
        return self.save_packages()
    
    def remove_package(self, name, pkg_type):
        """Remove a package from the tracker"""
        if pkg_type not in ['addon', 'plugin']:
            return False
        
        type_key = f"{pkg_type}s"
        
        if name in self.packages[type_key]:
            del self.packages[type_key][name]
            return self.save_packages()
        
        return False
    
    def get_package(self, name, pkg_type):
        """Get information about a specific package"""
        if pkg_type not in ['addon', 'plugin']:
            return None
        
        type_key = f"{pkg_type}s"
        return self.packages[type_key].get(name)
    
    def get_all_packages(self):
        """Get all tracked packages"""
        return {
            'addons': self.packages.get('addons', {}),
            'plugins': self.packages.get('plugins', {})
        }
    
    def package_exists(self, name, pkg_type):
        """Check if a package exists in the tracker"""
        if pkg_type not in ['addon', 'plugin']:
            return False
        
        type_key = f"{pkg_type}s"
        return name in self.packages[type_key]
    
    def get_package_count(self):
        """Get count of tracked packages"""
        return {
            'addons': len(self.packages.get('addons', {})),
            'plugins': len(self.packages.get('plugins', {}))
        }
    
    def update_package_info(self, name, pkg_type, updates):
        """Update specific fields of a package"""
        if pkg_type not in ['addon', 'plugin']:
            return False
        
        type_key = f"{pkg_type}s"
        
        if name in self.packages[type_key]:
            self.packages[type_key][name].update(updates)
            return self.save_packages()
        
        return False
    
    def export_packages(self, output_file):
        """Export package list to a file"""
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(self.packages, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False
    
    def import_packages(self, input_file):
        """Import package list from a file"""
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                imported = json.load(f)
                
            # Validate structure
            if 'addons' in imported and 'plugins' in imported:
                self.packages = imported
                return self.save_packages()
            
            return False
        except Exception:
            return False
    
    def get_setting(self, key, default=None):
        """Get a setting value"""
        if 'settings' not in self.packages:
            self.packages['settings'] = {}
        return self.packages['settings'].get(key, default)
    
    def set_setting(self, key, value):
        """Set a setting value"""
        if 'settings' not in self.packages:
            self.packages['settings'] = {}
        self.packages['settings'][key] = value
        return self.save_packages()
    
    def get_all_settings(self):
        """Get all settings"""
        if 'settings' not in self.packages:
            self.packages['settings'] = {}
        return self.packages['settings']
