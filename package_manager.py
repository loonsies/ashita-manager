"""
Package Manager
Handles installation, updates, and removal of Ashita addons and plugins
"""

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import stat
import hashlib
import requests
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from folder_structure_detector import FolderStructureDetector


class PackageManager:
    def __init__(self, ashita_root, package_tracker):
        """Initialize package manager.
        
        Args:
            ashita_root: str/Path - Root directory of Ashita installation
            package_tracker: PackageTracker - Package tracking instance
        """
        self.ashita_root = Path(ashita_root)
        self.addons_dir = self.ashita_root / "addons"
        self.plugins_dir = self.ashita_root / "plugins"
        self.docs_dir = self.ashita_root / "docs"
        self.package_tracker = package_tracker
        self.detector = FolderStructureDetector()
        self.official_repo = "https://github.com/AshitaXI/Ashita-v4beta"
        self.official_repo_branch = self._detect_current_branch()
        
        # Ensure directories exist
        self.addons_dir.mkdir(exist_ok=True)
        self.plugins_dir.mkdir(exist_ok=True)
        self.docs_dir.mkdir(exist_ok=True)

    def _run_command(self, cmd, cwd=None, **kwargs):
        """Run a subprocess command while avoiding new console window on Windows.
        
        Args:
            cmd: list - Command and arguments
            cwd: Optional str - Working directory
            **kwargs: Additional subprocess.run arguments
        
        Returns:
            subprocess.CompletedProcess - Process result with returncode, stdout, stderr
        """
        if os.name == 'nt':
            kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
        return subprocess.run(cmd, cwd=cwd, **kwargs)
    
    def _handle_remove_readonly(self, func, path, exc):
        """Handle Windows file deletion errors.
        
        Args:
            func: callable - Function that raised the error
            path: str - File path causing the error
            exc: Exception - Exception info tuple
        
        Returns:
            None - Handles error in-place
        """
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    def _detect_git_metadata(self, repo_path):
        """Extract git metadata from local repository.
        
        Args:
            repo_path: str/Path - Path to git repository
        
        Returns:
            dict - Git metadata with keys:
            - remote: str - Origin URL
            - branch: str - Current branch name
            - commit: str - Current commit hash
            Or empty dict if not a valid git repository
        """
        repo_path = Path(repo_path)
        if not (repo_path / '.git').exists():
            return None

        metadata = {}
        try:
            remote_result = self._run_command(
                ['git', 'remote', 'get-url', 'origin'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if remote_result.returncode == 0:
                metadata['source'] = remote_result.stdout.strip()
        except Exception:
            pass

        try:
            branch_result = self._run_command(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if branch_result.returncode == 0:
                metadata['branch'] = branch_result.stdout.strip()
        except Exception:
            pass

        try:
            commit_result = self._run_command(
                ['git', 'rev-parse', 'HEAD'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if commit_result.returncode == 0:
                metadata['commit'] = commit_result.stdout.strip()
        except Exception:
            pass

        return metadata if metadata else None
    
    def _remove_directory_safe(self, path):
        """Safely remove directory handling Windows file locks.
        
        Args:
            path: str/Path - Directory to remove
        
        Returns:
            bool - True if successfully removed, False if failed
        """
        if not path.exists():
            return
        
        try:
            # First try normal deletion
            shutil.rmtree(path)
        except Exception:
            # On Windows, use onerror handler to deal with readonly/locked files
            try:
                shutil.rmtree(path, onerror=self._handle_remove_readonly)
            except Exception:
                # Last resort: use Windows rmdir command
                if os.name == 'nt':
                    self._run_command(
                        ['cmd', '/c', 'rmdir', '/S', '/Q', str(path)],
                        capture_output=True
                    )
                else:
                    raise
    
    def _detect_current_branch(self):
        """Detect current git branch of Ashita installation.
        
        Returns:
            str - Branch name or 'main' if detection fails
        """
        try:
            result = self._run_command(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=self.ashita_root,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                branch = result.stdout.strip()
                if branch and branch != 'HEAD':
                    return branch
        except Exception:
            pass
        # Default to main if detection fails
        return 'main'
    
    def install_from_git(self, url, pkg_type, target_package_name=None, branch=None, force=False, plugin_variant=None, selected_entrypoint=None):
        """Install a package by cloning from git.
        
        Args:
            url: Git repository URL
            pkg_type: str - 'addon' or 'plugin'
            target_package_name: Optional str - specific package name to extract (for monorepos)
            branch: Optional str - specific branch to clone (defaults to repo's default)
            force: bool - Skip conflict checking if True
            plugin_variant: Optional str - specific plugin variant to install
            selected_entrypoint: Optional str - entrypoint lua file name for ambiguous addon detection
        
        Returns:
            dict - Installation result with keys:
            - success: bool - whether installation succeeded
            - message: str - success message
            - error: str - error message if failed
            - requires_confirmation: bool - file conflicts detected
            - requires_variant_selection: bool - user selection needed
            - requires_entrypoint_selection: bool - lua file selection needed
        """
        try:
            # Create temporary directory for cloning
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Build clone command with optional branch
                clone_cmd = ['git', 'clone', '--recurse-submodules']
                if branch:
                    clone_cmd.extend(['--branch', branch])
                clone_cmd.extend([url, str(temp_path / 'repo')])
                
                # Clone repository
                result = self._run_command(
                    clone_cmd,
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0:
                    return {'success': False, 'error': f'Git clone failed: {result.stderr}'}
                
                repo_path = temp_path / 'repo'
                
                # Get commit hash
                commit_result = self._run_command(
                    ['git', 'rev-parse', 'HEAD'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True
                )
                commit_hash = commit_result.stdout.strip()
                
                # Get branch name
                branch_result = self._run_command(
                    ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True
                )
                branch_name = branch_result.stdout.strip()
                
                # Detect structure and install
                if pkg_type == 'addon':
                    # Check if this is a monorepo with multiple addons
                    all_addons = self.detector.detect_all_addons(repo_path)
                    
                    if len(all_addons) > 1:
                        # Monorepo - check for conflicts first if not forcing
                        if not force:
                            all_conflicts = {}
                            has_conflicts = False
                            
                            for addon_info in all_addons:
                                addon_name = addon_info['name']
                                conflicts = self._check_file_conflicts(repo_path, addon_name, url)
                                if conflicts['libs'] or conflicts['docs'] or conflicts['resources']:
                                    all_conflicts[addon_name] = conflicts
                                    has_conflicts = True
                            
                            if has_conflicts:
                                # Return combined conflict info for all addons
                                return {
                                    'success': False, 
                                    'error': 'File conflicts detected in monorepo addons', 
                                    'conflicts': all_conflicts, 
                                    'requires_confirmation': True,
                                    'monorepo': True,
                                    'all_addons': all_addons
                                }
                        
                        # Monorepo - install all addons
                        installed_count = 0
                        failed = []
                        warnings = []
                        
                        for addon_info in all_addons:
                            result = self._install_single_addon(
                                addon_info, url, commit_hash, branch_name, None, repo_path, force=force
                            )
                            if result['success']:
                                installed_count += 1
                                if 'warnings' in result['message']:
                                    warnings.append(f"{addon_info['name']}: {result['message']}")
                            else:
                                failed.append(f"{addon_info['name']}: {result['error']}")
                        
                        # Save once after all addons are installed
                        self.package_tracker.save_packages()
                        
                        if installed_count > 0:
                            msg = f"Installed {installed_count} addon(s) from monorepo"
                            if failed:
                                msg += f" ({len(failed)} failed)"
                                for failure in failed:
                                    msg += f"\n{failure}"
                            if warnings:
                                for warning in warnings:
                                    msg += f"\n{warning}"
                            return {'success': True, 'message': msg}
                        else:
                            error_msg = "Failed to install addons:"
                            for failure in failed:
                                error_msg += f"\n{failure}"
                            return {'success': False, 'error': error_msg}
                    else:
                        # Single addon
                        result = self._install_addon(repo_path, url, commit_hash, branch_name, None, target_package_name, force=force, selected_entrypoint=selected_entrypoint)
                else:
                    # Plugin repo: look for variant folders containing .dll files
                    # For official repo, only look in plugins/ folder for the specific plugin
                    # For other repos, check for variant subfolders
                    variants = []
                    try:
                        if url == self.official_repo:
                            # Official repo: use standard plugin detection (no variants)
                            return self._install_plugin(repo_path, url, commit_hash, branch_name, None, target_package_name, force=force)
                        else:
                            # Non-official repo: look for variant folders
                            for p in repo_path.rglob('*'):
                                if p.is_dir():
                                    dlls = list(p.glob('*.dll'))
                                    if dlls:
                                        variants.append({'path': p, 'name': p.name, 'dlls': dlls})
                    except Exception:
                        variants = []

                    sel_path = None
                    sel_dlls = []
                    sel_name = None

                    if plugin_variant:
                        # try to match provided variant by folder name
                        for v in variants:
                            if v['name'] == plugin_variant:
                                sel_path = v['path']
                                sel_dlls = v['dlls']
                                sel_name = v['name']
                                break
                        if not sel_path:
                            return {'success': False, 'error': f'Plugin variant "{plugin_variant}" not found in repository'}
                    else:
                        if variants:
                            if len(variants) == 1:
                                sel_path = variants[0]['path']
                                sel_dlls = variants[0]['dlls']
                                sel_name = variants[0]['name']
                            else:
                                # multiple variants found, request UI selection
                                choices = [{'name': v['name'], 'version': None} for v in variants]
                                return {
                                    'success': False,
                                    'requires_variant_selection': True,
                                    'variants': choices,
                                    'repo_url': url
                                }
                        else:
                            # no variant folders found; fall back to standard plugin installer
                            return self._install_plugin(repo_path, url, commit_hash, branch_name, None, target_package_name, force=force)

                    # If we have a selected path with DLLs, install first DLL
                    if sel_path and sel_dlls:
                        dll_path = sel_dlls[0]
                        plugin_name = dll_path.stem
                        target_dll = self.plugins_dir / f"{plugin_name}.dll"

                        if target_dll.exists():
                            existing_pkg = self.package_tracker.get_package(plugin_name, 'plugin')
                            if existing_pkg and existing_pkg.get('source') == self.official_repo and url == self.official_repo:
                                target_dll.unlink()
                            else:
                                return {'success': False, 'error': f'Plugin "{plugin_name}.dll" already exists'}

                        shutil.copy2(dll_path, target_dll)

                        package_info = {
                            'source': url,
                            'install_method': 'git',
                            'installed_date': datetime.now().isoformat(),
                            'path': str(target_dll.relative_to(self.ashita_root))
                        }
                        if commit_hash:
                            package_info['commit'] = commit_hash
                            package_info['branch'] = branch_name

                        self.package_tracker.add_package(plugin_name, 'plugin', package_info)
                        try:
                            self._copy_extra_folders(repo_path, plugin_name, pkg_type='plugin')
                        except Exception:
                            pass
                        self.package_tracker.save_packages()
                        return {'success': True, 'message': f'Plugin "{plugin_name}" installed successfully'}
                
                return result
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def install_from_release(self, url, pkg_type, force=False, plugin_variant=None, asset_download_url=None, asset_name=None, selected_entrypoint=None):
        """Install a package from a GitHub release.
        
        Args:
            url: str - Repository URL
            pkg_type: str - 'addon' or 'plugin'
            force: bool - Skip conflict checking if True
            plugin_variant: Optional str - specific plugin variant to install
            asset_download_url: Optional str - direct download URL for specific asset
            asset_name: Optional str - preferred asset name
            selected_entrypoint: Optional str - entrypoint lua file name for ambiguous addon detection
        
        Returns:
            dict - Installation result with same keys as install_from_git
        """
        try:
            if asset_download_url:
                release_url = (asset_download_url, asset_name or self._infer_asset_name(asset_download_url))
            else:
                release_url = self._get_latest_release_url(url, preferred_asset_name=asset_name)
            
            if isinstance(release_url, dict):
                if release_url.get('rate_limited'):
                    return {'success': False, 'rate_limited': True, 'error': release_url.get('message', 'GitHub API rate limit exceeded')}
                elif release_url.get('multiple_assets'):
                    assets = release_url['assets']
                    return {
                        'success': False,
                        'requires_variant_selection': True,
                        'variants': [{'name': a['name'], 'version': a['name'], 'url': a.get('url') or a.get('download_url') or a.get('browser_download_url')} for a in assets],
                        'repo_url': url,
                        'is_release_asset': True
                    }
            
            if not release_url:
                return {'success': False, 'error': 'Could not find release download URL'}

            if isinstance(release_url, tuple):
                download_url, release_asset_name = release_url
            else:
                download_url = release_url
                release_asset_name = asset_name or self._infer_asset_name(release_url)
            
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                zip_path = temp_path / 'release.zip'
                
                response = requests.get(download_url, stream=True)
                response.raise_for_status()
                
                with open(zip_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # Check if the downloaded file is a .dll directly
                if release_asset_name and release_asset_name.lower().endswith('.dll'):
                    # Handle direct .dll file for plugins
                    if pkg_type == 'plugin':
                        dll_file = zip_path  # It's actually the dll file, not a zip
                        dll_file_renamed = temp_path / release_asset_name
                        dll_file.rename(dll_file_renamed) if dll_file != dll_file_renamed else None
                        
                        plugin_name = dll_file_renamed.stem
                        target_dll = self.plugins_dir / f"{plugin_name}.dll"
                        
                        if target_dll.exists():
                            existing_pkg = self.package_tracker.get_package(plugin_name, 'plugin')
                            if existing_pkg and existing_pkg.get('source') == self.official_repo and url == self.official_repo:
                                target_dll.unlink()
                            else:
                                return {'success': False, 'error': f'Plugin "{plugin_name}.dll" already exists'}
                        
                        shutil.copy2(dll_file_renamed, target_dll)
                        
                        release_tag = self._get_release_tag(url)
                        package_info = {
                            'source': url,
                            'install_method': 'release',
                            'installed_date': datetime.now().isoformat(),
                            'path': str(target_dll.relative_to(self.ashita_root)),
                            'release_tag': release_tag,
                            'release_asset_name': release_asset_name
                        }
                        self.package_tracker.add_package(plugin_name, 'plugin', package_info)
                        self.package_tracker.save_packages()
                        return {'success': True, 'message': f'Plugin "{plugin_name}" installed successfully'}
                    else:
                        return {'success': False, 'error': f'Cannot install addon from .dll file. Expected .zip archive'}
                
                extract_path = temp_path / 'extracted'
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_path)
                
                release_tag = self._get_release_tag(url)
                
                if pkg_type == 'addon':
                    result = self._install_addon(
                        extract_path,
                        url,
                        None,
                        None,
                        release_tag,
                        force=force,
                        release_asset_name=release_asset_name,
                        selected_entrypoint=selected_entrypoint
                    )
                else:
                    # Search extracted tree for variant folders containing DLLs
                    variants = []
                    try:
                        for p in extract_path.rglob('*'):
                            if p.is_dir():
                                dlls = list(p.glob('*.dll'))
                                if dlls:
                                    variants.append({'path': p, 'name': p.name, 'dlls': dlls})
                    except Exception:
                        variants = []

                    sel_path = None
                    sel_dlls = []
                    sel_name = None

                    if plugin_variant:
                        chosen = None
                        for v in variants:
                            if v['name'] == plugin_variant:
                                chosen = v
                                break
                        if chosen:
                            sel_path = chosen['path']
                            sel_dlls = chosen['dlls']
                            sel_name = chosen['name']
                        else:
                            return {'success': False, 'error': f'Plugin variant "{plugin_variant}" not found in release'}
                    else:
                        if variants:
                            if len(variants) == 1:
                                sel_path = variants[0]['path']
                                sel_dlls = variants[0]['dlls']
                                sel_name = variants[0]['name']
                            else:
                                choices = [{'name': v['name'], 'version': None} for v in variants]
                                return {
                                    'success': False,
                                    'requires_variant_selection': True,
                                    'variants': choices,
                                    'repo_url': url
                                }
                        else:
                            # No variants found; fallback to installer
                            return self._install_plugin(
                                extract_path,
                                url,
                                None,
                                None,
                                release_tag,
                                force=force,
                                release_asset_name=release_asset_name
                            )

                    if sel_path and sel_dlls:
                        dll_path = sel_dlls[0]
                        plugin_name = dll_path.stem
                        target_dll = self.plugins_dir / f"{plugin_name}.dll"

                        if target_dll.exists():
                            existing_pkg = self.package_tracker.get_package(plugin_name, 'plugin')
                            if existing_pkg and existing_pkg.get('source') == self.official_repo and url == self.official_repo:
                                target_dll.unlink()
                            else:
                                return {'success': False, 'error': f'Plugin "{plugin_name}.dll" already exists'}

                        shutil.copy2(dll_path, target_dll)

                        package_info = {
                            'source': url,
                            'install_method': 'release',
                            'installed_date': datetime.now().isoformat(),
                            'path': str(target_dll.relative_to(self.ashita_root)),
                            'release_tag': release_tag
                        }
                        if release_asset_name:
                            package_info['release_asset_name'] = release_asset_name
                        self.package_tracker.add_package(plugin_name, 'plugin', package_info)
                        try:
                            self._copy_extra_folders(extract_path, plugin_name, pkg_type='plugin')
                        except Exception:
                            pass
                        self.package_tracker.save_packages()
                        return {'success': True, 'message': f'Plugin "{plugin_name}" installed successfully'}
                
                return result
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _install_single_addon(self, addon_info, url, commit_hash=None, branch_name=None, release_tag=None, repo_root=None, force=False, release_asset_name=None):
        """Install single addon from monorepo addon_info dict.
        
        Args:
            addon_info: dict - Addon info with keys: name, path, structure
            url: str - Source repository URL
            commit_hash: Optional str - Git commit hash
            branch_name: Optional str - Git branch name
            release_tag: Optional str - Release tag
            repo_root: Optional str/Path - Root directory of extracted repo
            force: bool - Skip conflict checking
            release_asset_name: Optional str - Release asset filename
        
        Returns:
            dict - Installation result with success/error and package info
        """
        try:
            addon_name = addon_info['name']
            addon_source = addon_info['path']
            
            target_dir = self.addons_dir / addon_name
            
            if target_dir.exists():
                existing_pkg = self.package_tracker.get_package(addon_name, 'addon')
                if existing_pkg and existing_pkg.get('source') == self.official_repo and url == self.official_repo:
                    self._remove_directory_safe(target_dir)
                else:
                    return {'success': False, 'error': f'Addon "{addon_name}" already exists'}
            
            # Check for file conflicts (will skip conflicts from same repository unless force is False)
            if repo_root and not force:
                conflicts = self._check_file_conflicts(repo_root, addon_name, url)
                if conflicts['libs'] or conflicts['docs'] or conflicts['resources']:
                    return {'success': False, 'error': 'File conflicts detected', 'conflicts': conflicts, 'requires_confirmation': True}
            
            # Copy addon files
            shutil.copytree(addon_source, target_dir)
            
            # Track package
            install_method = 'git' if commit_hash else 'release'
            package_info = {
                'source': url,
                'install_method': install_method,
                'installed_date': datetime.now().isoformat(),
                'path': str(target_dir.relative_to(self.ashita_root))
            }

            if release_asset_name:
                package_info['release_asset_name'] = release_asset_name
            
            if commit_hash:
                # For monorepos, get folder-specific commit
                if url == self.official_repo:
                    folder_path = f'addons/{addon_name}'
                    folder_commit_result = self._run_command(
                        ['git', 'log', '-1', '--format=%H', '--', folder_path],
                        cwd=repo_root,
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if folder_commit_result.returncode == 0 and folder_commit_result.stdout.strip():
                        package_info['commit'] = folder_commit_result.stdout.strip()
                    else:
                        package_info['commit'] = commit_hash
                else:
                    package_info['commit'] = commit_hash
                package_info['branch'] = branch_name
            
            if release_tag:
                package_info['release_tag'] = release_tag
            
            self.package_tracker.add_package(addon_name, 'addon', package_info)
            
            # Copy extra folders (libs, docs, resources)
            extra_errors = []
            if repo_root:
                extra_errors = self._copy_extra_folders(repo_root, addon_name, pkg_type='addon', is_monorepo=True)
            
            msg = f'Addon "{addon_name}" installed successfully'
            if extra_errors:
                msg += f' (with warnings: {"; ".join(extra_errors)})'
            
            return {'success': True, 'message': msg}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _install_addon(self, source_path, url, commit_hash=None, branch_name=None, release_tag=None, target_name=None, force=False, release_asset_name=None, selected_entrypoint=None):
        """Install addon from extracted source directory.
        
        Args:
            source_path: str/Path - Extracted addon source directory
            url: str - Source repository URL
            commit_hash: Optional str - Git commit hash
            branch_name: Optional str - Git branch name
            release_tag: Optional str - Release tag
            target_name: Optional str - Specific addon name to install (for monorepos)
            force: bool - Skip conflict checking
            release_asset_name: Optional str - Release asset filename
            selected_entrypoint: Optional str - Entrypoint lua file for ambiguous addons
        
        Returns:
            dict - Installation result with keys:
            - success: bool - Whether installation succeeded
            - requires_entrypoint_selection: bool - User lua file selection needed
            - lua_files: list - Available lua files (if ambiguous)
            - error: str - Error message if failed
        """
        try:
            repo_root = source_path
            addon_info = self.detector.detect_addon_structure(source_path, target_name, url)
            
            # Handle ambiguous addon name detection
            if not addon_info['found'] and addon_info.get('ambiguous'):
                if not selected_entrypoint:
                    # Return lua files for user to select
                    return {
                        'success': False,
                        'requires_entrypoint_selection': True,
                        'lua_files': addon_info['lua_files'],
                        'source_url': url,
                        'is_git': bool(commit_hash),
                        'is_release': bool(release_tag)
                    }
                else:
                    # User has selected an entrypoint
                    addon_info = {
                        'found': True,
                        'name': selected_entrypoint,
                        'path': addon_info['path'],
                        'structure': addon_info['structure']
                    }
            elif not addon_info['found']:
                return {'success': False, 'error': 'Could not detect addon structure'}
            
            addon_name = addon_info['name']
            addon_source = addon_info['path']
            
            target_dir = self.addons_dir / addon_name
            
            if target_dir.exists():
                existing_pkg = self.package_tracker.get_package(addon_name, 'addon')
                if existing_pkg and existing_pkg.get('source') == self.official_repo and url == self.official_repo:
                    self._remove_directory_safe(target_dir)
                else:
                    return {'success': False, 'error': f'Addon "{addon_name}" already exists'}
            
            # Check for file conflicts (will skip conflicts from same repository unless force is False)
            if not force:
                conflicts = self._check_file_conflicts(repo_root, addon_name, url)
                if conflicts['libs'] or conflicts['docs'] or conflicts['resources']:
                    return {'success': False, 'error': 'File conflicts detected', 'conflicts': conflicts, 'requires_confirmation': True}
            
            if addon_info['structure'] == 'root':
                shutil.copytree(addon_source, target_dir)
            else:
                shutil.copytree(addon_source, target_dir)
            
            # Track package
            install_method = 'git' if commit_hash else 'release'
            package_info = {
                'source': url,
                'install_method': install_method,
                'installed_date': datetime.now().isoformat(),
                'path': str(target_dir.relative_to(self.ashita_root))
            }

            if release_asset_name:
                package_info['release_asset_name'] = release_asset_name
            
            if commit_hash:
                # For monorepos, get folder-specific commit
                if url == self.official_repo:
                    folder_path = f'addons/{addon_name}'
                    folder_commit_result = self._run_command(
                        ['git', 'log', '-1', '--format=%H', '--', folder_path],
                        cwd=source_path,
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if folder_commit_result.returncode == 0 and folder_commit_result.stdout.strip():
                        package_info['commit'] = folder_commit_result.stdout.strip()
                    else:
                        package_info['commit'] = commit_hash
                else:
                    package_info['commit'] = commit_hash
                package_info['branch'] = branch_name
            
            if release_tag:
                package_info['release_tag'] = release_tag
            
            self.package_tracker.add_package(addon_name, 'addon', package_info)
            
            # Copy extra folders (libs, docs, resources)
            self._copy_extra_folders(repo_root, addon_name, pkg_type='addon')
            self.package_tracker.save_packages()
            
            return {'success': True, 'message': f'Addon "{addon_name}" installed successfully'}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _install_plugin(self, source_path, url, commit_hash=None, branch_name=None, release_tag=None, target_name=None, force=False, release_asset_name=None):
        """Install plugin from extracted source directory.
        
        Args:
            source_path: str/Path - Extracted plugin source directory
            url: str - Source repository URL
            commit_hash: Optional str - Git commit hash
            branch_name: Optional str - Git branch name
            release_tag: Optional str - Release tag
            target_name: Optional str - Specific plugin name to install (for monorepos)
            force: bool - Skip conflict checking
            release_asset_name: Optional str - Release asset filename
        
        Returns:
            dict - Installation result with keys:
            - success: bool - Whether installation succeeded
            - error: str - Error message if failed
        """
        try:
            repo_root = source_path
            plugin_info = self.detector.detect_plugin_structure(source_path, target_name)
            
            if not plugin_info['found']:
                return {'success': False, 'error': 'Could not detect plugin structure (.dll file)'}
            
            plugin_name = plugin_info['name']
            
            target_dll = self.plugins_dir / f"{plugin_name}.dll"
            
            if target_dll.exists():
                existing_pkg = self.package_tracker.get_package(plugin_name, 'plugin')
                if existing_pkg and existing_pkg.get('source') == self.official_repo and url == self.official_repo:
                    target_dll.unlink()
                else:
                    return {'success': False, 'error': f'Plugin "{plugin_name}.dll" already exists'}
            
            shutil.copy2(plugin_info['dll_path'], target_dll)
            
            # Track package
            install_method = 'git' if commit_hash else 'release'
            package_info = {
                'source': url,
                'install_method': install_method,
                'installed_date': datetime.now().isoformat(),
                'path': str(target_dll.relative_to(self.ashita_root))
            }

            if release_asset_name:
                package_info['release_asset_name'] = release_asset_name
            
            if commit_hash:
                # For monorepos, get folder-specific commit
                if url == self.official_repo:
                    # For plugins, the path in git is to the dll file
                    folder_path = f'plugins/{plugin_name}.dll'
                    folder_commit_result = self._run_command(
                        ['git', 'log', '-1', '--format=%H', '--', folder_path],
                        cwd=source_path,
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if folder_commit_result.returncode == 0 and folder_commit_result.stdout.strip():
                        package_info['commit'] = folder_commit_result.stdout.strip()
                    else:
                        package_info['commit'] = commit_hash
                else:
                    package_info['commit'] = commit_hash
                package_info['branch'] = branch_name
            
            if release_tag:
                package_info['release_tag'] = release_tag
            
            self.package_tracker.add_package(plugin_name, 'plugin', package_info)
            
            # Copy extra folders (docs, resources) - this will update package_info
            self._copy_extra_folders(repo_root, plugin_name, pkg_type='plugin')
            self.package_tracker.save_packages()
            
            return {'success': True, 'message': f'Plugin "{plugin_name}" installed successfully'}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _clear_manual_artifacts(self, package_name):
        """Clear documentation and resource folders for a package.
        
        Args:
            package_name: str - Name of package to clear
        
        Returns:
            None
        """
        docs_path = self.docs_dir / package_name
        if docs_path.exists():
            self._remove_directory_safe(docs_path)
        resources_path = self.ashita_root / 'resources' / package_name
        if resources_path.exists():
            self._remove_directory_safe(resources_path)

    def _copy_manual_docs(self, docs_source, package_name):
        """Copy documentation folder to package docs directory.
        
        Args:
            docs_source: str/Path - Source documentation folder
            package_name: str - Target package name
        
        Returns:
            None - Raises ValueError if docs_source is invalid
        """
        docs_source = Path(docs_source)
        if not docs_source.exists() or not docs_source.is_dir():
            raise ValueError('Documentation path is not a folder')
        package_lower = package_name.lower()

        # Decide which folder to copy so we preserve original structure but avoid double-nesting
        # Prefer an inner folder that matches the package name if present, else use the selected folder
        subdirs = [d for d in docs_source.iterdir() if d.is_dir() and not d.name.startswith('.')]
        source_to_copy = None

        # If docs_source contains a single subdirectory that matches the package name, use it
        if len(subdirs) == 1 and subdirs[0].name.lower() == package_lower:
            source_to_copy = subdirs[0]
        # If docs_source contains a subfolder named after the package, prefer that
        elif (docs_source / package_name).exists() and (docs_source / package_name).is_dir():
            source_to_copy = docs_source / package_name
        else:
            for d in subdirs:
                if d.name.lower() == package_lower:
                    source_to_copy = d
                    break

        # Fallback to the selected folder itself
        if source_to_copy is None:
            source_to_copy = docs_source

        target_docs = self.docs_dir / package_name
        if target_docs.exists():
            self._remove_directory_safe(target_docs)

        # If the user selected a folder whose basename equals the package name, copy the *contents*
        # so we don't end up with docs/MyAddon/MyAddon/...
        if source_to_copy == docs_source and docs_source.name.lower() == package_lower:
            target_docs.mkdir(parents=True, exist_ok=True)
            for item in docs_source.iterdir():
                if item.is_dir():
                    shutil.copytree(item, target_docs / item.name)
                else:
                    shutil.copy2(item, target_docs / item.name)
        else:
            shutil.copytree(source_to_copy, target_docs)
        
        doc_files = []
        for item in target_docs.rglob('*'):
            if item.is_file():
                try:
                    rel_path = item.relative_to(self.ashita_root)
                    doc_files.append(str(rel_path))
                except ValueError:
                    doc_files.append(str(item))
        return doc_files

    def _copy_manual_resources(self, resources_source, package_name):
        """Copy resources folder to ashita resources directory.
        
        Args:
            resources_source: str/Path - Source resources folder
            package_name: str - Target package name
        
        Returns:
            None - Raises ValueError if resources_source is invalid
        """
        resources_source = Path(resources_source)
        if not resources_source.exists() or not resources_source.is_dir():
            raise ValueError('Resources path is not a folder')
        package_lower = package_name.lower()

        subdirs = [d for d in resources_source.iterdir() if d.is_dir() and not d.name.startswith('.')]
        source_to_copy = None

        if len(subdirs) == 1 and subdirs[0].name.lower() == package_lower:
            source_to_copy = subdirs[0]
        elif (resources_source / package_name).exists() and (resources_source / package_name).is_dir():
            source_to_copy = resources_source / package_name
        else:
            for d in subdirs:
                if d.name.lower() == package_lower:
                    source_to_copy = d
                    break

        if source_to_copy is None:
            source_to_copy = resources_source

        resources_root = self.ashita_root / 'resources'
        resources_root.mkdir(parents=True, exist_ok=True)
        target_resources = resources_root / package_name
        if target_resources.exists():
            self._remove_directory_safe(target_resources)

        if source_to_copy == resources_source and resources_source.name.lower() == package_lower:
            target_resources.mkdir(parents=True, exist_ok=True)
            for item in resources_source.iterdir():
                if item.is_dir():
                    shutil.copytree(item, target_resources / item.name)
                else:
                    shutil.copy2(item, target_resources / item.name)
        else:
            shutil.copytree(source_to_copy, target_resources)
        
        resource_files = []
        for item in target_resources.rglob('*'):
            if item.is_file():
                try:
                    rel_path = item.relative_to(self.ashita_root)
                    resource_files.append(str(rel_path))
                except ValueError:
                    resource_files.append(str(item))
        return resource_files

    def manual_install_addon(self, addon_path, docs_path=None, resources_path=None, expected_name=None, selected_entrypoint=None):
        """Install an addon from a manually selected folder.
        
        Args:
            addon_path: str/Path - Path to addon folder
            docs_path: Optional str/Path - Path to documentation folder
            resources_path: Optional str/Path - Path to resources folder
            expected_name: Optional str - Expected addon name for validation
            selected_entrypoint: Optional str - Specific lua file name as entrypoint
        
        Returns:
            dict - Installation result with keys:
            - success: bool - whether installation succeeded
            - message: str - success/error message
            - requires_entrypoint_selection: bool - lua file selection needed
            - lua_files: list - list of available lua files (if ambiguous)
        """
        try:
            source_path = Path(addon_path)
            if not source_path.exists():
                return {'success': False, 'error': 'Selected addon folder does not exist'}

            addon_info = self.detector.detect_addon_structure(source_path)
            
            # Handle ambiguous addon name detection
            if not addon_info['found'] and addon_info.get('ambiguous'):
                if not selected_entrypoint:
                    # Return lua files for user to select
                    return {
                        'success': False,
                        'requires_entrypoint_selection': True,
                        'lua_files': addon_info['lua_files'],
                        'addon_path': str(source_path)
                    }
                else:
                    # User has selected an entrypoint
                    addon_info = {
                        'found': True,
                        'name': selected_entrypoint,
                        'path': addon_info['path'],
                        'structure': addon_info['structure']
                    }
            elif not addon_info['found']:
                return {'success': False, 'error': 'Could not detect addon entry point in selected folder'}

            addon_name = addon_info['name']
            if expected_name and addon_name.lower() != expected_name.lower():
                return {'success': False, 'error': f'Selected addon "{addon_name}" does not match "{expected_name}"'}

            addon_source = addon_info['path']
            target_dir = self.addons_dir / addon_name
            if target_dir.exists():
                return {'success': False, 'error': f'Addon "{addon_name}" already exists'}

            # Check if source and target are the same or related (would cause infinite loop)
            try:
                addon_source_resolved = addon_source.resolve()
                target_dir_resolved = target_dir.resolve()
                
                # Check if they're the same directory
                if addon_source_resolved == target_dir_resolved:
                    return {'success': False, 'error': f'Addon "{addon_name}" is already installed in the correct location'}
                
                # Check if target is inside source or source is inside target (would cause recursive copy)
                if target_dir_resolved in addon_source_resolved.parents or addon_source_resolved in target_dir_resolved.parents:
                    return {'success': False, 'error': 'Cannot copy addon: source and destination are nested within each other'}
            except Exception:
                pass  # If path resolution fails, proceed with copy attempt

            shutil.copytree(addon_source, target_dir)

            package_info = {
                'source': 'unknown',
                'install_method': 'manual',
                'installed_date': datetime.now().isoformat(),
                'path': str(target_dir.relative_to(self.ashita_root))
            }

            self._clear_manual_artifacts(addon_name)

            if docs_path:
                try:
                    doc_files = self._copy_manual_docs(docs_path, addon_name)
                    if doc_files:
                        package_info['doc_files'] = doc_files
                except Exception as e:
                    if target_dir.exists():
                        self._remove_directory_safe(target_dir)
                    self._clear_manual_artifacts(addon_name)
                    return {'success': False, 'error': f'Failed to copy documentation: {e}'}

            if resources_path:
                try:
                    resource_files = self._copy_manual_resources(resources_path, addon_name)
                    if resource_files:
                        package_info['resource_files'] = resource_files
                except Exception as e:
                    if target_dir.exists():
                        self._remove_directory_safe(target_dir)
                    self._clear_manual_artifacts(addon_name)
                    return {'success': False, 'error': f'Failed to copy resources: {e}'}

            self.package_tracker.add_package(addon_name, 'addon', package_info)
            self.package_tracker.save_packages()

            return {'success': True, 'message': f'Addon "{addon_name}" installed manually', 'package_name': addon_name}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def manual_install_plugin(self, dll_path, docs_path=None, resources_path=None, expected_name=None):
        """Install a plugin from a manually selected DLL file.
        
        Args:
            dll_path: str/Path - Path to plugin DLL file
            docs_path: Optional str/Path - Path to documentation folder
            resources_path: Optional str/Path - Path to resources folder
            expected_name: Optional str - Expected plugin name for validation
        
        Returns:
            dict - Installation result with keys:
            - success: bool - whether installation succeeded
            - message: str - success/error message
        """
        try:
            plugin_file = Path(dll_path)
            if not plugin_file.exists() or plugin_file.suffix.lower() != '.dll':
                return {'success': False, 'error': 'Please select a valid .dll file'}

            plugin_name = plugin_file.stem
            if expected_name and plugin_name.lower() != expected_name.lower():
                return {'success': False, 'error': f'Selected plugin "{plugin_name}" does not match "{expected_name}"'}

            target_dll = self.plugins_dir / f"{plugin_name}.dll"
            if target_dll.exists():
                return {'success': False, 'error': f'Plugin "{plugin_name}.dll" already exists'}

            shutil.copy2(plugin_file, target_dll)

            package_info = {
                'source': 'unknown',
                'install_method': 'manual',
                'installed_date': datetime.now().isoformat(),
                'path': str(target_dll.relative_to(self.ashita_root))
            }

            self._clear_manual_artifacts(plugin_name)

            if docs_path:
                try:
                    doc_files = self._copy_manual_docs(docs_path, plugin_name)
                    if doc_files:
                        package_info['doc_files'] = doc_files
                except Exception as e:
                    if target_dll.exists():
                        target_dll.unlink()
                    self._clear_manual_artifacts(plugin_name)
                    return {'success': False, 'error': f'Failed to copy documentation: {e}'}

            if resources_path:
                try:
                    resource_files = self._copy_manual_resources(resources_path, plugin_name)
                    if resource_files:
                        package_info['resource_files'] = resource_files
                except Exception as e:
                    if target_dll.exists():
                        target_dll.unlink()
                    self._clear_manual_artifacts(plugin_name)
                    return {'success': False, 'error': f'Failed to copy resources: {e}'}

            self.package_tracker.add_package(plugin_name, 'plugin', package_info)
            self.package_tracker.save_packages()

            return {'success': True, 'message': f'Plugin "{plugin_name}" installed manually', 'package_name': plugin_name}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _check_file_conflicts(self, source_path, package_name, source_url=None):
        """Check for file conflicts in libs, docs, and resources folders.
        
        Args:
            source_path: str/Path - Source package directory
            package_name: str - Target package name
            source_url: Optional str - Source repository URL for same-repo skipping
        
        Returns:
            dict - Conflict info with keys:
            - libs: list - Conflicting library files
            - docs: list - Conflicting documentation files
            - resources: list - Conflicting resource files
        """
        source_path = Path(source_path)
        conflicts = {'libs': [], 'docs': False, 'resources': False}
        
        # Check libs conflicts
        libs_source = source_path / 'addons' / 'libs'
        if libs_source.exists() and libs_source.is_dir():
            libs_target = self.addons_dir / 'libs'
            if libs_target.exists():
                for item in libs_source.rglob('*'):
                    if item.is_file():
                        rel_path = item.relative_to(libs_source)
                        target_file = libs_target / rel_path
                        if target_file.exists():
                            # Check if owned by another package
                            owner = None
                            owner_source = None
                            all_packages = self.package_tracker.get_all_packages()
                            for pkg_name, pkg_info in all_packages.get('addons', {}).items():
                                if pkg_name != package_name and 'lib_files' in pkg_info:
                                    if str(rel_path) in pkg_info['lib_files']:
                                        owner = pkg_name
                                        owner_source = pkg_info.get('source')
                                        break
                            
                            # Only report conflict if from a different repository
                            if owner and owner_source != source_url:
                                conflicts['libs'].append({'file': str(rel_path), 'owner': owner, 'owner_source': owner_source})
        
        # Check docs conflicts
        for docs_loc in [source_path / 'docs', source_path / 'Docs']:
            if docs_loc.exists():
                target_docs = self.docs_dir / package_name
                if target_docs.exists():
                    conflicts['docs'] = True
                break
        
        # Check resources conflicts
        for res_loc in [source_path / 'resources', source_path / 'Resources']:
            if res_loc.exists():
                resources_dir = self.ashita_root / 'resources'
                target_resources = resources_dir / package_name
                if target_resources.exists():
                    conflicts['resources'] = True
                break
        
        return conflicts
    
    def _copy_extra_folders(self, source_path, package_name, pkg_type='addon', is_monorepo=False):
        """Copy extra documentation and resource folders.
        
        Args:
            source_path: str/Path - Source directory
            package_name: str - Target package name
            pkg_type: str - 'addon' or 'plugin'
            is_monorepo: bool - Whether source is from monorepo
        
        Returns:
            None
        """
        source_path = Path(source_path)
        errors = []
        
        subdirs = [d for d in source_path.iterdir() if d.is_dir() and not d.name.startswith('.')]
        if len(subdirs) == 1:
            actual_source = subdirs[0]
        else:
            actual_source = source_path
        
        has_addons_folder = (actual_source / 'addons').exists()
        has_plugins_folder = (actual_source / 'plugins').exists()
        is_multi_folder_repo = has_addons_folder or has_plugins_folder
        
        if is_multi_folder_repo:
            try:
                libs_source = actual_source / 'addons' / 'libs'
                if libs_source.exists() and libs_source.is_dir():
                    libs_target = self.addons_dir / 'libs'
                    libs_target.mkdir(exist_ok=True, parents=True)
                    
                    lib_files = []
                    
                    for item in libs_source.rglob('*'):
                        if item.is_file():
                            rel_path = item.relative_to(libs_source)
                            target_file = libs_target / rel_path
                            
                            target_file.parent.mkdir(parents=True, exist_ok=True)
                            
                            shutil.copy2(item, target_file)
                            
                            try:
                                tracked_path = target_file.relative_to(self.ashita_root)
                            except ValueError:
                                tracked_path = rel_path
                            lib_files.append(str(tracked_path))
                    
                    if lib_files:
                        pkg = self.package_tracker.get_package(package_name, 'addon')
                        if pkg:
                            pkg['lib_files'] = lib_files
            except Exception as e:
                errors.append(f"Error copying libs: {e}")
        
        try:
            doc_files = []
            found_docs = False
            
            if is_multi_folder_repo:
                package_docs_locations = [
                    actual_source / 'docs',
                    actual_source / 'Docs',
                ]
            else:
                package_docs_locations = [
                    actual_source / 'docs',
                    actual_source / 'Docs',
                ]
            
            for docs_location in package_docs_locations:
                if not docs_location.exists() or not docs_location.is_dir():
                    continue
                    
                target_docs = self.docs_dir / package_name
                source_to_copy = None
                
                package_variations = [
                    docs_location / package_name,
                    docs_location / package_name.lower(),
                    docs_location / package_name.upper(),
                    docs_location / package_name.title()
                ]
                
                for variation in package_variations:
                    if variation.exists() and variation.is_dir():
                        source_to_copy = variation
                        break
                
                if not source_to_copy:
                    if is_multi_folder_repo:
                        source_to_copy = docs_location
                    else:
                        source_to_copy = docs_location
                
                if source_to_copy and source_to_copy.exists():
                    if target_docs.exists():
                        self._remove_directory_safe(target_docs)
                    shutil.copytree(source_to_copy, target_docs)
                    
                    for item in source_to_copy.rglob('*'):
                        if item.is_file():
                            rel_path = item.relative_to(source_to_copy)
                            target_file = target_docs / rel_path
                            try:
                                tracked_path = target_file.relative_to(self.ashita_root)
                            except ValueError:
                                tracked_path = rel_path
                            doc_files.append(str(tracked_path))
                    found_docs = True
                    break
            
            if doc_files:
                pkg = self.package_tracker.get_package(package_name, pkg_type)
                if pkg:
                    pkg['doc_files'] = doc_files
        except Exception as e:
            errors.append(f"Error copying docs: {e}")
        
        try:
            resource_files = []
            found_resources = False
            
            if is_multi_folder_repo:
                package_resources_locations = [
                    actual_source / 'resources',
                    actual_source / 'Resources',
                ]
            else:
                package_resources_locations = [
                    actual_source / 'resources',
                    actual_source / 'Resources',
                ]
            
            for res_location in package_resources_locations:
                if not res_location.exists() or not res_location.is_dir():
                    continue
                
                resources_dir = self.ashita_root / 'resources'
                resources_dir.mkdir(exist_ok=True, parents=True)
                
                package_variations = [
                    res_location / package_name,
                    res_location / package_name.lower(),
                    res_location / package_name.upper(),
                    res_location / package_name.title()
                ]
                
                has_package_subfolder = any(v.exists() and v.is_dir() for v in package_variations)
                
                if has_package_subfolder:
                    for variation in package_variations:
                        if variation.exists() and variation.is_dir():
                            target_resources = resources_dir / package_name
                            if target_resources.exists():
                                self._remove_directory_safe(target_resources)
                            shutil.copytree(variation, target_resources)
                            
                            for item in variation.rglob('*'):
                                if item.is_file():
                                    rel_path = item.relative_to(variation)
                                    target_file = target_resources / rel_path
                                    try:
                                        tracked_path = target_file.relative_to(self.ashita_root)
                                    except ValueError:
                                        tracked_path = rel_path
                                    resource_files.append(str(tracked_path))
                            found_resources = True
                            break
                else:
                    for subdir in res_location.iterdir():
                        if subdir.is_dir():
                            target_subdir = resources_dir / subdir.name
                            if target_subdir.exists():
                                for item in subdir.rglob('*'):
                                    if item.is_file():
                                        rel_path = item.relative_to(res_location)
                                        target_file = resources_dir / rel_path
                                        target_file.parent.mkdir(parents=True, exist_ok=True)
                                        shutil.copy2(item, target_file)
                                        try:
                                            tracked_path = target_file.relative_to(self.ashita_root)
                                        except ValueError:
                                            tracked_path = rel_path
                                        resource_files.append(str(tracked_path))
                            else:
                                shutil.copytree(subdir, target_subdir)
                                for item in subdir.rglob('*'):
                                    if item.is_file():
                                        rel_path = item.relative_to(res_location)
                                        target_file = resources_dir / rel_path
                                        try:
                                            tracked_path = target_file.relative_to(self.ashita_root)
                                        except ValueError:
                                            tracked_path = rel_path
                                        resource_files.append(str(tracked_path))
                    found_resources = True
                
                if found_resources:
                    break
            
            if resource_files:
                pkg = self.package_tracker.get_package(package_name, pkg_type)
                if pkg:
                    pkg['resource_files'] = resource_files
        except Exception as e:
            errors.append(f"Error copying resources: {e}")
        
        return errors
    
    def _get_latest_release_url(self, repo_url, preferred_asset_name=None):
        """Fetch latest release asset download URL from repository.
        
        Args:
            repo_url: str - Repository URL
            preferred_asset_name: Optional str - Preferred asset filename to match
        
        Returns:
            dict - Release info with keys:
            - download_url: str - Asset download URL
            - tag: str - Release tag
            - rate_limited: bool - True if GitHub API rate limited
            Or None if no release found
        """
        try:
            parsed = urlparse(repo_url)
            path_parts = parsed.path.strip('/').split('/')
            
            if len(path_parts) < 2:
                return None
            
            owner, repo = path_parts[0], path_parts[1]
            
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            
            headers = {}
            token = self.package_tracker.get_setting('github_token')
            if not token:
                token = os.environ.get('GITHUB_TOKEN')
            if token:
                headers['Authorization'] = f'token {token}'
            
            response = requests.get(api_url, headers=headers or None, timeout=10)
            
            if response.status_code == 403:
                error_data = response.json()
                if 'rate limit' in error_data.get('message', '').lower():
                    return {'rate_limited': True, 'message': 'GitHub API rate limit exceeded'}
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            
            if 'assets' in data and len(data['assets']) > 0:
                assets = data['assets']
                zip_assets = [a for a in assets if a['name'].lower().endswith('.zip')]

                if preferred_asset_name and zip_assets:
                    normalized = preferred_asset_name.lower()
                    for asset in zip_assets:
                        if asset['name'].lower() == normalized:
                            return (asset['browser_download_url'], asset['name'])

                    tokens = self._tokenize_asset_name(preferred_asset_name)
                    if tokens:
                        best_asset = None
                        best_score = 0
                        for asset in zip_assets:
                            score = self._score_asset_match(asset['name'], tokens)
                            if score > best_score:
                                best_asset = asset
                                best_score = score
                        if best_asset and best_score > 0:
                            return (best_asset['browser_download_url'], best_asset['name'])

                    for asset in zip_assets:
                        if normalized in asset['name'].lower():
                            return (asset['browser_download_url'], asset['name'])

                if len(zip_assets) > 1:
                    return {'multiple_assets': True, 'assets': [{'name': a['name'], 'url': a['browser_download_url']} for a in zip_assets]}
                elif len(zip_assets) == 1:
                    asset = zip_assets[0]
                    return (asset['browser_download_url'], asset['name'])
                else:
                    asset = assets[0]
                    return (asset['browser_download_url'], asset['name'])
            
            if 'zipball_url' in data:
                return (data['zipball_url'], None)
            
            return None
            
        except Exception:
            return None
    
    def _get_release_tag(self, repo_url):
        """Get the latest release tag for a repository.
        
        Args:
            repo_url: str - Repository URL
        
        Returns:
            str - Release tag name or None if no releases found
        """
        try:
            parsed = urlparse(repo_url)
            path_parts = parsed.path.strip('/').split('/')
            
            if len(path_parts) < 2:
                return 'unknown'
            
            owner, repo = path_parts[0], path_parts[1]
            
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            
            headers = {}
            token = self.package_tracker.get_setting('github_token')
            if not token:
                token = os.environ.get('GITHUB_TOKEN')
            if token:
                headers['Authorization'] = f'token {token}'
            
            response = requests.get(api_url, headers=headers or None, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('tag_name', 'unknown')
            
            return 'unknown'
            
        except Exception:
            return 'unknown'

    def _infer_asset_name(self, download_url):
        """Extract asset filename from download URL.
        
        Args:
            download_url: str - Full download URL
        
        Returns:
            str - Asset filename or empty string on error
        """
        try:
            parsed = urlparse(download_url)
            if not parsed.path:
                return None
            return Path(parsed.path).name
        except Exception:
            return None

    def _tokenize_asset_name(self, name):
        """Split asset filename into tokenized parts.
        
        Args:
            name: str - Asset filename
        
        Returns:
            list - List of lowercase tokens/parts
        """
        if not name:
            return []
        tokens = re.split(r'[^a-z0-9]+', name.lower())
        return [t for t in tokens if t and len(t) > 2 and not t.isdigit()]

    def _score_asset_match(self, candidate_name, tokens):
        """Calculate matching score between asset name and tokens.
        
        Args:
            candidate_name: str - Asset filename to test
            tokens: list - Token list to match against
        
        Returns:
            int - Matching score (higher is better)
        """
        if not candidate_name or not tokens:
            return 0
        candidate_lower = candidate_name.lower()
        score = 0
        for token in tokens:
            if token and token in candidate_lower:
                score += 1
        return score

    def _fetch_official_repo_catalog(self, branch=None):
        """Fetch official addon and plugin lists from Ashita repository.
        
        Args:
            branch: Optional str - Git branch to fetch from (defaults to configured branch)
        
        Returns:
            dict - Catalog with keys:
            - success: bool - Whether fetch succeeded
            - addons: set - Set of official addon names
            - plugins: set - Set of official plugin names
            - error: str - Error message if failed
        """
        result = {
            'addons': set(),
            'plugins': set(),
            'success': False,
            'error': None,
            'rate_limited': False
        }
        try:
            base_url = "https://api.github.com/repos/AshitaXI/Ashita-v4beta/contents"
            headers = {}
            token = self.package_tracker.get_setting('github_token')
            if not token:
                token = os.environ.get('GITHUB_TOKEN')
            if token:
                headers['Authorization'] = f'token {token}'

            ref = f"?ref={branch}" if branch else ''
            addons_resp = requests.get(f"{base_url}/addons{ref}", headers=headers or None, timeout=10)
            plugins_resp = requests.get(f"{base_url}/plugins{ref}", headers=headers or None, timeout=10)

            is_rate_limited = False
            if addons_resp.status_code == 403 or plugins_resp.status_code == 403:
                try:
                    resp_with_error = addons_resp if addons_resp.status_code == 403 else plugins_resp
                    error_data = resp_with_error.json()
                    if 'rate limit' in error_data.get('message', '').lower():
                        is_rate_limited = True
                except Exception:
                    is_rate_limited = True
            
            if is_rate_limited:
                result['rate_limited'] = True
                result['error'] = 'GitHub API rate limit exceeded. Please wait or configure a GitHub token in Settings.'
                return result

            if addons_resp.status_code == 200:
                for entry in addons_resp.json():
                    if entry.get('type') == 'dir':
                        name = entry.get('name')
                        if name and not name.startswith('.') and name.lower() != 'libs':
                            result['addons'].add(name)

            if plugins_resp.status_code == 200:
                for entry in plugins_resp.json():
                    if entry.get('type') == 'file':
                        name = entry.get('name')
                        if name and name.lower().endswith('.dll'):
                            result['plugins'].add(Path(name).stem)

            if addons_resp.status_code == 200 and plugins_resp.status_code == 200:
                result['success'] = True
            else:
                error_parts = []
                if addons_resp.status_code != 200:
                    error_parts.append(f"addons:{addons_resp.status_code}")
                if plugins_resp.status_code != 200:
                    error_parts.append(f"plugins:{plugins_resp.status_code}")
                result['error'] = ' / '.join(error_parts) or 'Unknown error'
        except Exception as e:
            result['error'] = str(e)

        return result
    
    def _compare_with_remote_files(self, package_name, pkg_type, source_url, branch):
        """Compare local package files with remote repository.
        
        Args:
            package_name: str - Package name
            pkg_type: str - 'addon' or 'plugin'
            source_url: str - Remote repository URL
            branch: str - Git branch to compare against
        
        Returns:
            dict - Comparison result with keys:
            - changed: bool - Whether files differ from remote
            - error: str - Error message if comparison failed
        """
        try:
            # Only compare with official repo
            if source_url != self.official_repo:
                return {'needs_update': True}
            
            # Create temporary directory for cloning
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                if pkg_type == 'addon':
                    # Clone only the specific addon folder (sparse checkout)
                    repo_path = temp_path / 'repo'
                    
                    # Use sparse checkout for efficiency
                    init_result = self._run_command(
                        ['git', 'init'],
                        cwd=temp_path,
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if init_result.returncode != 0:
                        return {'needs_update': True}
                    
                    # Add remote
                    self._run_command(
                        ['git', 'remote', 'add', 'origin', source_url],
                        cwd=temp_path,
                        capture_output=True,
                        timeout=10
                    )
                    
                    # Enable sparse checkout
                    self._run_command(
                        ['git', 'config', 'core.sparseCheckout', 'true'],
                        cwd=temp_path,
                        capture_output=True,
                        timeout=10
                    )
                    
                    # Specify the path to checkout
                    sparse_file = temp_path / '.git' / 'info' / 'sparse-checkout'
                    sparse_file.parent.mkdir(parents=True, exist_ok=True)
                    sparse_file.write_text(f'addons/{package_name}/*\n')
                    
                    # Pull the specific branch
                    pull_result = self._run_command(
                        ['git', 'pull', 'origin', branch, '--depth=1'],
                        cwd=temp_path,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    if pull_result.returncode != 0:
                        return {'needs_update': True}
                    
                    remote_addon_dir = temp_path / 'addons' / package_name
                    local_addon_dir = self.addons_dir / package_name
                    
                    if not remote_addon_dir.exists() or not local_addon_dir.exists():
                        return {'needs_update': True}
                    
                    # Compare files
                    return self._compare_directories(local_addon_dir, remote_addon_dir)
                    
                else:  # plugin
                    # Clone only the plugins folder
                    repo_path = temp_path / 'repo'
                    
                    init_result = self._run_command(
                        ['git', 'init'],
                        cwd=temp_path,
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if init_result.returncode != 0:
                        return {'needs_update': True}
                    
                    self._run_command(
                        ['git', 'remote', 'add', 'origin', source_url],
                        cwd=temp_path,
                        capture_output=True,
                        timeout=10
                    )
                    
                    self._run_command(
                        ['git', 'config', 'core.sparseCheckout', 'true'],
                        cwd=temp_path,
                        capture_output=True,
                        timeout=10
                    )
                    
                    sparse_file = temp_path / '.git' / 'info' / 'sparse-checkout'
                    sparse_file.parent.mkdir(parents=True, exist_ok=True)
                    sparse_file.write_text(f'plugins/{package_name}.dll\n')
                    
                    pull_result = self._run_command(
                        ['git', 'pull', 'origin', branch, '--depth=1'],
                        cwd=temp_path,
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    if pull_result.returncode != 0:
                        return {'needs_update': True}
                    
                    remote_dll = temp_path / 'plugins' / f'{package_name}.dll'
                    local_dll = self.plugins_dir / f'{package_name}.dll'
                    
                    if not remote_dll.exists() or not local_dll.exists():
                        return {'needs_update': True}
                    
                    # Compare file sizes and content
                    if remote_dll.stat().st_size != local_dll.stat().st_size:
                        return {'needs_update': True}
                    
                    # Binary comparison
                    remote_hash = hashlib.md5(remote_dll.read_bytes()).hexdigest()
                    local_hash = hashlib.md5(local_dll.read_bytes()).hexdigest()
                    
                    return {'needs_update': remote_hash != local_hash}
                    
        except Exception as e:
            # On error, assume update is needed
            return {'needs_update': True, 'error': str(e)}
    
    def _compare_directories(self, local_dir, remote_dir):
        """Recursively compare two directories using MD5 hashing.
        
        Args:
            local_dir: str/Path - Local directory path
            remote_dir: str/Path - Remote directory path
        
        Returns:
            bool - True if directories have same content, False if different
        """
        try:
            # Get all files in both directories
            local_files = set()
            remote_files = set()
            
            for f in local_dir.rglob('*'):
                if f.is_file():
                    local_files.add(f.relative_to(local_dir))
            
            for f in remote_dir.rglob('*'):
                if f.is_file():
                    remote_files.add(f.relative_to(remote_dir))
            
            # Check if file lists differ
            if local_files != remote_files:
                return {'needs_update': True}
            
            # Compare file contents
            for rel_path in local_files:
                local_file = local_dir / rel_path
                remote_file = remote_dir / rel_path
                
                # Quick size check
                if local_file.stat().st_size != remote_file.stat().st_size:
                    return {'needs_update': True}
                
                # Content comparison using hash
                local_hash = hashlib.md5(local_file.read_bytes()).hexdigest()
                remote_hash = hashlib.md5(remote_file.read_bytes()).hexdigest()
                
                if local_hash != remote_hash:
                    return {'needs_update': True}
            
            # All files are identical
            return {'needs_update': False}
            
        except Exception as e:
            # On error, assume update is needed
            return {'needs_update': True, 'error': str(e)}
    
    def update_package(self, package_name, pkg_type, release_asset_url=None, release_asset_name=None, manual_payload=None):
        """Update an existing package.
        
        Args:
            package_name: str - Name of package to update
            pkg_type: str - 'addon' or 'plugin'
            release_asset_url: Optional str - Direct release asset download URL
            release_asset_name: Optional str - Preferred release asset name
            manual_payload: Optional dict - Payload for manual update (docs_path, resources_path, etc)
        
        Returns:
            dict - Update result with keys:
            - success: bool - whether update succeeded or already up-to-date
            - message: str - result message
            - already_updated: bool - True if no update needed
            - requires_manual_update: bool - manual update required
            - error: str - error message if failed
        """
        try:
            package_info = self.package_tracker.get_package(package_name, pkg_type)
            
            if not package_info:
                return {'success': False, 'error': f'Package "{package_name}" not found'}
            
            install_method = package_info.get('install_method')
            source_url = package_info.get('source')
            current_commit = package_info.get('commit')
            branch = package_info.get('branch', self.official_repo_branch)
            old_package_info = package_info.copy()
            is_pre_installed = install_method == 'pre-installed' or source_url == 'pre-installed'

            requires_manual = install_method == 'manual' or (
                install_method == 'release' and (not source_url or source_url == 'unknown')
            )

            if manual_payload:
                return self._apply_manual_update(package_name, pkg_type, manual_payload, old_package_info)

            if requires_manual:
                return {
                    'success': False,
                    'requires_manual_update': True,
                    'package_name': package_name,
                    'pkg_type': pkg_type,
                    'reason': 'manual' if install_method == 'manual' else 'unknown-source'
                }
            
            # Handle pre-installed packages
            if is_pre_installed:
                source_url = self.official_repo
                # Don't change install_method yet - we'll preserve it if no update needed
            
            if not source_url:
                return {'success': False, 'error': 'Package source URL not found'}
            
            # For pre-installed packages, compare files with official repo
            if is_pre_installed:
                comparison_result = self._compare_with_remote_files(package_name, pkg_type, source_url, branch)
                if not comparison_result.get('needs_update', True):
                    # Files are identical, no update needed
                    # Update commit hash if we can get it
                    if source_url == self.official_repo:
                        repo_path = f'addons/{package_name}' if pkg_type == 'addon' else f'plugins/{package_name}.dll'
                        remote_result = self._get_remote_commit_hash(source_url, branch, repo_path)
                        if remote_result and isinstance(remote_result, dict) and remote_result.get('sha'):
                            package_info['commit'] = remote_result['sha']
                            self.package_tracker.add_package(package_name, pkg_type, package_info)
                    
                    return {
                        'success': True,
                        'message': f'Package "{package_name}" is already up-to-date',
                        'already_updated': True
                    }
            
            # Check if package is already up-to-date (for git-installed packages)
            if not is_pre_installed and install_method == 'git' and current_commit:
                repo_path = None
                if source_url == self.official_repo:
                    if pkg_type == 'addon':
                        repo_path = f'addons/{package_name}'
                    else:
                        repo_path = f'plugins/{package_name}.dll'
                
                remote_result = self._get_remote_commit_hash(source_url, branch, repo_path)
                
                if remote_result and isinstance(remote_result, dict):
                    if remote_result.get('rate_limited'):
                        return {
                            'success': False, 
                            'error': 'GitHub API rate limit exceeded. Please wait or add a GitHub token in Settings.'
                        }
                    
                    remote_commit = remote_result.get('sha')
                    if remote_commit and remote_commit == current_commit:
                        return {
                            'success': True, 
                            'message': f'Package "{package_name}" is already up-to-date',
                            'already_updated': True
                        }

            if install_method == 'release' and not release_asset_url:
                current_release_tag = package_info.get('release_tag')
                if current_release_tag:
                    latest_release_tag = self._get_release_tag(source_url)
                    if latest_release_tag and latest_release_tag != 'unknown' and latest_release_tag == current_release_tag:
                        return {
                            'success': True,
                            'message': f'Package "{package_name}" is already up-to-date (release {current_release_tag})',
                            'already_updated': True
                        }
            
            # Try to reinstall first (don't delete until we know it works)
            # Temporarily rename old files as backup
            backup_path = None
            if pkg_type == 'addon':
                target_dir = self.addons_dir / package_name
                if target_dir.exists():
                    backup_path = self.addons_dir / f"{package_name}.backup"
                    if backup_path.exists():
                        self._remove_directory_safe(backup_path)
                    shutil.move(str(target_dir), str(backup_path))
            else:
                target_dll = self.plugins_dir / f"{package_name}.dll"
                if target_dll.exists():
                    backup_path = self.plugins_dir / f"{package_name}.dll.backup"
                    if backup_path.exists():
                        backup_path.unlink()
                    shutil.move(str(target_dll), str(backup_path))
            
            try:
                # Determine the install method for the update operation
                update_install_method = 'git' if (is_pre_installed or install_method == 'git') else install_method
                
                if update_install_method == 'git':
                    branch = self.official_repo_branch if source_url == self.official_repo else None
                    result = self.install_from_git(source_url, pkg_type, target_package_name=package_name, branch=branch)
                    if isinstance(result, dict) and result.get('requires_variant_selection'):
                        result = result.copy()
                        result.setdefault('error', 'Variant selection required')
                        result['package_name'] = package_name
                        result['pkg_type'] = pkg_type
                        result['is_update'] = True
                        return result
                else:
                    preferred_asset_name = release_asset_name or package_info.get('release_asset_name')
                    result = self.install_from_release(
                        source_url,
                        pkg_type,
                        asset_download_url=release_asset_url,
                        asset_name=preferred_asset_name
                    )
                    if result.get('requires_variant_selection'):
                        result = result.copy()
                        result.setdefault('error', 'Variant selection required')
                        result['package_name'] = package_name
                        result['pkg_type'] = pkg_type
                        result['is_update'] = True
                        return result
                
                if result['success']:
                    # Success, remove backup
                    if backup_path and backup_path.exists():
                        if backup_path.is_dir():
                            self._remove_directory_safe(backup_path)
                        else:
                            backup_path.unlink()
                    
                    # If originally pre-installed, restore that status
                    if is_pre_installed:
                        updated_package_info = self.package_tracker.get_package(package_name, pkg_type)
                        if updated_package_info:
                            updated_package_info['install_method'] = 'pre-installed'
                            self.package_tracker.add_package(package_name, pkg_type, updated_package_info)
                    
                    return {'success': True, 'message': f'Package "{package_name}" updated successfully'}
                else:
                    # Failed, restore backup
                    if backup_path and backup_path.exists():
                        if pkg_type == 'addon':
                            target_dir = self.addons_dir / package_name
                            if target_dir.exists():
                                self._remove_directory_safe(target_dir)
                            shutil.move(str(backup_path), str(target_dir))
                        else:
                            target_dll = self.plugins_dir / f"{package_name}.dll"
                            if target_dll.exists():
                                target_dll.unlink()
                            shutil.move(str(backup_path), str(target_dll))
                    
                    # Restore tracker entry
                    self.package_tracker.add_package(package_name, pkg_type, old_package_info)
                    error_msg = result.get('error', 'Unknown error during update')
                    return {'success': False, 'error': f'Update failed: {error_msg}'}
            
            except Exception as e:
                # Exception during update, restore backup
                if backup_path and backup_path.exists():
                    if pkg_type == 'addon':
                        target_dir = self.addons_dir / package_name
                        if target_dir.exists():
                            self._remove_directory_safe(target_dir)
                        shutil.move(str(backup_path), str(target_dir))
                    else:
                        target_dll = self.plugins_dir / f"{package_name}.dll"
                        if target_dll.exists():
                            target_dll.unlink()
                        shutil.move(str(backup_path), str(target_dll))
                
                # Restore tracker entry
                self.package_tracker.add_package(package_name, pkg_type, old_package_info)
                raise
                
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _apply_manual_update(self, package_name, pkg_type, manual_payload, old_package_info):
        """Apply manual update with documentation and resources.
        
        Args:
            package_name: str - Package name to update
            pkg_type: str - 'addon' or 'plugin'
            manual_payload: dict - Update payload with paths
            old_package_info: dict - Previous package info for rollback
        
        Returns:
            dict - Update result with success/error status
        """
        backup_path = None
        try:
            if pkg_type == 'addon':
                addon_path = manual_payload.get('addon_path')
                if not addon_path:
                    return {'success': False, 'error': 'Addon folder is required for manual update'}
                target_dir = self.addons_dir / package_name
                if target_dir.exists():
                    backup_path = self.addons_dir / f"{package_name}.manual.backup"
                    if backup_path.exists():
                        self._remove_directory_safe(backup_path)
                    shutil.move(str(target_dir), str(backup_path))
                self._clear_manual_artifacts(package_name)
                result = self.manual_install_addon(
                    addon_path,
                    docs_path=manual_payload.get('docs_path'),
                    resources_path=manual_payload.get('resources_path'),
                    expected_name=package_name
                )
            else:
                dll_path = manual_payload.get('dll_path')
                if not dll_path:
                    return {'success': False, 'error': 'Plugin DLL is required for manual update'}
                target_dll = self.plugins_dir / f"{package_name}.dll"
                if target_dll.exists():
                    backup_path = self.plugins_dir / f"{package_name}.dll.manual.backup"
                    if backup_path.exists():
                        backup_path.unlink()
                    shutil.move(str(target_dll), str(backup_path))
                self._clear_manual_artifacts(package_name)
                result = self.manual_install_plugin(
                    dll_path,
                    docs_path=manual_payload.get('docs_path'),
                    resources_path=manual_payload.get('resources_path'),
                    expected_name=package_name
                )

            if result['success']:
                if backup_path and backup_path.exists():
                    if backup_path.is_dir():
                        self._remove_directory_safe(backup_path)
                    else:
                        backup_path.unlink()
                return {'success': True, 'message': f'Package "{package_name}" updated manually'}
            else:
                self._restore_manual_backup(package_name, pkg_type, backup_path)
                self.package_tracker.add_package(package_name, pkg_type, old_package_info)
                return {'success': False, 'error': result.get('error', 'Manual update failed')}
        except Exception as e:
            self._restore_manual_backup(package_name, pkg_type, backup_path)
            self.package_tracker.add_package(package_name, pkg_type, old_package_info)
            return {'success': False, 'error': str(e)}

    def _restore_manual_backup(self, package_name, pkg_type, backup_path):
        """Restore package from backup after failed update.
        
        Args:
            package_name: str - Package name to restore
            pkg_type: str - 'addon' or 'plugin'
            backup_path: str/Path - Path to backup directory
        
        Returns:
            bool - True if restore successful, False otherwise
        """
        if not backup_path:
            return
        backup = Path(backup_path)
        if not backup.exists():
            return
        if pkg_type == 'addon':
            target_dir = self.addons_dir / package_name
            if target_dir.exists():
                self._remove_directory_safe(target_dir)
            shutil.move(str(backup), str(target_dir))
        else:
            target_dll = self.plugins_dir / f"{package_name}.dll"
            if target_dll.exists():
                target_dll.unlink()
            shutil.move(str(backup), str(target_dll))
    
    def _get_folder_commit_hash(self, folder_path):
        """Get latest commit hash affecting a specific folder.
        
        Args:
            folder_path: str/Path - Path to folder
        
        Returns:
            str - Commit hash or None if not in git repository
        """
        try:
            result = self._run_command(
                ['git', 'log', '-1', '--format=%H', '--', str(folder_path)],
                cwd=self.ashita_root,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return None
    
    def _get_remote_commit_hash(self, repo_url, branch, path=None):
        """Get latest commit hash from remote repository branch.
        
        Args:
            repo_url: str - Repository URL
            branch: str - Git branch name
            path: Optional str - Specific path to check (None for repo root)
        
        Returns:
            str - Commit hash or None if retrieval failed
        """
        max_retries = 5
        retry_delay = 2
        rate_limited = False
        
        for attempt in range(max_retries):
            try:
                parsed = urlparse(repo_url)
                path_parts = parsed.path.strip('/').split('/')
                
                if len(path_parts) >= 2 and 'github.com' in parsed.netloc:
                    owner, repo = path_parts[0], path_parts[1]
                    
                    if path:
                        api_url = f"https://api.github.com/repos/{owner}/{repo}/commits?path={path}&sha={branch}&per_page=1"
                    else:
                        api_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
                    
                    headers = {}
                    token = self.package_tracker.get_setting('github_token')
                    if not token:
                        token = os.environ.get('GITHUB_TOKEN')
                    if token:
                        headers['Authorization'] = f'token {token}'
                    
                    response = requests.get(api_url, headers=headers or None, timeout=10)
                    
                    if response.status_code == 403:
                        error_data = response.json()
                        if 'rate limit' in error_data.get('message', '').lower():
                            rate_limited = True
                            if attempt < max_retries - 1:
                                wait_time = retry_delay * (2 ** attempt)
                                time.sleep(wait_time)
                                continue
                            else:
                                return {'rate_limited': True}
                    
                    if response.status_code == 200:
                        data = response.json()
                        if isinstance(data, list) and len(data) > 0:
                            return {'sha': data[0]['sha']}
                        elif isinstance(data, dict) and 'sha' in data:
                            return {'sha': data['sha']}
                    
                    return None
                    
            except Exception:
                pass
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
        
        if rate_limited:
            return {'rate_limited': True}
        return None
    
    def remove_package(self, package_name, pkg_type):
        """Remove an installed package.
        
        Args:
            package_name: str - Name of package to remove
            pkg_type: str - 'addon' or 'plugin'
        
        Returns:
            dict - Removal result with keys:
            - success: bool - whether removal succeeded
            - message: str - success/error message
        """
        try:
            package_info = self.package_tracker.get_package(package_name, pkg_type)
            
            if not package_info:
                return {'success': False, 'error': f'Package "{package_name}" not found'}
            
            # Remove files
            if pkg_type == 'addon':
                target_dir = self.addons_dir / package_name
                if target_dir.exists():
                    self._remove_directory_safe(target_dir)
                
                # Remove tracked lib files (only if no other addon uses them)
                if 'lib_files' in package_info:
                    libs_dir = self.addons_dir / 'libs'
                    
                    # Get all other addons and their lib files
                    all_packages = self.package_tracker.get_all_packages()
                    other_addon_lib_files = set()
                    for other_name, other_info in all_packages.get('addons', {}).items():
                        if other_name != package_name and 'lib_files' in other_info:
                            other_addon_lib_files.update(other_info['lib_files'])
                    
                    for lib_file in package_info['lib_files']:
                        if lib_file not in other_addon_lib_files:
                            lib_path = self.ashita_root / lib_file
                            if not lib_path.exists():
                                lib_path = libs_dir / lib_file
                            if lib_path.exists():
                                try:
                                    lib_path.unlink()
                                    parent = lib_path.parent
                                    while parent != libs_dir and parent.exists():
                                        try:
                                            parent.rmdir()
                                            parent = parent.parent
                                        except OSError:
                                            break
                                except Exception:
                                    pass
            else:
                target_dll = self.plugins_dir / f"{package_name}.dll"
                if target_dll.exists():
                    target_dll.unlink()
            
            # Remove tracked docs files (only if no other package uses them)
            if 'doc_files' in package_info:
                docs_base = self.docs_dir / package_name
                
                all_packages = self.package_tracker.get_all_packages()
                other_doc_files = set()
                for pkg_type_name in ['addons', 'plugins']:
                    for other_name, other_info in all_packages.get(pkg_type_name, {}).items():
                        if other_name != package_name and 'doc_files' in other_info:
                            other_doc_files.update(other_info['doc_files'])
                
                for doc_file in package_info['doc_files']:
                    if doc_file not in other_doc_files:
                        doc_path = self.ashita_root / doc_file
                        if not doc_path.exists():
                            doc_path = docs_base / doc_file
                        if doc_path.exists():
                            try:
                                doc_path.unlink()
                            except Exception:
                                pass
                
                if docs_base.exists():
                    try:
                        self._remove_directory_safe(docs_base)
                    except OSError:
                        pass
            
            # Remove tracked resource files (only if no other package uses them)
            if 'resource_files' in package_info:
                resources_base = self.ashita_root / 'resources'
                
                all_packages = self.package_tracker.get_all_packages()
                other_resource_files = set()
                for pkg_type_name in ['addons', 'plugins']:
                    for other_name, other_info in all_packages.get(pkg_type_name, {}).items():
                        if other_name != package_name and 'resource_files' in other_info:
                            other_resource_files.update(other_info['resource_files'])
                
                for resource_file in package_info['resource_files']:
                    if resource_file not in other_resource_files:
                        resource_path = self.ashita_root / resource_file
                        if not resource_path.exists():
                            resource_path = resources_base / resource_file
                        if resource_path.exists():
                            try:
                                resource_path.unlink()
                                parent = resource_path.parent
                                while parent != resources_base and parent.exists():
                                    try:
                                        parent.rmdir()
                                        parent = parent.parent
                                    except OSError:
                                        break
                            except Exception:
                                pass
            
            # Remove from tracker
            self.package_tracker.remove_package(package_name, pkg_type)
            
            return {'success': True, 'message': f'Package "{package_name}" removed successfully'}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def scan_existing_packages(self):
        """Scan for existing addons and plugins on first launch.
        
        Returns:
            dict - Scan result with keys:
            - addons: int - number of addons found
            - plugins: int - number of plugins found
            - official_lookup: dict - official catalog lookup result
            - release_flags: list - packages flagged as manual install
        """
        addon_count = 0
        plugin_count = 0

        catalog = self._fetch_official_repo_catalog(branch=self.official_repo_branch)
        official_addons = catalog.get('addons', set())
        official_plugins = catalog.get('plugins', set())
        catalog_success = catalog.get('success', False)
        official_addons_lower = {name.lower() for name in official_addons}
        official_plugins_lower = {name.lower() for name in official_plugins}

        catalog_summary = {
            'success': catalog_success,
            'error': catalog.get('error')
        }
        release_reasons = []

        # Scan addons
        if self.addons_dir.exists():
            for addon_dir in self.addons_dir.iterdir():
                if not addon_dir.is_dir():
                    continue
                main_lua = addon_dir / f"{addon_dir.name}.lua"
                if not main_lua.exists():
                    continue

                package_info = {
                    'installed_date': datetime.now().isoformat(),
                    'path': str(addon_dir.relative_to(self.ashita_root))
                }

                git_info = self._detect_git_metadata(addon_dir)
                if git_info:
                    package_info['install_method'] = 'git'
                    package_info['source'] = git_info.get('source') or 'unknown'
                    if git_info.get('branch'):
                        package_info['branch'] = git_info['branch']
                    if git_info.get('commit'):
                        package_info['commit'] = git_info['commit']
                elif catalog_success:
                    if addon_dir.name.lower() in official_addons_lower:
                        package_info['install_method'] = 'pre-installed'
                        package_info['source'] = self.official_repo
                        package_info['branch'] = self.official_repo_branch
                        commit_hash = self._get_folder_commit_hash(addon_dir)
                        if commit_hash:
                            package_info['commit'] = commit_hash
                    else:
                        # Unknown source on disk and not listed in official catalog - treat as manual install
                        package_info['install_method'] = 'manual'
                        package_info['source'] = 'unknown'
                        release_reasons.append(f"Addon '{addon_dir.name}' flagged as manual: not listed in official catalog")
                else:
                    package_info['install_method'] = 'pre-installed'
                    package_info['source'] = self.official_repo
                    package_info['branch'] = self.official_repo_branch
                    commit_hash = self._get_folder_commit_hash(addon_dir)
                    if commit_hash:
                        package_info['commit'] = commit_hash

                self.package_tracker.add_package(addon_dir.name, 'addon', package_info)
                addon_count += 1

        # Scan plugins
        if self.plugins_dir.exists():
            plugins_commit_hash = self._get_folder_commit_hash(self.plugins_dir)
            for plugin_file in self.plugins_dir.glob('*.dll'):
                plugin_name = plugin_file.stem
                package_info = {
                    'installed_date': datetime.now().isoformat(),
                    'path': str(plugin_file.relative_to(self.ashita_root))
                }

                plugin_repo_dir = self.plugins_dir / plugin_name
                git_info = None
                if plugin_repo_dir.exists() and plugin_repo_dir.is_dir():
                    git_info = self._detect_git_metadata(plugin_repo_dir)

                if git_info:
                    package_info['install_method'] = 'git'
                    package_info['source'] = git_info.get('source') or 'unknown'
                    if git_info.get('branch'):
                        package_info['branch'] = git_info['branch']
                    if git_info.get('commit'):
                        package_info['commit'] = git_info['commit']
                elif catalog_success:
                    if plugin_name.lower() in official_plugins_lower:
                        package_info['install_method'] = 'pre-installed'
                        package_info['source'] = self.official_repo
                        package_info['branch'] = self.official_repo_branch
                        if plugins_commit_hash:
                            package_info['commit'] = plugins_commit_hash
                    else:
                        # Unknown plugin not in official catalog - treat as manual install
                        package_info['install_method'] = 'manual'
                        package_info['source'] = 'unknown'
                        release_reasons.append(f"Plugin '{plugin_name}' flagged as manual: not listed in official catalog")
                else:
                    package_info['install_method'] = 'pre-installed'
                    package_info['source'] = self.official_repo
                    package_info['branch'] = self.official_repo_branch
                    if plugins_commit_hash:
                        package_info['commit'] = plugins_commit_hash

                self.package_tracker.add_package(plugin_name, 'plugin', package_info)
                plugin_count += 1

        result = {
            'addons': addon_count,
            'plugins': plugin_count,
            'official_lookup': catalog_summary
        }
        if release_reasons:
            result['release_flags'] = release_reasons
        if not catalog_success and catalog.get('error'):
            result['official_lookup_error'] = catalog['error']
        return result
    
    def detect_package_type(self, url):
        """Auto-detect if a repository contains an addon or plugin.
        
        Args:
            url: str - Git repository URL
        
        Returns:
            str - 'addon', 'plugin', or None if detection fails
        """
        try:
            # Create temporary directory for cloning
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Clone repository with depth 1 for speed
                result = self._run_command(
                    ['git', 'clone', '--depth', '1', url, str(temp_path / 'repo')],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode != 0:
                    return None
                
                repo_path = temp_path / 'repo'
                
                plugin_info = self.detector.detect_plugin_structure(repo_path)
                if plugin_info['found']:
                    return 'plugin'
                
                addon_info = self.detector.detect_addon_structure(repo_path)
                if addon_info['found'] or addon_info.get('ambiguous'):
                    return 'addon'
                
                return None
                
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

    def detect_package_type_from_release(self, url):
        """Determine package type by inspecting latest release asset.
        
        Args:
            url: str - Repository URL
        
        Returns:
            str - 'addon', 'plugin', or None if detection fails
        """
        try:
            release_info = self._get_latest_release_url(url)

            download_url = None
            asset_name = None
            if isinstance(release_info, dict):
                if release_info.get('rate_limited'):
                    return None
                assets = release_info.get('assets', [])
                if not assets:
                    return None
                # Prefer .zip assets when multiple exist
                zip_asset = next((a for a in assets if a['name'].lower().endswith('.zip')), None)
                candidate = zip_asset or assets[0]
                download_url = candidate.get('url') or candidate.get('download_url') or candidate.get('browser_download_url')
                asset_name = candidate.get('name', '')
            elif isinstance(release_info, tuple):
                download_url = release_info[0]
                asset_name = release_info[1] if len(release_info) > 1 else ''
            else:
                download_url = release_info
                asset_name = ''

            if not download_url:
                return None

            # Check if it's a direct .dll file
            if asset_name and asset_name.lower().endswith('.dll'):
                return 'plugin'

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                zip_path = temp_path / 'release.zip'

                response = requests.get(download_url, stream=True, timeout=30)
                response.raise_for_status()

                with open(zip_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                extract_path = temp_path / 'extracted'
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_path)

                plugin_info = self.detector.detect_plugin_structure(extract_path)
                if plugin_info.get('found'):
                    return 'plugin'

                addon_info = self.detector.detect_addon_structure(extract_path)
                if addon_info.get('found') or addon_info.get('ambiguous'):
                    return 'addon'

            return None
        except Exception:
            return None

    def list_remote_branches(self, repo_url):
        """List remote branches for a git repository.
        
        Args:
            repo_url: str - Git repository URL
        
        Returns:
            list - List of branch names (strings), or empty list on failure
        """
        try:
            result = self._run_command(
                ['git', 'ls-remote', '--heads', repo_url],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                return []

            branches = []
            for line in result.stdout.splitlines():
                parts = line.split('\t')
                if len(parts) == 2 and parts[1].startswith('refs/heads/'):
                    branch = parts[1].replace('refs/heads/', '')
                    branches.append(branch)

            # Deduplicate and sort, prefer official branch first
            unique = []
            for b in branches:
                if b not in unique:
                    unique.append(b)
            if self.official_repo_branch in unique:
                unique.remove(self.official_repo_branch)
                unique.insert(0, self.official_repo_branch)
            return unique
        except Exception:
            return []
