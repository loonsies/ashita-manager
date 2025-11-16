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

    def _run_command(self, cmd, cwd=None, **kwargs):
        """Run a subprocess command while avoiding creating a new console window on Windows."""
        if os.name == 'nt':
            kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
        return subprocess.run(cmd, cwd=cwd, **kwargs)
    
    def _handle_remove_readonly(self, func, path, exc):
        """Error handler for Windows file deletion issues"""
        # Handle readonly and locked files
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
                    self._run_command(
                        ['cmd', '/c', 'rmdir', '/S', '/Q', str(path)],
                        capture_output=True
                    )
                else:
                    raise
    
    def _detect_current_branch(self):
        """Detect the current git branch of the Ashita installation"""
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
    
    def install_from_git(self, url, pkg_type, target_package_name=None, branch=None, force=False, plugin_variant=None):
        """Install a package by cloning from git
        
        Args:
            url: Git repository URL
            pkg_type: 'addon' or 'plugin'
            target_package_name: Optional specific package name to extract (for monorepos)
            branch: Optional specific branch to clone (defaults to repo's default)
            force: Skip conflict checking if True
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
                        result = self._install_addon(repo_path, url, commit_hash, branch_name, None, target_package_name, force=force)
                else:
                    # Plugin repo: look for variant folders containing .dll files
                    variants = []
                    try:
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
    
    def install_from_release(self, url, pkg_type, force=False, plugin_variant=None, asset_download_url=None, asset_name=None):
        try:
            if asset_download_url:
                release_url = (asset_download_url, asset_name or self._infer_asset_name(asset_download_url))
            else:
                release_url = self._get_latest_release_url(url, preferred_asset_name=asset_name)
            
            if isinstance(release_url, dict):
                if release_url.get('error') == 'rate_limit':
                    return {'success': False, 'error': release_url.get('message', 'GitHub API rate limit exceeded')}
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
                        release_asset_name=release_asset_name
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
    
    def _install_addon(self, source_path, url, commit_hash=None, branch_name=None, release_tag=None, target_name=None, force=False, release_asset_name=None):
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
    
    def _check_file_conflicts(self, source_path, package_name, source_url=None):
        """Check for file conflicts in libs/docs/resources folders. Returns dict with conflict info.
        
        Args:
            source_path: Path to the repository being installed
            package_name: Name of the package being installed
            source_url: URL of the repository (used to check if conflicts are from same repo)
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
        try:
            parsed = urlparse(download_url)
            if not parsed.path:
                return None
            return Path(parsed.path).name
        except Exception:
            return None

    def _tokenize_asset_name(self, name):
        if not name:
            return []
        tokens = re.split(r'[^a-z0-9]+', name.lower())
        return [t for t in tokens if t and len(t) > 2 and not t.isdigit()]

    def _score_asset_match(self, candidate_name, tokens):
        if not candidate_name or not tokens:
            return 0
        candidate_lower = candidate_name.lower()
        score = 0
        for token in tokens:
            if token and token in candidate_lower:
                score += 1
        return score
    
    def update_package(self, package_name, pkg_type, release_asset_url=None, release_asset_name=None):
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
            
            # Check if package is already up-to-date
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
            
            try:
                if install_method == 'git':
                    branch = self.official_repo_branch if source_url == self.official_repo else None
                    result = self.install_from_git(source_url, pkg_type, target_package_name=package_name, branch=branch)
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
    
    def _get_folder_commit_hash(self, folder_path):
        """Get the last commit hash that affected a specific folder"""
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
                if addon_info['found']:
                    return 'addon'
                
                return None
                
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

    def detect_package_type_from_release(self, url):
        """Attempt to determine package type by inspecting the latest release asset."""
        try:
            release_info = self._get_latest_release_url(url)

            download_url = None
            if isinstance(release_info, dict):
                if release_info.get('error') == 'rate_limit':
                    return None
                assets = release_info.get('assets', [])
                if not assets:
                    return None
                # Prefer .zip assets when multiple exist
                zip_asset = next((a for a in assets if a['name'].lower().endswith('.zip')), None)
                candidate = zip_asset or assets[0]
                download_url = candidate.get('url') or candidate.get('download_url') or candidate.get('browser_download_url')
            elif isinstance(release_info, tuple):
                download_url = release_info[0]
            else:
                download_url = release_info

            if not download_url:
                return None

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
                if addon_info.get('found'):
                    return 'addon'

            return None
        except Exception:
            return None

    def list_remote_branches(self, repo_url):
        """List remote branches for a git repository URL using `git ls-remote --heads`.

        Returns a list of branch names (strings) or an empty list on failure.
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
