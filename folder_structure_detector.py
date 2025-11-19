"""
Folder Structure Detector
Detects different folder structures for addons and plugins
"""

from pathlib import Path

class FolderStructureDetector:
    def __init__(self):
        """Initialize folder structure detector."""
        pass
    
    def detect_all_addons(self, source_path):
        """Detect all addons in a repository (for monorepos).
        
        Args:
            source_path: Path to search for addons
        
        Returns:
            A list of addon info dicts, each with:
            - found: bool - whether addon was found
            - name: str - addon name
            - path: Path - path to addon folder
            - structure: str - 'nested' or 'root'
            - repo_root: Path - root of repository
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
    
    def detect_addon_structure(self, source_path, target_name=None, repo_url=None):
        """Detect addon folder structure.
        
        Args:
            source_path: Path to search for addon
            target_name: Optional specific addon name to find (useful for monorepos)
            repo_url: Optional repository URL to extract addon name from
        
        Returns:
            A dict with:
            - found: bool - whether an addon was found
            - name: str - addon name
            - path: Path - path to addon folder/files
            - structure: str - 'root' or 'nested'
            - ambiguous: bool - True if multiple lua files found but cannot determine name
            - lua_files: list - list of lua file names (if ambiguous)
        """
        source_path = Path(source_path)
        
        # Check if there are lua files at the root level first
        # If yes, this is the addon folder (don't descend into subdirectories)
        root_lua_files = list(source_path.glob('*.lua'))
        has_root_lua = len(root_lua_files) > 0
        
        # First, check if there's a single subdirectory (only if no lua files at root)
        # Exclude .git and other hidden folders from this check
        subdirs = [d for d in source_path.iterdir() if d.is_dir() and not d.name.startswith('.')]
        if len(subdirs) == 1 and not has_root_lua:
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
            # Infer addon name from the lua file or parent folder
            addon_name = self._infer_addon_name(actual_source, lua_files, repo_url)
            if addon_name:
                return {
                    'found': True,
                    'name': addon_name,
                    'path': actual_source,
                    'structure': 'root'
                }
            else:
                # Could not determine addon name, return ambiguous result
                return {
                    'found': False,
                    'ambiguous': True,
                    'lua_files': [lua.stem for lua in lua_files],
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
        """Detect plugin folder structure.
        
        Args:
            source_path: Path to search for plugin
            target_name: Optional specific plugin name to find (useful for monorepos)
        
        Returns:
            A dict with:
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
    
    def _infer_addon_name(self, folder_path, lua_files, repo_url=None):
        """Infer addon name from folder path and lua files.
        
        Args:
            folder_path: Path to the addon folder
            lua_files: List of lua file Paths in the folder
            repo_url: Optional repository URL to extract addon name from
        
        Returns:
            str - The inferred addon name, or None if cannot determine
        """
        folder_path = Path(folder_path)
        folder_name = folder_path.name
        folder_name_lower = folder_name.lower()
        
        # Step 0: Check if repo URL provides a name match
        if repo_url:
            repo_name = repo_url.rstrip('/').split('/')[-1].lower()
            # Check for exact match first
            for lua_file in lua_files:
                if lua_file.stem.lower() == repo_name:
                    return lua_file.stem
        else:
            repo_name = None
        
        # Step 1: If only one lua file exists, it's probably the main one
        if len(lua_files) == 1:
            return lua_files[0].stem
        
        # Step 2: Check if any lua file name exactly matches the folder name (case-insensitive)
        for lua_file in lua_files:
            if lua_file.stem.lower() == folder_name_lower:
                return lua_file.stem
        
        # Step 3: Check if any lua file name is a substring match with the folder name or repo name
        best_match = None
        best_match_length = 0
        
        for lua_file in lua_files:
            lua_name_lower = lua_file.stem.lower()
            
            # Check if lua name appears in folder name
            if lua_name_lower in folder_name_lower and len(lua_name_lower) > best_match_length:
                best_match = lua_file.stem
                best_match_length = len(lua_name_lower)
            
            # Check if folder name appears in lua name
            elif folder_name_lower in lua_name_lower and len(folder_name_lower) > best_match_length:
                best_match = lua_file.stem
                best_match_length = len(folder_name_lower)
            
            # Check if lua name appears in repo name (from URL)
            if repo_name and lua_name_lower in repo_name and len(lua_name_lower) > best_match_length:
                best_match = lua_file.stem
                best_match_length = len(lua_name_lower)
            
            # Check if repo name appears in lua name
            elif repo_name and repo_name in lua_name_lower and len(repo_name) > best_match_length:
                best_match = lua_file.stem
                best_match_length = len(repo_name)
        
        if best_match and best_match_length >= 3:  # Require at least 3 chars to match
            return best_match
        
        # Step 4: Return None to signal that user selection is needed
        return None
    
    def has_docs_folder(self, source_path):
        """Check if source has a docs folder.
        
        Args:
            source_path: Path to search for docs folder
        
        Returns:
            A tuple of (bool - whether docs folder exists, Path - path to docs folder or None)
        """
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
        """Check if source has a resources folder.
        
        Args:
            source_path: Path to search for resources folder
        
        Returns:
            A tuple of (bool - whether resources folder exists, Path - path to resources folder or None)
        """
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
