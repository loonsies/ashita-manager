"""
Package Manager
Handles installation, updates, and removal of Ashita addons and plugins
"""

import os
import shutil
import subprocess
import tempfile
import zipfile
import stat
import requests
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from folder_structure_detector import FolderStructureDetector


class PackageManager:
    def __init__(self, ashita_root, package_tracker):
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
    
    def _handle_remove_readonly(self, func, path, exc):
        """Error handler for Windows file deletion issues"""
        # Handle readonly and locked files (especially in .git folders)
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass
    
    def _remove_directory_safe(self, path):
        """Safely remove a directory handling Windows file locks"""
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
                    subprocess.run(
                        ['cmd', '/c', 'rmdir', '/S', '/Q', str(path)],
                        capture_output=True
                    )
                else:
                    raise
    
    def _detect_current_branch(self):
        """Detect the current git branch of the Ashita installation"""
        try:
            result = subprocess.run(
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
    
    def install_from_git(self, url, pkg_type, target_package_name=None, branch=None):
        """Install a package by cloning from git
        
        Args:
            url: Git repository URL
            pkg_type: 'addon' or 'plugin'
            target_package_name: Optional specific package name to extract (for monorepos)
            branch: Optional specific branch to clone (defaults to repo's default)
        """
        try:
            # Create temporary directory for cloning
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Build clone command with optional branch
                clone_cmd = ['git', 'clone']
                if branch:
                    clone_cmd.extend(['--branch', branch])
                clone_cmd.extend([url, str(temp_path / 'repo')])
                
                # Clone repository
                result = subprocess.run(
                    clone_cmd,
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0:
                    return {'success': False, 'error': f'Git clone failed: {result.stderr}'}
                
                repo_path = temp_path / 'repo'
                
                # Get commit hash
                commit_result = subprocess.run(
                    ['git', 'rev-parse', 'HEAD'],
                    cwd=repo_path,
                    capture_output=True,
                    text=True
                )
                commit_hash = commit_result.stdout.strip()
                
                # Get branch name
                branch_result = subprocess.run(
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
                        # Monorepo - install all addons
                        installed_count = 0
                        failed = []
                        
                        for addon_info in all_addons:
                            result = self._install_single_addon(
                                addon_info, url, commit_hash, branch_name, None, repo_path
                            )
                            if result['success']:
                                installed_count += 1
                            else:
                                failed.append(f"{addon_info['name']}: {result['error']}")
                        
                        if installed_count > 0:
                            msg = f"Installed {installed_count} addon(s) from monorepo"
                            if failed:
                                msg += f" ({len(failed)} failed)"
                            return {'success': True, 'message': msg}
                        else:
                            return {'success': False, 'error': f"Failed to install addons: {'; '.join(failed)}"}
                    else:
                        # Single addon
                        result = self._install_addon(repo_path, url, commit_hash, branch_name, None, target_package_name)
                else:
                    result = self._install_plugin(repo_path, url, commit_hash, branch_name, None, target_package_name)
                
                return result
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def install_from_release(self, url, pkg_type):
        try:
            release_url = self._get_latest_release_url(url)
            
            if isinstance(release_url, dict) and release_url.get('error') == 'rate_limit':
                return {'success': False, 'error': release_url.get('message', 'GitHub API rate limit exceeded')}
            
            if not release_url:
                return {'success': False, 'error': 'Could not find release download URL'}
            
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                zip_path = temp_path / 'release.zip'
                
                response = requests.get(release_url, stream=True)
                response.raise_for_status()
                
                with open(zip_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                extract_path = temp_path / 'extracted'
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_path)
                
                release_tag = self._get_release_tag(url)
                
                if pkg_type == 'addon':
                    result = self._install_addon(extract_path, url, None, None, release_tag)
                else:
                    result = self._install_plugin(extract_path, url, None, None, release_tag)
                
                return result
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _install_single_addon(self, addon_info, url, commit_hash=None, branch_name=None, release_tag=None, repo_root=None):
        """Install a single addon from addon_info dict (used for monorepos)"""
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
            
            # Copy addon files
            shutil.copytree(addon_source, target_dir)
            
            # Track package FIRST (so _copy_extra_folders can update it)
            install_method = 'git' if commit_hash else 'release'
            package_info = {
                'source': url,
                'install_method': install_method,
                'installed_date': datetime.now().isoformat(),
                'path': str(target_dir.relative_to(self.ashita_root))
            }
            
            if commit_hash:
                # For monorepos, get folder-specific commit
                if url == self.official_repo:
                    folder_path = f'addons/{addon_name}'
                    folder_commit_result = subprocess.run(
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
            
            # Copy extra folders (libs, etc.) - this will update package_info with lib_files
            if repo_root:
                self._copy_extra_folders(repo_root, addon_name, is_monorepo=True)
            
            return {'success': True, 'message': f'Addon "{addon_name}" installed successfully'}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _install_addon(self, source_path, url, commit_hash=None, branch_name=None, release_tag=None, target_name=None):
        try:
            repo_root = source_path
            addon_info = self.detector.detect_addon_structure(source_path, target_name)
            
            if not addon_info['found']:
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
            
            if addon_info['structure'] == 'root':
                shutil.copytree(addon_source, target_dir)
            else:
                shutil.copytree(addon_source, target_dir)
            
            self._copy_extra_folders(repo_root, addon_name)
            
            # Track package
            install_method = 'git' if commit_hash else 'release'
            package_info = {
                'source': url,
                'install_method': install_method,
                'installed_date': datetime.now().isoformat(),
                'path': str(target_dir.relative_to(self.ashita_root))
            }
            
            if commit_hash:
                # For monorepos (like official repo), get folder-specific commit
                if url == self.official_repo:
                    folder_path = f'addons/{addon_name}'
                    folder_commit_result = subprocess.run(
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
            
            return {'success': True, 'message': f'Addon "{addon_name}" installed successfully'}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _install_plugin(self, source_path, url, commit_hash=None, branch_name=None, release_tag=None, target_name=None):
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
                    return {'success': False, 'error': f'Plugin "{plugin_name}" already exists'}
            
            shutil.copy2(plugin_info['dll_path'], target_dll)
            
            self._copy_extra_folders(repo_root, plugin_name)
            
            # Track package
            install_method = 'git' if commit_hash else 'release'
            package_info = {
                'source': url,
                'install_method': install_method,
                'installed_date': datetime.now().isoformat(),
                'path': str(target_dll.relative_to(self.ashita_root))
            }
            
            if commit_hash:
                # For monorepos (like official repo), get folder-specific commit
                if url == self.official_repo:
                    # For plugins, the path in git is to the dll file
                    folder_path = f'plugins/{plugin_name}.dll'
                    folder_commit_result = subprocess.run(
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
            
            return {'success': True, 'message': f'Plugin "{plugin_name}" installed successfully'}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _copy_extra_folders(self, source_path, package_name, is_monorepo=False):
        source_path = Path(source_path)
        
        has_addons_folder = (source_path / 'addons').exists()
        has_plugins_folder = (source_path / 'plugins').exists()
        is_multi_folder_repo = has_addons_folder or has_plugins_folder
        
        if not is_multi_folder_repo:
            return
        
        # Handle libs folder - MERGE instead of replace for monorepos
        libs_source = source_path / 'addons' / 'libs'
        if libs_source.exists() and libs_source.is_dir():
            libs_target = self.addons_dir / 'libs'
            libs_target.mkdir(exist_ok=True)
            
            # Track which lib files belong to this package
            lib_files = []
            
            # Copy/merge libs files
            for item in libs_source.rglob('*'):
                if item.is_file():
                    rel_path = item.relative_to(libs_source)
                    target_file = libs_target / rel_path
                    
                    # Create parent directories if needed
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Copy the file
                    shutil.copy2(item, target_file)
                    
                    # Track this file for this package
                    lib_files.append(str(rel_path))
            
            # Store lib files in package metadata
            if lib_files:
                pkg = self.package_tracker.get_package(package_name, 'addon')
                if pkg:
                    pkg['lib_files'] = lib_files
                    self.package_tracker.save()
        
        package_docs_locations = [
            source_path / 'docs',
            source_path / 'Docs',
        ]
        
        for docs_location in package_docs_locations:
            if docs_location.exists() and docs_location.is_dir():
                target_docs = self.docs_dir / package_name
                if target_docs.exists():
                    self._remove_directory_safe(target_docs)
                shutil.copytree(docs_location, target_docs)
                break
        
        package_resources_locations = [
            source_path / 'resources',
            source_path / 'Resources',
        ]
        
        for res_location in package_resources_locations:
            if res_location.exists() and res_location.is_dir():
                resources_dir = self.ashita_root / 'resources'
                resources_dir.mkdir(exist_ok=True)
                target_resources = resources_dir / package_name
                if target_resources.exists():
                    self._remove_directory_safe(target_resources)
                shutil.copytree(res_location, target_resources)
                break
    
    def _get_latest_release_url(self, repo_url):
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
                    return {'error': 'rate_limit', 'message': 'GitHub API rate limit exceeded'}
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            
            if 'assets' in data and len(data['assets']) > 0:
                return data['assets'][0]['browser_download_url']
            
            if 'zipball_url' in data:
                return data['zipball_url']
            
            return None
            
        except Exception:
            return None
    
    def _get_release_tag(self, repo_url):
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
    
    def update_package(self, package_name, pkg_type):
        """Update an existing package"""
        try:
            package_info = self.package_tracker.get_package(package_name, pkg_type)
            
            if not package_info:
                return {'success': False, 'error': f'Package "{package_name}" not found'}
            
            install_method = package_info.get('install_method')
            source_url = package_info.get('source')
            current_commit = package_info.get('commit')
            branch = package_info.get('branch', self.official_repo_branch)
            
            # Handle pre-installed packages
            if install_method == 'pre-installed' or source_url == 'pre-installed':
                source_url = self.official_repo
                install_method = 'git'
            
            if not source_url:
                return {'success': False, 'error': 'Package source URL not found'}
            
            # Check if package is already up-to-date (for git installations)
            if install_method == 'git' and current_commit:
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
            
            old_package_info = package_info.copy()
            
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
            
            # Try to reinstall with branch for official repo
            try:
                if install_method == 'git':
                    # Use detected branch if it's the official repo
                    branch = self.official_repo_branch if source_url == self.official_repo else None
                    result = self.install_from_git(source_url, pkg_type, target_package_name=package_name, branch=branch)
                else:
                    result = self.install_from_release(source_url, pkg_type)
                
                if result['success']:
                    # Success, remove backup
                    if backup_path and backup_path.exists():
                        if backup_path.is_dir():
                            self._remove_directory_safe(backup_path)
                        else:
                            backup_path.unlink()
                    
                    # Remove old docs
                    docs_path = self.docs_dir / package_name
                    if docs_path.exists():
                        self._remove_directory_safe(docs_path)
                    
                    # Remove old resources
                    resources_dir = self.ashita_root / 'resources'
                    resources_path = resources_dir / package_name
                    if resources_path.exists():
                        self._remove_directory_safe(resources_path)
                    
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
                    return {'success': False, 'error': f'Update failed: {result["error"]}'}
            
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
    
    def _get_folder_commit_hash(self, folder_path):
        """Get the last commit hash that affected a specific folder"""
        try:
            result = subprocess.run(
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
        """Remove a package"""
        try:
            package_info = self.package_tracker.get_package(package_name, pkg_type)
            
            if not package_info:
                return {'success': False, 'error': f'Package "{package_name}" not found'}
            
            # Remove files
            if pkg_type == 'addon':
                target_dir = self.addons_dir / package_name
                if target_dir.exists():
                    self._remove_directory_safe(target_dir)
                
                # Remove tracked lib files
                if 'lib_files' in package_info:
                    libs_dir = self.addons_dir / 'libs'
                    for lib_file in package_info['lib_files']:
                        lib_path = libs_dir / lib_file
                        if lib_path.exists():
                            try:
                                lib_path.unlink()
                                # Remove empty parent directories
                                parent = lib_path.parent
                                while parent != libs_dir and parent.exists():
                                    try:
                                        parent.rmdir()  # Only removes if empty
                                        parent = parent.parent
                                    except OSError:
                                        break
                            except Exception:
                                pass  # Continue even if file removal fails
            else:
                target_dll = self.plugins_dir / f"{package_name}.dll"
                if target_dll.exists():
                    target_dll.unlink()
            
            # Remove docs if they exist
            docs_path = self.docs_dir / package_name
            if docs_path.exists():
                self._remove_directory_safe(docs_path)
            
            # Remove resources if they exist
            resources_dir = self.ashita_root / 'resources'
            resources_path = resources_dir / package_name
            if resources_path.exists():
                self._remove_directory_safe(resources_path)
            
            # Remove from tracker
            self.package_tracker.remove_package(package_name, pkg_type)
            
            return {'success': True, 'message': f'Package "{package_name}" removed successfully'}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def scan_existing_packages(self):
        """Scan for existing addons and plugins on first launch"""
        addon_count = 0
        plugin_count = 0
        
        # Scan addons
        if self.addons_dir.exists():
            for addon_dir in self.addons_dir.iterdir():
                if addon_dir.is_dir():
                    # Check if main lua file exists
                    main_lua = addon_dir / f"{addon_dir.name}.lua"
                    if main_lua.exists():
                        # Get commit hash for this addon folder
                        commit_hash = self._get_folder_commit_hash(addon_dir)
                        
                        # Add to tracker as pre-installed with official repo
                        package_info = {
                            'source': self.official_repo,
                            'install_method': 'pre-installed',
                            'installed_date': datetime.now().isoformat(),
                            'path': str(addon_dir.relative_to(self.ashita_root)),
                            'branch': self.official_repo_branch
                        }
                        
                        if commit_hash:
                            package_info['commit'] = commit_hash
                        
                        self.package_tracker.add_package(addon_dir.name, 'addon', package_info)
                        addon_count += 1
        
        # Scan plugins
        if self.plugins_dir.exists():
            for plugin_file in self.plugins_dir.glob('*.dll'):
                plugin_name = plugin_file.stem
                
                # Get commit hash for the plugins folder
                commit_hash = self._get_folder_commit_hash(self.plugins_dir)
                
                # Add to tracker as pre-installed with official repo
                package_info = {
                    'source': self.official_repo,
                    'install_method': 'pre-installed',
                    'installed_date': datetime.now().isoformat(),
                    'path': str(plugin_file.relative_to(self.ashita_root)),
                    'branch': self.official_repo_branch
                }
                
                if commit_hash:
                    package_info['commit'] = commit_hash
                
                self.package_tracker.add_package(plugin_name, 'plugin', package_info)
                plugin_count += 1
        
        return {'addons': addon_count, 'plugins': plugin_count}
    
    def detect_package_type(self, url):
        """
        Auto-detect if a repository contains an addon or plugin
        Returns 'addon', 'plugin', or None if detection fails
        """
        try:
            # Create temporary directory for cloning
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Clone repository with depth 1 for speed
                result = subprocess.run(
                    ['git', 'clone', '--depth', '1', url, str(temp_path / 'repo')],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode != 0:
                    return None
                
                repo_path = temp_path / 'repo'
                
                # Try to detect plugin first (more specific - .dll files)
                plugin_info = self.detector.detect_plugin_structure(repo_path)
                if plugin_info['found']:
                    return 'plugin'
                
                # Then try to detect addon (.lua files)
                addon_info = self.detector.detect_addon_structure(repo_path)
                if addon_info['found']:
                    return 'addon'
                
                return None
                
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

    def list_remote_branches(self, repo_url):
        """List remote branches for a git repository URL using `git ls-remote --heads`.

        Returns a list of branch names (strings) or an empty list on failure.
        """
        try:
            result = subprocess.run(
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
