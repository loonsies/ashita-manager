"""
Folder Structure Detector
Detects different folder structures for addons and plugins
"""

import os
from pathlib import Path


class FolderStructureDetector:
    def __init__(self):
        pass
    
    def detect_all_addons(self, source_path):
        """
        Detect ALL addons in a repository (for monorepos)
        
        Returns a list of addon info dicts, each with:
        - found: bool
        - name: str
        - path: Path
        - structure: str
        """
        source_path = Path(source_path)
        addons = []
        
        # First, check if there's a single subdirectory
        # Exclude .git and other hidden folders from this check
        subdirs = [d for d in source_path.iterdir() if d.is_dir() and not d.name.startswith('.')]
        if len(subdirs) == 1:
            actual_source = subdirs[0]
        else:
            actual_source = source_path
        
        # Check for 'addons' folder in the structure
        addons_folder = actual_source / 'addons'
        if addons_folder.exists() and addons_folder.is_dir():
            # Find all addon folders inside
            for addon_dir in addons_folder.iterdir():
                if addon_dir.is_dir() and addon_dir.name != 'libs':
                    # Check if main .lua file exists
                    main_lua = addon_dir / f"{addon_dir.name}.lua"
                    if main_lua.exists():
                        addons.append({
                            'found': True,
                            'name': addon_dir.name,
                            'path': addon_dir,
                            'structure': 'nested',
                            'repo_root': actual_source
                        })
        
        # If we found addons, return them all
        if addons:
            return addons
        
        # Fall back to single addon detection
        single = self.detect_addon_structure(source_path, None)
        if single['found']:
            single['repo_root'] = actual_source
            return [single]
        
        return []
    
    def detect_addon_structure(self, source_path, target_name=None):
        """
        Detect addon folder structure
        
        Args:
            source_path: Path to search for addon
            target_name: Optional specific addon name to find (useful for monorepos)
        
        Returns a dict with:
        - found: bool - whether an addon was found
        - name: str - addon name
        - path: Path - path to the addon folder/files
        - structure: str - 'root' or 'nested'
        """
        source_path = Path(source_path)
        
        # First, check if there's a single subdirectory
        # Exclude .git and other hidden folders from this check
        subdirs = [d for d in source_path.iterdir() if d.is_dir() and not d.name.startswith('.')]
        if len(subdirs) == 1:
            # Check inside the single subdirectory
            actual_source = subdirs[0]
        else:
            actual_source = source_path
        
        # Pattern 1: Check for 'addons' folder in the structure
        addons_folder = actual_source / 'addons'
        if addons_folder.exists() and addons_folder.is_dir():
            # If target_name specified, look for that specific addon
            if target_name:
                target_addon = addons_folder / target_name
                if target_addon.is_dir():
                    main_lua = target_addon / f"{target_name}.lua"
                    if main_lua.exists():
                        return {
                            'found': True,
                            'name': target_name,
                            'path': target_addon,
                            'structure': 'nested'
                        }
            else:
                # Look for addon folders inside (return first found)
                for addon_dir in addons_folder.iterdir():
                    if addon_dir.is_dir() and addon_dir.name != 'libs':
                        # Check if main .lua file exists
                        main_lua = addon_dir / f"{addon_dir.name}.lua"
                        if main_lua.exists():
                            return {
                                'found': True,
                                'name': addon_dir.name,
                                'path': addon_dir,
                                'structure': 'nested'
                            }
        
        # Pattern 2: Root contains .lua files directly
        # Look for .lua files at root
        lua_files = list(actual_source.glob('*.lua'))
        if lua_files:
            # Try to find the main addon file (usually matches a pattern)
            for lua_file in lua_files:
                # Check if there's a matching lua file with the parent folder name
                # or if there are multiple lua files suggesting this is the addon root
                if len(lua_files) >= 1:
                    # Infer addon name from the lua file or parent folder
                    addon_name = self._infer_addon_name(actual_source, lua_files)
                    return {
                        'found': True,
                        'name': addon_name,
                        'path': actual_source,
                        'structure': 'root'
                    }
        
        # Pattern 3: Single folder at root that contains the addon
        for item in actual_source.iterdir():
            if item.is_dir():
                main_lua = item / f"{item.name}.lua"
                if main_lua.exists():
                    return {
                        'found': True,
                        'name': item.name,
                        'path': item,
                        'structure': 'nested'
                    }
        
        return {'found': False, 'name': None, 'path': None, 'structure': None}
    
    def detect_plugin_structure(self, source_path, target_name=None):
        """
        Detect plugin folder structure
        
        Args:
            source_path: Path to search for plugin
            target_name: Optional specific plugin name to find (useful for monorepos)
        
        Returns a dict with:
        - found: bool - whether a plugin was found
        - name: str - plugin name (without .dll extension)
        - dll_path: Path - path to the .dll file
        """
        source_path = Path(source_path)
        
        # First, check if there's a single subdirectory
        # Exclude .git and other hidden folders from this check
        subdirs = [d for d in source_path.iterdir() if d.is_dir() and not d.name.startswith('.')]
        if len(subdirs) == 1:
            # Check inside the single subdirectory
            actual_source = subdirs[0]
        else:
            actual_source = source_path
        
        # Pattern 1: Look for 'plugins' folder
        plugins_folder = actual_source / 'plugins'
        if plugins_folder.exists() and plugins_folder.is_dir():
            # If target_name specified, look for that specific plugin
            if target_name:
                target_dll = plugins_folder / f"{target_name}.dll"
                if target_dll.exists():
                    return {
                        'found': True,
                        'name': target_name,
                        'dll_path': target_dll
                    }
            else:
                # Find .dll files (return first found)
                dll_files = list(plugins_folder.glob('*.dll'))
                if dll_files:
                    dll_file = dll_files[0]  # Take first .dll found
                    return {
                        'found': True,
                        'name': dll_file.stem,
                        'dll_path': dll_file
                    }
        
        # Pattern 2: .dll at root level
        dll_files = list(actual_source.glob('*.dll'))
        if dll_files:
            dll_file = dll_files[0]  # Take first .dll found
            return {
                'found': True,
                'name': dll_file.stem,
                'dll_path': dll_file
            }
        
        # Pattern 3: Search recursively (max 2 levels deep)
        for dll_file in actual_source.rglob('*.dll'):
            # Don't go too deep
            relative = dll_file.relative_to(actual_source)
            if len(relative.parts) <= 2:
                return {
                    'found': True,
                    'name': dll_file.stem,
                    'dll_path': dll_file
                }
        
        return {'found': False, 'name': None, 'dll_path': None}
    
    def _infer_addon_name(self, folder_path, lua_files):
        """
        Infer addon name from folder and lua files
        """
        folder_path = Path(folder_path)
        
        # Check if any lua file matches the folder name
        folder_name = folder_path.name
        for lua_file in lua_files:
            if lua_file.stem.lower() == folder_name.lower():
                return lua_file.stem
        
        # Check for common main file names
        common_names = ['init', 'main', folder_name]
        for name in common_names:
            for lua_file in lua_files:
                if lua_file.stem.lower() == name.lower():
                    return name
        
        # Fallback: use the first lua file's name
        if lua_files:
            return lua_files[0].stem
        
        # Last resort: use folder name
        return folder_name
    
    def has_docs_folder(self, source_path):
        """Check if source has a docs folder"""
        source_path = Path(source_path)
        
        # Check common locations
        docs_locations = [
            source_path / 'docs',
            source_path / 'Docs',
        ]
        
        for location in docs_locations:
            if location.exists() and location.is_dir():
                return True, location
        
        return False, None
    
    def has_resources_folder(self, source_path):
        """Check if source has a resources folder"""
        source_path = Path(source_path)
        
        # Check common locations
        resources_locations = [
            source_path / 'resources',
            source_path / 'Resources',
        ]
        
        for location in resources_locations:
            if location.exists() and location.is_dir():
                return True, location
        
        return False, None
