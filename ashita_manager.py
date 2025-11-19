"""
Ashita v4 Addon/Plugin Manager
A PyQt6-based package manager for Ashita v4 addons and plugins
"""

__version__ = "1.0"

import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QComboBox, QListWidget, QListWidgetItem, QTabWidget,
                             QMessageBox, QProgressDialog, QGroupBox, QTextEdit,
                             QDialog, QDialogButtonBox, QFormLayout, QFileDialog,
                             QSpinBox, QScrollArea, QInputDialog, QStyle,
                             QTreeWidget, QTreeWidgetItem, QStackedWidget)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QIcon, QGuiApplication

# Try to import WebEngine for markdown rendering
try:
    from markdown_viewer import MarkdownViewer
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False

import qdarktheme
from package_manager import PackageManager
from package_tracker import PackageTracker
from script_parser import ScriptParser


class InstallWorker(QThread):
    """Thread worker for package git/release installation.
    
    Signals:
        finished(success, message) - Installation complete
        conflict_detected(result) - File conflicts found
        variant_selection_requested(result) - Plugin variant selection needed
        entrypoint_selection_requested(result) - Addon lua file selection needed
        progress(message) - Installation progress update
    """
    finished = pyqtSignal(bool, str)
    conflict_detected = pyqtSignal(dict)
    variant_selection_requested = pyqtSignal(dict)
    entrypoint_selection_requested = pyqtSignal(dict)
    progress = pyqtSignal(str)
    
    def __init__(self, package_manager, url, pkg_type, install_method, branch=None, force=False, plugin_variant=None, release_asset_url=None, release_asset_name=None, selected_entrypoint=None):
        """Initialize installation worker.
        
        Args:
            package_manager: PackageManager - Package manager instance
            url: str - Repository URL
            pkg_type: str - 'addon' or 'plugin'
            install_method: str - 'Clone' or 'Release'
            branch: Optional str - Git branch for clone method
            force: bool - Skip conflict checking
            plugin_variant: Optional str - Specific plugin variant
            release_asset_url: Optional str - Specific release asset URL
            release_asset_name: Optional str - Specific release asset name
            selected_entrypoint: Optional str - Addon lua file entrypoint
        """
        super().__init__()
        self.package_manager = package_manager
        self.url = url
        self.pkg_type = pkg_type
        self.install_method = install_method
        self.branch = branch
        self.force = force
        self.plugin_variant = plugin_variant
        self.release_asset_url = release_asset_url
        self.release_asset_name = release_asset_name
        self.selected_entrypoint = selected_entrypoint
    
    def run(self):
        """Execute installation based on method type.
        
        Emits appropriate signals: finished, conflict_detected,
        variant_selection_requested, or entrypoint_selection_requested
        """
        try:
            if self.install_method == "Clone":
                self.progress.emit(f"Cloning repository from {self.url}...")
                result = self.package_manager.install_from_git(
                    self.url, 
                    self.pkg_type, 
                    branch=self.branch, 
                    force=self.force, 
                    plugin_variant=self.plugin_variant,
                    selected_entrypoint=self.selected_entrypoint
                )
            else:  # Release
                self.progress.emit(f"Downloading release from {self.url}...")
                result = self.package_manager.install_from_release(
                    self.url,
                    self.pkg_type,
                    force=self.force,
                    plugin_variant=self.plugin_variant,
                    asset_download_url=self.release_asset_url,
                    asset_name=self.release_asset_name,
                    selected_entrypoint=self.selected_entrypoint
                )
            
            if result['success']:
                self.finished.emit(True, result['message'])
            elif result.get('requires_confirmation'):
                self.conflict_detected.emit(result)
            elif result.get('requires_variant_selection'):
                self.variant_selection_requested.emit(result)
            elif result.get('requires_entrypoint_selection'):
                self.entrypoint_selection_requested.emit(result)
            else:
                self.finished.emit(False, result['error'])
        except Exception as e:
            self.finished.emit(False, str(e))


class ManualInstallWorker(QThread):
    finished = pyqtSignal(bool, str)
    entrypoint_selection_requested = pyqtSignal(dict)
    progress = pyqtSignal(str)

    def __init__(self, package_manager, payload):
        """Initialize manual installation worker.
        
        Args:
            package_manager: PackageManager - Package manager instance
            payload: dict - Installation payload with addon_path, dll_path, etc
        """
        super().__init__()
        self.package_manager = package_manager
        self.payload = payload

    def run(self):
        """Execute manual installation from provided payload.
        
        Emits: finished(success, message) or entrypoint_selection_requested(result)
        """
        try:
            pkg_type = self.payload.get('pkg_type')
            if pkg_type == 'addon':
                self.progress.emit("Copying addon files...")
                result = self.package_manager.manual_install_addon(
                    self.payload.get('addon_path'),
                    docs_path=self.payload.get('docs_path'),
                    resources_path=self.payload.get('resources_path'),
                    selected_entrypoint=self.payload.get('selected_entrypoint')
                )
            else:
                self.progress.emit("Copying plugin files...")
                result = self.package_manager.manual_install_plugin(
                    self.payload.get('dll_path'),
                    docs_path=self.payload.get('docs_path'),
                    resources_path=self.payload.get('resources_path')
                )

            if result.get('success'):
                self.finished.emit(True, result.get('message', 'Manual installation completed'))
            elif result.get('requires_entrypoint_selection'):
                self.entrypoint_selection_requested.emit(result)
            else:
                self.finished.emit(False, result.get('error', 'Manual installation failed'))
        except Exception as e:
            self.finished.emit(False, str(e))


class UpdateWorker(QThread):
    """Worker thread for package update"""
    finished = pyqtSignal(bool, str, bool)
    progress = pyqtSignal(str)
    variant_selection_requested = pyqtSignal(dict)
    entrypoint_selection_requested = pyqtSignal(dict)
    manual_update_requested = pyqtSignal(dict)
    
    def __init__(self, package_manager, package_name, pkg_type, release_asset_url=None, release_asset_name=None, manual_payload=None):
        """Initialize update worker.
        
        Args:
            package_manager: PackageManager - Package manager instance
            package_name: str - Package to update
            pkg_type: str - 'addon' or 'plugin'
            release_asset_url: Optional str - Specific release asset URL
            release_asset_name: Optional str - Specific release asset name
            manual_payload: Optional dict - Manual update payload
        """
        super().__init__()
        self.package_manager = package_manager
        self.package_name = package_name
        self.pkg_type = pkg_type
        self.release_asset_url = release_asset_url
        self.release_asset_name = release_asset_name
        self.manual_payload = manual_payload
    
    def run(self):
        """Execute package update operation.
        
        Emits: finished(success, message, already_updated) or variant/manual selection signals
        """
        try:
            self.progress.emit(f"Updating {self.package_name}...")
            result = self.package_manager.update_package(
                self.package_name,
                self.pkg_type,
                release_asset_url=self.release_asset_url,
                release_asset_name=self.release_asset_name,
                manual_payload=self.manual_payload
            )
            if result.get('requires_variant_selection'):
                self.variant_selection_requested.emit(result)
                return
            if result.get('requires_entrypoint_selection'):
                self.entrypoint_selection_requested.emit(result)
                return
            if result.get('requires_manual_update'):
                self.manual_update_requested.emit(result)
                return
            
            if result['success']:
                already_updated = result.get('already_updated', False)
                self.finished.emit(True, result['message'], already_updated)
            else:
                self.finished.emit(False, result['error'], False)
        except Exception as e:
            self.finished.emit(False, str(e), False)


class BatchUpdateWorker(QThread):
    """Worker thread for batch update"""
    finished = pyqtSignal(int, int, int)  # updated, failed, skipped
    progress = pyqtSignal(str, int, int)
    log = pyqtSignal(str)
    
    def __init__(self, package_manager, package_list, pkg_type):
        """Initialize batch update worker.
        
        Args:
            package_manager: PackageManager - Package manager instance
            package_list: list - List of package names to update
            pkg_type: str - 'addon' or 'plugin'
        """
        super().__init__()
        self.package_manager = package_manager
        self.package_list = package_list
        self.pkg_type = pkg_type
        self._is_cancelled = False
    
    def cancel(self):
        """Request cancellation of batch update process."""
        self._is_cancelled = True
    
    def run(self):
        """Execute batch update for multiple packages.
        
        Emits: progress(message), finished(updated, failed)
        """
        updated = 0
        failed = 0
        skipped = 0
        total = len(self.package_list)
        
        for idx, package_name in enumerate(self.package_list):
            # Check for cancellation
            if self._is_cancelled:
                self.log.emit(f"Batch update cancelled by user")
                break
            
            self.progress.emit(f"Checking {package_name}...", idx, total)
            self.log.emit(f"[{idx + 1}/{total}] Checking {package_name}...")
            
            result = self.package_manager.update_package(package_name, self.pkg_type)
            
            if result.get('requires_manual_update'):
                failed += 1
                reason = result.get('reason') or 'Manual input required'
                self.log.emit(f"{package_name} requires manual update ({reason}). Skipping in batch.")
                continue

            if result['success']:
                if result.get('already_updated', False):
                    skipped += 1
                    self.log.emit(f"{package_name} already up-to-date")
                else:
                    updated += 1
                    self.log.emit(f"{package_name} updated successfully")
            else:
                failed += 1
                self.log.emit(f"{package_name} failed: {result.get('error', 'Unknown error')}")
        self.finished.emit(updated, failed, skipped)


class ScanWorker(QThread):
    finished = pyqtSignal(dict)
    progress = pyqtSignal(str)
    
    def __init__(self, package_manager):
        """Initialize scan worker.
        
        Args:
            package_manager: PackageManager - Package manager instance
        """
        super().__init__()
        self.package_manager = package_manager
    
    def run(self):
        """Scan existing addons and plugins.
        
        Emits: finished(results)
        """
        try:
            self.progress.emit("Scanning for existing packages...")
            results = self.package_manager.scan_existing_packages()
            self.finished.emit(results)
        except Exception as e:
            self.finished.emit({'addons': 0, 'plugins': 0, 'error': str(e)})


class ManualPackageDialog(QDialog):
    def __init__(self, parent=None, mode='install', pkg_type=None, package_name=None):
        """Initialize manual installation dialog.
        
        Args:
            parent: Optional QWidget - Parent window
            mode: str - 'install' or 'update'
            pkg_type: Optional str - 'addon' or 'plugin' (auto-selected if not specified)
            package_name: Optional str - Package name for update mode
        """
        super().__init__(parent)
        self.mode = mode
        self.package_name = package_name
        self.setWindowTitle("Manual Update" if mode == 'update' else "Manual Install")
        self.setModal(True)
        self.resize(520, 200)

        layout = QVBoxLayout(self)

        if package_name:
            info_label = QLabel(f"Target package: {package_name}")
            info_label.setStyleSheet("font-weight: bold;")
            layout.addWidget(info_label)

        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Package type:"))
        self.type_selector = QComboBox()
        self.type_selector.addItems(["Addon", "Plugin"])
        if pkg_type:
            idx = 0 if pkg_type == 'addon' else 1
            self.type_selector.setCurrentIndex(idx)
            if mode == 'update':
                self.type_selector.setEnabled(False)
        type_layout.addWidget(self.type_selector)
        type_layout.addStretch()
        layout.addLayout(type_layout)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_addon_form())
        self.stack.addWidget(self._build_plugin_form())
        layout.addWidget(self.stack)

        self.type_selector.currentIndexChanged.connect(self.stack.setCurrentIndex)
        
        # Sync stack with initial type selection
        if pkg_type:
            self.stack.setCurrentIndex(0 if pkg_type == 'addon' else 1)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self._handle_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _build_addon_form(self):
        """Build addon installation form with path selections.
        
        Returns:
            QWidget - Form widget with addon path inputs
        """
        widget = QWidget()
        form = QFormLayout(widget)

        self.addon_path_input = QLineEdit()
        addon_browse = QPushButton("Browse...")
        addon_browse.clicked.connect(lambda: self._browse_folder(self.addon_path_input, "Select addon folder"))
        addon_row = QHBoxLayout()
        addon_row.addWidget(self.addon_path_input)
        addon_row.addWidget(addon_browse)
        form.addRow("Addon folder:", addon_row)

        self.addon_docs_input = QLineEdit()
        docs_browse = QPushButton("Browse...")
        docs_browse.clicked.connect(lambda: self._browse_folder(self.addon_docs_input, "Select docs folder"))
        docs_row = QHBoxLayout()
        docs_row.addWidget(self.addon_docs_input)
        docs_row.addWidget(docs_browse)
        form.addRow("Docs folder (optional):", docs_row)

        self.addon_resources_input = QLineEdit()
        res_browse = QPushButton("Browse...")
        res_browse.clicked.connect(lambda: self._browse_folder(self.addon_resources_input, "Select resources folder"))
        res_row = QHBoxLayout()
        res_row.addWidget(self.addon_resources_input)
        res_row.addWidget(res_browse)
        form.addRow("Resources folder (optional):", res_row)

        return widget

    def _build_plugin_form(self):
        """Build plugin installation form with DLL selection.
        
        Returns:
            QWidget - Form widget with plugin DLL input
        """
        widget = QWidget()
        form = QFormLayout(widget)

        self.plugin_dll_input = QLineEdit()
        dll_browse = QPushButton("Browse...")
        dll_browse.clicked.connect(self._browse_dll)
        dll_row = QHBoxLayout()
        dll_row.addWidget(self.plugin_dll_input)
        dll_row.addWidget(dll_browse)
        form.addRow("Plugin DLL:", dll_row)

        self.plugin_docs_input = QLineEdit()
        plugin_docs_browse = QPushButton("Browse...")
        plugin_docs_browse.clicked.connect(lambda: self._browse_folder(self.plugin_docs_input, "Select docs folder"))
        plugin_docs_row = QHBoxLayout()
        plugin_docs_row.addWidget(self.plugin_docs_input)
        plugin_docs_row.addWidget(plugin_docs_browse)
        form.addRow("Docs folder (optional):", plugin_docs_row)

        self.plugin_resources_input = QLineEdit()
        plugin_res_browse = QPushButton("Browse...")
        plugin_res_browse.clicked.connect(lambda: self._browse_folder(self.plugin_resources_input, "Select resources folder"))
        plugin_res_row = QHBoxLayout()
        plugin_res_row.addWidget(self.plugin_resources_input)
        plugin_res_row.addWidget(plugin_res_browse)
        form.addRow("Resources folder (optional):", plugin_res_row)

        return widget

    def _browse_folder(self, line_edit, caption):
        """Open folder browser dialog and update line edit.
        
        Args:
            line_edit: QLineEdit - Text field to update with selection
            caption: str - Dialog title
        """
        folder = QFileDialog.getExistingDirectory(self, caption, os.path.expanduser("~"))
        if folder:
            line_edit.setText(folder)

    def _browse_dll(self):
        """Open file browser for DLL selection.
        
        Updates dll_path field with selected file.
        """
        file_path, _ = QFileDialog.getOpenFileName(self, "Select plugin DLL", os.path.expanduser("~"), "DLL Files (*.dll)")
        if file_path:
            self.plugin_dll_input.setText(file_path)

    def _selected_pkg_type(self):
        """Get currently selected package type.
        
        Returns:
            str - 'addon' or 'plugin'
        """
        return 'addon' if self.type_selector.currentIndex() == 0 else 'plugin'

    def _handle_accept(self):
        """Handle accept button - validate and close dialog."""
        if not self._validate_inputs():
            return
        self.accept()

    def _validate_inputs(self):
        """Validate user inputs for addon or plugin installation.
        
        Returns:
            bool - True if all inputs are valid, False otherwise
        """
        pkg_type = self._selected_pkg_type()
        if pkg_type == 'addon':
            path = self.addon_path_input.text().strip()
            if not path:
                self._show_error("Please select an addon folder.")
                return False
            if not os.path.isdir(path):
                self._show_error("Addon folder does not exist.")
                return False
        else:
            dll_path = self.plugin_dll_input.text().strip()
            if not dll_path:
                self._show_error("Please select a plugin DLL file.")
                return False
            if not os.path.isfile(dll_path) or not dll_path.lower().endswith('.dll'):
                self._show_error("Invalid DLL file selected.")
                return False
        return True

    def _show_error(self, message):
        """Show error dialog with validation message.
        
        Args:
            message: str - Error message to display
        """
        QMessageBox.warning(self, "Invalid input", message)

    def get_payload(self):
        """Get installation payload from form inputs.
        
        Returns:
            dict - Payload with pkg_type, addon_path, dll_path, docs_path, resources_path
        """
        pkg_type = self._selected_pkg_type()
        if pkg_type == 'addon':
            return {
                'pkg_type': 'addon',
                'addon_path': self.addon_path_input.text().strip(),
                'docs_path': self._optional_path(self.addon_docs_input.text()),
                'resources_path': self._optional_path(self.addon_resources_input.text())
            }
        return {
            'pkg_type': 'plugin',
            'dll_path': self.plugin_dll_input.text().strip(),
            'docs_path': self._optional_path(self.plugin_docs_input.text()),
            'resources_path': self._optional_path(self.plugin_resources_input.text())
        }

    def _optional_path(self, value):
        """Convert path value to string or None if empty.
        
        Args:
            value: str or Path - Path value to normalize
        
        Returns:
            str or None - Path string or None if empty
        """
        value = (value or '').strip()
        return value or None

class SettingsDialog(QDialog):
    
    def __init__(self, package_tracker, ashita_path, parent=None):
        """Initialize settings dialog.
        
        Args:
            package_tracker: PackageTracker - Package tracker instance
            ashita_path: str - Current Ashita installation path
            parent: Optional QWidget - Parent window
        """
        super().__init__(parent)
        self.package_tracker = package_tracker
        self.current_ashita_path = ashita_path
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout(self)
        
        form_layout = QFormLayout()
        
        ashita_path_label = QLabel("Ashita Path:")
        ashita_path_label.setToolTip("Path to Ashita installation folder")
        self.path_input = QLineEdit()
        self.path_input.setReadOnly(True)
        
        if ashita_path:
            self.path_input.setText(ashita_path)
        
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.path_input)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_ashita_path)
        path_layout.addWidget(browse_btn)
        
        form_layout.addRow(ashita_path_label, path_layout)
        
        token_label = QLabel("GitHub Token:")
        token_label.setToolTip("Token for GitHub API (increases rate limit)")
        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText("ghp_xxxxxxxxxxxxxxxxxxxx")
        
        existing_token = self.package_tracker.get_setting('github_token', '')
        if existing_token:
            self.token_input.setText(existing_token)
        
        token_layout = QHBoxLayout()
        token_layout.addWidget(self.token_input)
        self.show_token_btn = QPushButton("Show")
        self.show_token_btn.setMaximumWidth(60)
        self.show_token_btn.clicked.connect(self.toggle_token_visibility)
        token_layout.addWidget(self.show_token_btn)
        
        form_layout.addRow(token_label, token_layout)
        
        # Help text
        help_text = QLabel(
            "To avoid GitHub API rate limits:\n"
            "1. Go to GitHub.com → Settings → Developer settings → Fine-grained tokens\n"
            "2. Generate a new token with 'Public repositories' scope\n"
            "3. Copy and paste the token above\n\n"
            "Without a token, you're limited to ~60 API calls per hour."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: gray; font-size: 10pt; padding: 10px;")
        
        layout.addLayout(form_layout)
        layout.addWidget(help_text)
        
        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.save_settings)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def toggle_token_visibility(self):
        """Toggle GitHub token visibility between password and plain text."""
        if self.token_input.echoMode() == QLineEdit.EchoMode.Password:
            self.token_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_token_btn.setText("Hide")
        else:
            self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_token_btn.setText("Show")
    
    def browse_ashita_path(self):
        """Open folder browser to select Ashita installation path."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Ashita Installation Folder",
            self.path_input.text() or os.path.expanduser("~")
        )
        if folder:
            self.path_input.setText(folder)
    
    def save_settings(self):
        """Save settings to package tracker.
        
        Returns:
            bool - True if save successful, False if validation failed
        """
        ashita_path = self.path_input.text().strip()
        path_changed = False
        
        if ashita_path and ashita_path != self.current_ashita_path:
            if not os.path.isdir(ashita_path):
                QMessageBox.warning(
                    self,
                    "Invalid Path",
                    "The selected Ashita path does not exist or is not a directory."
                )
                return
            
            addons_dir = os.path.join(ashita_path, 'addons')
            plugins_dir = os.path.join(ashita_path, 'plugins')
            
            if not (os.path.isdir(addons_dir) or os.path.isdir(plugins_dir)):
                reply = QMessageBox.question(
                    self,
                    "Confirm Path",
                    f"The selected folder doesn't contain 'addons' or 'plugins' folders.\n"
                    f"Are you sure this is your Ashita installation folder?\n\n"
                    f"Path: {ashita_path}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    return
            
            self.package_tracker.set_setting('ashita_path', ashita_path)
            path_changed = True
        
        token = self.token_input.text().strip()
        
        if token and not (token.startswith('ghp_') or token.startswith('github_pat_')):
            QMessageBox.warning(
                self,
                "Invalid Token",
                "GitHub tokens typically start with 'ghp_' or 'github_pat_'.\n"
                "Are you sure this is correct?"
            )
            return
        
        self.package_tracker.set_setting('github_token', token)
        
        msg = "Settings have been saved successfully.\n"
        if path_changed:
            msg += "Please restart the application for the path change to take effect."
        else:
            msg += "The new token will be used for all GitHub API requests."
        
        QMessageBox.information(
            self,
            "Settings saved",
            msg
        )
        
        self.accept()


class AshitaManagerUI(QMainWindow):
    def __init__(self):
        """Initialize Ashita Package Manager application."""
        super().__init__()
        
        # base directory based on whether frozen (bundled) or not
        if getattr(sys, 'frozen', False):
            resource_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
            base_dir = os.path.dirname(sys.executable)
        else:
            resource_dir = os.path.dirname(os.path.abspath(__file__))
            base_dir = resource_dir

        # Set application icon
        try:
            icon_path = os.path.join(resource_dir, 'assets', 'logo.png')
            if os.path.exists(icon_path):
                QApplication.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

        self.package_tracker = PackageTracker(base_dir)

        ashita_path = self.package_tracker.get_setting('ashita_path', '')

        self.init_ui()

        try:
            screen = QApplication.primaryScreen()
            if screen:
                scr = screen.availableGeometry()
                x = scr.x() + (scr.width() - 900) // 2
                y = scr.y() + (scr.height() - 700) // 2
                self.setGeometry(max(x, 0), max(y, 0), 900, 700)
        except Exception:
            self.setGeometry(100, 100, 900, 700)

        try:
            self.show()
            QApplication.processEvents()
        except Exception:
            pass

        if not ashita_path:
            ashita_path = self._prompt_for_ashita_path()
            if not ashita_path:
                QMessageBox.critical(None, "Error", "Ashita path is required to continue.")
                sys.exit(1)
            self.package_tracker.set_setting('ashita_path', ashita_path)
        
        self.ashita_root = ashita_path
        self.package_manager = PackageManager(self.ashita_root, self.package_tracker)
        self._centered = False
        self._first_launch = self.package_tracker.is_first_launch()
        self._last_update_params = None

        # Script manager
        self.current_script = None
        self.current_script_path = None

        try:
            ashita_display = str(self.ashita_root)
            branch_display = getattr(self.package_manager, 'official_repo_branch', 'main')
            self.statusBar().showMessage(f"Ashita: {ashita_display} | Branch: {branch_display}")
        except Exception:
            pass

        # Center the window before showing initial scan dialog
        try:
            screen = QApplication.primaryScreen()
            if screen:
                scr = screen.availableGeometry()
                x = scr.x() + (scr.width() - self.width()) // 2
                y = scr.y() + (scr.height() - self.height()) // 2
                self.move(max(x, 0), max(y, 0))
            self._centered = True
        except Exception:
            pass

        if self._first_launch:
            self.perform_initial_scan()

        self.refresh_package_lists()
        self.refresh_script_list()
    
    def _prompt_for_ashita_path(self):
        """Prompt user to select Ashita installation path.
        
        Returns:
            str - Selected path or None if cancelled
        """
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("Welcome to Ashita Package Manager!")
        msg.setInformativeText("Please select your Ashita installation folder.")
        msg.setWindowTitle("First time setup")
        msg.setMinimumSize(420, 140)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        msg.show()
        self._center_widget(msg)

        if msg.exec() == QMessageBox.StandardButton.Cancel:
            return None
        
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Ashita Installation folder",
            os.path.expanduser("~")
        )
        
        if folder:
            addons_dir = os.path.join(folder, 'addons')
            plugins_dir = os.path.join(folder, 'plugins')
            
            if not (os.path.isdir(addons_dir) or os.path.isdir(plugins_dir)):
                reply = QMessageBox.question(
                    self,
                    "Confirm path",
                    f"The selected folder doesn't contain 'addons' or 'plugins' folders.\n"
                    f"Are you sure this is your Ashita installation folder?\n\n"
                    f"Path: {folder}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    return self._prompt_for_ashita_path()
        
        return folder
    
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Ashita Package Manager")

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Installation section
        install_group = QGroupBox("Install new package")
        install_layout = QVBoxLayout()

        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("Git URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://github.com/username/repo")
        url_layout.addWidget(self.url_input)

        url_layout.addSpacing(10)

        # Package type selector
        type_label = QLabel("Type:")
        url_layout.addWidget(type_label)
        self.type_selector = QComboBox()
        self.type_selector.addItems(["Auto", "Addon", "Plugin"])
        url_layout.addWidget(self.type_selector)

        url_layout.addSpacing(10)

        # Installation method selector
        method_label = QLabel("Method:")
        url_layout.addWidget(method_label)
        self.method_selector = QComboBox()
        self.method_selector.addItems(["Clone", "Release"])
        url_layout.addWidget(self.method_selector)

        url_layout.addSpacing(10)

        # Install button
        self.install_btn = QPushButton("Install")
        self.install_btn.clicked.connect(self.install_package)
        icon = self._std_icon('drive_file_move')
        if not icon.isNull():
            self.install_btn.setIcon(icon)
        url_layout.addWidget(self.install_btn)

        self.manual_install_btn = QPushButton("Manual install...")
        icon = self._std_icon('add')
        if not icon.isNull():
            self.manual_install_btn.setIcon(icon)
        self.manual_install_btn.clicked.connect(self.open_manual_install_dialog)
        url_layout.addWidget(self.manual_install_btn)

        install_layout.addLayout(url_layout)
        install_group.setLayout(install_layout)
        main_layout.addWidget(install_group)

        # Package lists
        self.tabs = QTabWidget()

        # Addons tab
        addons_widget = QWidget()
        addons_layout = QVBoxLayout(addons_widget)

        self.addons_search = QLineEdit()
        self.addons_search.setPlaceholderText("Search addons...")
        self.addons_search.textChanged.connect(lambda: self.filter_packages("addon"))
        addons_layout.addWidget(self.addons_search)

        self.addons_list = QTreeWidget()
        self.addons_list.setHeaderHidden(True)
        self.addons_list.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.addons_list.itemClicked.connect(self.show_package_info)
        addons_layout.addWidget(self.addons_list)

        # Addon buttons
        addon_buttons = QHBoxLayout()
        self.update_addon_btn = QPushButton("Update")
        icon = self._std_icon('save')
        if not icon.isNull():
            self.update_addon_btn.setIcon(icon)
        self.update_addon_btn.clicked.connect(lambda: self.update_package("addon"))
        addon_buttons.addWidget(self.update_addon_btn)

        self.update_all_addons_btn = QPushButton("Update all")
        icon = self._std_icon('yes_all')
        if not icon.isNull():
            self.update_all_addons_btn.setIcon(icon)
        self.update_all_addons_btn.clicked.connect(lambda: self.batch_update("addon"))
        addon_buttons.addWidget(self.update_all_addons_btn)

        self.remove_addon_btn = QPushButton("Remove")
        icon = self._std_icon('remove')
        if not icon.isNull():
            self.remove_addon_btn.setIcon(icon)
        self.remove_addon_btn.clicked.connect(lambda: self.remove_package("addon"))
        addon_buttons.addWidget(self.remove_addon_btn)

        self.refresh_addon_btn = QPushButton("Refresh list")
        icon = self._std_icon('refresh')
        if not icon.isNull():
            self.refresh_addon_btn.setIcon(icon)
        self.refresh_addon_btn.clicked.connect(self.refresh_package_lists)
        addon_buttons.addWidget(self.refresh_addon_btn)

        self.open_addon_repo_btn = QPushButton("Open repository")
        icon = self._std_icon('install')
        if not icon.isNull():
            self.open_addon_repo_btn.setIcon(icon)
        self.open_addon_repo_btn.clicked.connect(lambda: self.open_repository("addon"))
        addon_buttons.addWidget(self.open_addon_repo_btn)

        self.open_addon_readme_btn = QPushButton("Open README")
        icon = self._std_icon('help')
        if not icon.isNull():
            self.open_addon_readme_btn.setIcon(icon)
        self.open_addon_readme_btn.clicked.connect(lambda: self.open_readme("addon"))
        addon_buttons.addWidget(self.open_addon_readme_btn)

        addons_layout.addLayout(addon_buttons)
        self.tabs.addTab(addons_widget, "Addons (0)")

        # Plugins tab
        plugins_widget = QWidget()
        plugins_layout = QVBoxLayout(plugins_widget)

        self.plugins_search = QLineEdit()
        self.plugins_search.setPlaceholderText("Search plugins...")
        self.plugins_search.textChanged.connect(lambda: self.filter_packages("plugin"))
        plugins_layout.addWidget(self.plugins_search)

        self.plugins_list = QTreeWidget()
        self.plugins_list.setHeaderHidden(True)
        self.plugins_list.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.plugins_list.itemClicked.connect(self.show_package_info)
        plugins_layout.addWidget(self.plugins_list)

        # Plugin buttons
        plugin_buttons = QHBoxLayout()
        self.update_plugin_btn = QPushButton("Update")
        icon = self._std_icon('save')
        if not icon.isNull():
            self.update_plugin_btn.setIcon(icon)
        self.update_plugin_btn.clicked.connect(lambda: self.update_package("plugin"))
        plugin_buttons.addWidget(self.update_plugin_btn)

        self.update_all_plugins_btn = QPushButton("Update all")
        icon = self._std_icon('yes_all')
        if not icon.isNull():
            self.update_all_plugins_btn.setIcon(icon)
        self.update_all_plugins_btn.clicked.connect(lambda: self.batch_update("plugin"))
        plugin_buttons.addWidget(self.update_all_plugins_btn)

        self.remove_plugin_btn = QPushButton("Remove")
        icon = self._std_icon('remove')
        if not icon.isNull():
            self.remove_plugin_btn.setIcon(icon)
        self.remove_plugin_btn.clicked.connect(lambda: self.remove_package("plugin"))
        plugin_buttons.addWidget(self.remove_plugin_btn)

        self.refresh_plugin_btn = QPushButton("Refresh list")
        icon = self._std_icon('refresh')
        if not icon.isNull():
            self.refresh_plugin_btn.setIcon(icon)
        self.refresh_plugin_btn.clicked.connect(self.refresh_package_lists)
        plugin_buttons.addWidget(self.refresh_plugin_btn)

        self.open_plugin_repo_btn = QPushButton("Open repository")
        icon = self._std_icon('install')
        if not icon.isNull():
            self.open_plugin_repo_btn.setIcon(icon)
        self.open_plugin_repo_btn.clicked.connect(lambda: self.open_repository("plugin"))
        plugin_buttons.addWidget(self.open_plugin_repo_btn)

        self.open_plugin_readme_btn = QPushButton("Open README")
        icon = self._std_icon('help')
        if not icon.isNull():
            self.open_plugin_readme_btn.setIcon(icon)
        self.open_plugin_readme_btn.clicked.connect(lambda: self.open_readme("plugin"))
        plugin_buttons.addWidget(self.open_plugin_readme_btn)

        plugins_layout.addLayout(plugin_buttons)
        self.tabs.addTab(plugins_widget, "Plugins (0)")

        # Script Manager tab
        script_widget = QWidget()
        script_main_layout = QVBoxLayout(script_widget)

        # Script selector
        script_selector_layout = QHBoxLayout()
        script_selector_layout.addWidget(QLabel("Script file:"))
        self.script_selector = QComboBox()
        self.script_selector.currentTextChanged.connect(self.load_selected_script)
        script_selector_layout.addWidget(self.script_selector)

        refresh_scripts_btn = QPushButton("Refresh")
        icon = self._std_icon('refresh')
        if not icon.isNull():
            refresh_scripts_btn.setIcon(icon)
        refresh_scripts_btn.clicked.connect(self.refresh_script_list)
        script_selector_layout.addWidget(refresh_scripts_btn)
        script_selector_layout.addStretch()

        script_main_layout.addLayout(script_selector_layout)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        script_content = QWidget()
        script_layout = QVBoxLayout(script_content)

        # Plugins section - horizontal layout with two columns
        plugins_section = QHBoxLayout()

        # Left: Plugins in Script
        self.script_plugins_group = QGroupBox("Plugins in script")
        plugins_group_layout = QVBoxLayout()

        # Search bar for active plugins
        self.script_plugins_search = QLineEdit()
        self.script_plugins_search.setPlaceholderText("Search plugins in script...")
        self.script_plugins_search.textChanged.connect(lambda: self.filter_script_list('plugin'))
        plugins_group_layout.addWidget(self.script_plugins_search)

        self.script_plugins_list = QTreeWidget()
        self.script_plugins_list.setHeaderHidden(True)
        self.script_plugins_list.setRootIsDecorated(False)  # Remove indentation for flat list
        self.script_plugins_list.setMinimumHeight(200)
        plugins_group_layout.addWidget(self.script_plugins_list)

        # Connect itemChanged signal for plugins
        self.script_plugins_list.itemChanged.connect(lambda item, column: self.on_script_item_changed(item, column, 'plugin'))

        plugins_buttons = QHBoxLayout()
        self.move_plugin_up_btn = QPushButton("Move up")
        self.move_plugin_up_btn.clicked.connect(lambda: self.move_script_item('plugin', -1))
        icon = self._std_icon('up')
        if not icon.isNull():
            self.move_plugin_up_btn.setIcon(icon)
        plugins_buttons.addWidget(self.move_plugin_up_btn)

        self.move_plugin_down_btn = QPushButton("Move down")
        self.move_plugin_down_btn.clicked.connect(lambda: self.move_script_item('plugin', 1))
        icon = self._std_icon('down')
        if not icon.isNull():
            self.move_plugin_down_btn.setIcon(icon)
        plugins_buttons.addWidget(self.move_plugin_down_btn)

        self.remove_plugin_from_script_btn = QPushButton("Remove")
        self.remove_plugin_from_script_btn.clicked.connect(lambda: self.remove_from_script('plugin'))
        icon = self._std_icon('remove')
        if not icon.isNull():
            self.remove_plugin_from_script_btn.setIcon(icon)
        plugins_buttons.addWidget(self.remove_plugin_from_script_btn)

        plugins_buttons.addStretch()
        plugins_group_layout.addLayout(plugins_buttons)
        self.script_plugins_group.setLayout(plugins_group_layout)
        plugins_section.addWidget(self.script_plugins_group)

        # Right: Available Plugins (not in script)
        self.available_plugins_group = QGroupBox("Available Plugins (0)")
        available_plugins_layout = QVBoxLayout()

        # Search bar for available plugins
        self.available_plugins_search = QLineEdit()
        self.available_plugins_search.setPlaceholderText("Search available plugins...")
        self.available_plugins_search.textChanged.connect(lambda: self.filter_available_list('plugin'))
        available_plugins_layout.addWidget(self.available_plugins_search)

        self.available_plugins_list = QTreeWidget()
        self.available_plugins_list.setHeaderHidden(True)
        self.available_plugins_list.setMinimumHeight(200)
        available_plugins_layout.addWidget(self.available_plugins_list)

        add_plugin_btn = QPushButton("Add to script")
        add_plugin_btn.clicked.connect(lambda: self.add_to_script('plugin'))
        icon = self._std_icon('add')
        if not icon.isNull():
            add_plugin_btn.setIcon(icon)
        available_plugins_layout.addWidget(add_plugin_btn)

        self.available_plugins_group.setLayout(available_plugins_layout)
        plugins_section.addWidget(self.available_plugins_group)

        script_layout.addLayout(plugins_section)

        # Addons section - horizontal layout with two columns
        addons_section = QHBoxLayout()

        # Left: Addons in Script
        self.script_addons_group = QGroupBox("Addons in script")
        addons_group_layout = QVBoxLayout()

        # Search bar for active addons
        self.script_addons_search = QLineEdit()
        self.script_addons_search.setPlaceholderText("Search addons in script...")
        self.script_addons_search.textChanged.connect(lambda: self.filter_script_list('addon'))
        addons_group_layout.addWidget(self.script_addons_search)

        self.script_addons_list = QTreeWidget()
        self.script_addons_list.setHeaderHidden(True)
        self.script_addons_list.setRootIsDecorated(False)  # Remove indentation for flat list
        self.script_addons_list.setMinimumHeight(200)
        addons_group_layout.addWidget(self.script_addons_list)

        # Connect itemChanged signal for addons
        self.script_addons_list.itemChanged.connect(lambda item, column: self.on_script_item_changed(item, column, 'addon'))

        addons_buttons = QHBoxLayout()
        self.move_addon_up_btn = QPushButton("Move up")
        self.move_addon_up_btn.clicked.connect(lambda: self.move_script_item('addon', -1))
        icon = self._std_icon('up')
        if not icon.isNull():
            self.move_addon_up_btn.setIcon(icon)
        addons_buttons.addWidget(self.move_addon_up_btn)

        self.move_addon_down_btn = QPushButton("Move Down")
        self.move_addon_down_btn.clicked.connect(lambda: self.move_script_item('addon', 1))
        icon = self._std_icon('down')
        if not icon.isNull():
            self.move_addon_down_btn.setIcon(icon)
        addons_buttons.addWidget(self.move_addon_down_btn)

        self.remove_addon_from_script_btn = QPushButton("Remove")
        self.remove_addon_from_script_btn.clicked.connect(lambda: self.remove_from_script('addon'))
        icon = self._std_icon('remove')
        if not icon.isNull():
            self.remove_addon_from_script_btn.setIcon(icon)
        addons_buttons.addWidget(self.remove_addon_from_script_btn)

        addons_buttons.addStretch()
        addons_group_layout.addLayout(addons_buttons)
        self.script_addons_group.setLayout(addons_group_layout)
        addons_section.addWidget(self.script_addons_group)

        # Right: Available Addons (not in script)
        self.available_addons_group = QGroupBox("Available Addons (0)")
        available_addons_layout = QVBoxLayout()

        # Search bar for available addons
        self.available_addons_search = QLineEdit()
        self.available_addons_search.setPlaceholderText("Search available addons...")
        self.available_addons_search.textChanged.connect(lambda: self.filter_available_list('addon'))
        available_addons_layout.addWidget(self.available_addons_search)

        self.available_addons_list = QTreeWidget()
        self.available_addons_list.setHeaderHidden(True)
        self.available_addons_list.setMinimumHeight(200)
        available_addons_layout.addWidget(self.available_addons_list)

        add_addon_btn = QPushButton("Add to script")
        add_addon_btn.clicked.connect(lambda: self.add_to_script('addon'))
        icon = self._std_icon('add')
        if not icon.isNull():
            add_addon_btn.setIcon(icon)
        available_addons_layout.addWidget(add_addon_btn)

        self.available_addons_group.setLayout(available_addons_layout)
        addons_section.addWidget(self.available_addons_group)

        script_layout.addLayout(addons_section)

        # Exec section
        self.script_exec_group = QGroupBox("Execute scripts (Keybinds/Aliases)")
        exec_group_layout = QVBoxLayout()
        self.script_exec_list = QListWidget()
        self.script_exec_list.setMaximumHeight(120)
        exec_group_layout.addWidget(self.script_exec_list)

        # Connect item change to update enabled state
        self.script_exec_list.itemChanged.connect(lambda item: self.on_script_item_changed(item, None, 'exec'))

        exec_buttons = QHBoxLayout()
        self.add_exec_btn = QPushButton("Add Keybind/Alias")
        self.add_exec_btn.clicked.connect(self.add_exec_command)
        icon = self._std_icon('add')
        if not icon.isNull():
            self.add_exec_btn.setIcon(icon)
        exec_buttons.addWidget(self.add_exec_btn)

        self.remove_exec_btn = QPushButton("Remove")
        self.remove_exec_btn.clicked.connect(self.remove_exec_command)
        icon = self._std_icon('remove')
        if not icon.isNull():
            self.remove_exec_btn.setIcon(icon)
        exec_buttons.addWidget(self.remove_exec_btn)

        exec_buttons.addStretch()
        exec_group_layout.addLayout(exec_buttons)
        self.script_exec_group.setLayout(exec_group_layout)
        script_layout.addWidget(self.script_exec_group)

        # Wait time section
        wait_group = QGroupBox("Wait time (seconds)")
        wait_layout = QHBoxLayout()
        wait_layout.addWidget(QLabel("Wait before configuration commands:"))
        self.wait_time_spin = QSpinBox()
        self.wait_time_spin.setMinimum(3)
        self.wait_time_spin.setMaximum(60)
        self.wait_time_spin.setValue(3)
        wait_layout.addWidget(self.wait_time_spin)
        wait_layout.addStretch()
        wait_group.setLayout(wait_layout)
        script_layout.addWidget(wait_group)

        # Config commands section
        self.script_config_group = QGroupBox("Configuration commands")
        config_group_layout = QVBoxLayout()
        self.script_config_list = QListWidget()
        self.script_config_list.setMaximumHeight(150)
        config_group_layout.addWidget(self.script_config_list)

        # Connect item change to update enabled state
        self.script_config_list.itemChanged.connect(lambda item: self.on_script_item_changed(item, None, 'config'))

        config_buttons = QHBoxLayout()
        self.add_config_btn = QPushButton("Add Command")
        self.add_config_btn.clicked.connect(self.add_config_command)
        icon = self._std_icon('add')
        if not icon.isNull():
            self.add_config_btn.setIcon(icon)
        config_buttons.addWidget(self.add_config_btn)

        self.remove_config_btn = QPushButton("Remove")
        self.remove_config_btn.clicked.connect(lambda: self.remove_config_command)
        icon = self._std_icon('remove')
        if not icon.isNull():
            self.remove_config_btn.setIcon(icon)
        config_buttons.addWidget(self.remove_config_btn)

        config_buttons.addStretch()
        config_group_layout.addLayout(config_buttons)
        self.script_config_group.setLayout(config_group_layout)
        script_layout.addWidget(self.script_config_group)

        script_layout.addStretch()
        scroll.setWidget(script_content)
        script_main_layout.addWidget(scroll)

        # Save button
        save_script_layout = QHBoxLayout()
        save_script_layout.addStretch()
        self.save_script_btn = QPushButton("Save Script")
        self.save_script_btn.clicked.connect(self.save_current_script)
        icon = self._std_icon('save')
        if not icon.isNull():
            self.save_script_btn.setIcon(icon)
        save_script_layout.addWidget(self.save_script_btn)
        script_main_layout.addLayout(save_script_layout)

        self.tabs.addTab(script_widget, "Scripts")

        main_layout.addWidget(self.tabs)

        # Connect tab change to hide/show info panel
        self.tabs.currentChanged.connect(self.on_tab_changed)

        # Info panel
        self.info_group = QGroupBox("Package information")
        info_layout = QVBoxLayout()
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setMaximumHeight(150)
        info_layout.addWidget(self.info_text)
        self.info_group.setLayout(info_layout)
        main_layout.addWidget(self.info_group)

        # Log window
        log_group = QGroupBox("Activity log")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        log_layout.addWidget(self.log_text)

        # Log controls
        log_controls = QHBoxLayout()
        self.clear_log_btn = QPushButton("Clear log")
        self.clear_log_btn.clicked.connect(lambda: self.log_text.clear())
        icon = self._std_icon('remove')
        if not icon.isNull():
            self.clear_log_btn.setIcon(icon)
        log_controls.addWidget(self.clear_log_btn)

        # Settings button
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        icon = self._std_icon('settings')
        if not icon.isNull():
            self.settings_btn.setIcon(icon)
        log_controls.addWidget(self.settings_btn)

        log_controls.addStretch()
        log_layout.addLayout(log_controls)

        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # Initial log message
        self.log("Ashita Package Manager started")

        # Footer: Ashita folder and branch
        if hasattr(self, 'ashita_root') and self.ashita_root:
            try:
                ashita_display = str(self.ashita_root)
                branch_display = getattr(self.package_manager, 'official_repo_branch', 'main')
                self.statusBar().showMessage(f"Ashita: {ashita_display} | Branch: {branch_display}")
            except Exception:
                pass
        else:
            self.statusBar().showMessage('Waiting for initialization...')

    def showEvent(self, event):
        """Handle window show event - center window on screen.
        
        Args:
            event: QShowEvent - Show event object
        """
        super().showEvent(event)
        try:
            if not getattr(self, '_centered', False):
                screen = QApplication.primaryScreen()
                if screen:
                    scr = screen.availableGeometry()
                    x = scr.x() + (scr.width() - self.width()) // 2
                    y = scr.y() + (scr.height() - self.height()) // 2
                    self.move(max(x, 0), max(y, 0))
                self._centered = True
        except Exception:
            pass

    def _center_widget(self, widget, parent=None):
        """Center a widget on screen or within parent.
        
        Args:
            widget: QWidget - Widget to center
            parent: Optional QWidget - Parent widget for relative positioning
        """
        try:
            parent = parent or self
            if parent and getattr(parent, 'isVisible', lambda: False)():
                parent_geom = parent.geometry()
                x = parent_geom.x() + (parent_geom.width() - widget.width()) // 2
                y = parent_geom.y() + (parent_geom.height() - widget.height()) // 2
            else:
                screen = QGuiApplication.primaryScreen()
                if screen:
                    scr = screen.availableGeometry()
                    x = scr.x() + (scr.width() - widget.width()) // 2
                    y = scr.y() + (scr.height() - widget.height()) // 2
                else:
                    x, y = 0, 0
            widget.move(max(x, 0), max(y, 0))
        except Exception:
            pass

    def _show_centered_message(self, icon, title, text, informative=None, buttons=QMessageBox.StandardButton.Ok):
        """Show centered message dialog.
        
        Args:
            icon: QMessageBox.Icon - Message box icon type
            title: str - Dialog title
            text: str - Main message text
            informative: Optional str - Informative text
            buttons: QMessageBox buttons - Buttons to show
        
        Returns:
            int - Button clicked
        """
        try:
            msg = QMessageBox(self)
            msg.setIcon(icon)
            msg.setWindowTitle(title)
            msg.setText(text)
            if informative:
                msg.setInformativeText(informative)
            msg.setStandardButtons(buttons)
            msg.setMinimumSize(420, 140)
            msg.show()
            self._center_widget(msg)
            return msg.exec()
        except Exception:
            # Fallback to static method if anything goes wrong
            if icon == QMessageBox.Icon.Warning:
                return QMessageBox.warning(self, title, text)
            elif icon == QMessageBox.Icon.Critical:
                return QMessageBox.critical(self, title, text)
            else:
                return QMessageBox.information(self, title, text)
    
    def log(self, message):
        """Add a message to the log."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")

        self.log_text.append(f"[{timestamp}] {message}")



    def _create_progress(self, label, cancel_text, minimum, maximum):
        """Create and show progress dialog.
        
        Args:
            label: str - Progress label text
            cancel_text: str - Cancel button text
            minimum: int - Minimum progress value
            maximum: int - Maximum progress value
        
        Returns:
            QProgressDialog - Progress dialog widget
        """
        dlg = QProgressDialog(label, cancel_text, minimum, maximum, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setWindowTitle(label)
        width = min(max(450, int(self.width() * 0.85)), 1000)
        dlg.setFixedWidth(width)
        dlg.setMinimumDuration(0)
        dlg.show()

        # Center the progress dialog over the main window (or screen if not visible)
        self._center_widget(dlg)
        return dlg

    def _std_icon(self, key):
        """Get standard icon by key name.
        
        Args:
            key: str - Icon key ('info', 'warning', 'error', 'question')
        
        Returns:
            QIcon - Standard icon or default if key not found
        """
        mapping = {
            'install': 'SP_DialogOpenButton',
            'add': 'SP_FileDialogNewFolder',
            'remove': 'SP_TrashIcon',
            'up': 'SP_ArrowUp',
            'down': 'SP_ArrowDown',
            'save': 'SP_DialogSaveButton',
            'refresh': 'SP_BrowserReload',
            'settings': 'SP_FileDialogDetailedView',
            'clear': 'SP_DialogResetButton',
            'ok': 'SP_DialogApplyButton',
            'error': 'SP_MessageBoxCritical',
            'floppy': 'SP_DriveFDIcon',
            'hdd': 'SP_DriveHDIcon',
            'network': 'SP_DriveNetIcon',
            'arrow_upward': 'SP_FileDialogToParent',
            'drive_file_move': 'SP_FileDialogStart',
            'refresh': 'SP_DialogRetryButton',
            'save': 'SP_DialogSaveButton',
            'yes_all': 'SP_DialogYesToAllButton',
            'help': 'SP_DialogHelpButton',
        }
        enum_name = mapping.get(key)
        if not enum_name:
            return QIcon()
        sp = getattr(QStyle.StandardPixmap, enum_name, None)
        if sp is None:
            return QIcon()
        return QApplication.style().standardIcon(sp)
    
    def perform_initial_scan(self):
        """Start initial package scan on first application launch."""
        self.log("First launch detected")
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("First launch detected")
        msg.setInformativeText("Would you like to scan for existing addons and plugins?")
        msg.setWindowTitle("Initial scan")
        msg.setMinimumSize(420, 140)
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.show()
        self._center_widget(msg)

        if msg.exec() == QMessageBox.StandardButton.Yes:
            self.log("Starting initial scan...")
            self.scan_progress = self._create_progress("Scanning for existing packages...", None, 0, 0)
            
            self.scan_worker = ScanWorker(self.package_manager)
            self.scan_worker.progress.connect(self.scan_progress.setLabelText)
            self.scan_worker.progress.connect(self.log)
            self.scan_worker.finished.connect(self.scan_finished)
            self.scan_worker.start()
    
    def scan_finished(self, results):
        """Handle completion of package scan.
        
        Args:
            results: dict - Scan results with addon/plugin counts
        """
        self.scan_progress.close()

        official_lookup = results.get('official_lookup')
        if official_lookup:
            if official_lookup.get('success', False):
                self.log("Official catalog lookup succeeded")
            else:
                if official_lookup.get('rate_limited'):
                    error_msg = official_lookup.get('error') or 'GitHub API rate limit exceeded'
                    self.log(f"Official catalog lookup failed: {error_msg}")
                    self._show_centered_message(QMessageBox.Icon.Warning, "Rate limit exceeded", 
                        f"GitHub API rate limit exceeded.\n\nPlease wait before retrying or configure a GitHub token in Settings for higher limits.")
                else:
                    error_msg = official_lookup.get('error') or 'Unknown error'
                    self.log(f"Official catalog lookup failed: {error_msg}")

        for flag in results.get('release_flags', []):
            self.log(flag)
        
        if 'error' in results:
            self.log(f"Scan failed: {results['error']}")
            self._show_centered_message(QMessageBox.Icon.Warning, "Scan failed", f"Failed to scan packages:\n{results['error']}")
        else:
            self.log(f"Scan complete: {results['addons']} addons, {results['plugins']} plugins found")
            info_msg = f"Scan complete!\n\nAddons found: {results['addons']}\nPlugins found: {results['plugins']}"
            self._show_centered_message(QMessageBox.Icon.Information, "Scan complete", info_msg)
            self.refresh_package_lists()
    
    def install_package(self):
        """Install package from URL input.
        
        Validates URL and starts installation worker thread.
        """
        url = self.url_input.text().strip()
        if not url:
            self._show_centered_message(QMessageBox.Icon.Warning, "Error", "Please enter a Git URL")
            return
        
        pkg_type_text = self.type_selector.currentText()
        install_method = self.method_selector.currentText()
        
        if pkg_type_text == "Auto":
            self.log(f"Auto-detecting package type for {url}...")
            progress = self._create_progress("Auto-detecting package type...", None, 0, 0)
            QApplication.processEvents()

            detected_type = None
            if install_method == 'Release':
                detected_type = self.package_manager.detect_package_type_from_release(url)
            if not detected_type:
                detected_type = self.package_manager.detect_package_type(url)
            
            progress.close()
            
            if not detected_type:
                self.log("Auto-detection failed")
                self._show_centered_message(QMessageBox.Icon.Warning, "Auto-detect failed", 
                    "Could not auto-detect package type. Please select Type manually.")
                return
            
            pkg_type = detected_type
            self.log(f"Detected as {pkg_type}")
        else:
            pkg_type = pkg_type_text.lower()
        
        self.log(f"Installing {pkg_type} from {url} using {install_method}...")
        
        self.progress = self._create_progress("Installing package...", "Cancel", 0, 0)

        # Store parameters for potential conflict retry
        self._last_install_params = {
            'url': url,
            'pkg_type': pkg_type,
            'install_method': install_method,
            'branch': None,
            'release_asset_url': None,
            'release_asset_name': None
        }
        # If cloning, attempt to list remote branches and prompt the user if there are multiple
        branch = None
        if install_method == 'Clone':
            try:
                branches = self.package_manager.list_remote_branches(url)
            except Exception:
                branches = None

            if branches and len(branches) > 1:
                # prompt the user which branch to install, defaults to main or master if available
                default_index = 0
                if 'main' in branches:
                    default_index = branches.index('main')
                elif 'master' in branches:
                    default_index = branches.index('master')
                
                branch_choice, ok = QInputDialog.getItem(self, "Select branch", "Select branch to install:", branches, default_index, False)
                if not ok:
                    self.progress.close()
                    return
                branch = branch_choice
            
            # Update stored branch parameter
            self._last_install_params['branch'] = branch

        self.worker = InstallWorker(
            self.package_manager,
            url,
            pkg_type,
            install_method,
            branch=branch,
            plugin_variant=None,
            release_asset_url=self._last_install_params.get('release_asset_url'),
            release_asset_name=self._last_install_params.get('release_asset_name'),
            selected_entrypoint=None
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.progress.connect(self.log)
        self.worker.finished.connect(self.install_finished)
        self.worker.conflict_detected.connect(self.handle_install_conflict)
        self.worker.variant_selection_requested.connect(self.handle_variant_selection)
        self.worker.entrypoint_selection_requested.connect(self.handle_entrypoint_selection)
        self.worker.start()
    
    def update_progress(self, message):
        """Update progress dialog label text.
        
        Args:
            message: str - Progress message
        """
        self.progress.setLabelText(message)
        QApplication.processEvents()
    
    def install_finished(self, success, message):
        """Handle installation completion.
        
        Args:
            success: bool - Whether installation succeeded
            message: str - Result message
        """
        self.progress.close()
        
        if success:
            self.log(message)
            self._show_centered_message(QMessageBox.Icon.Information, "Success", message)
            self.url_input.clear()
            self.refresh_package_lists()
        else:
            self.log(f"Installation failed: {message}")
            self._show_centered_message(QMessageBox.Icon.Critical, "Error", f"Installation failed:\n{message}")

    def open_manual_install_dialog(self):
        """Open manual package installation dialog."""
        dialog = ManualPackageDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.get_payload()
        if not payload:
            return
        pkg_label = 'addon' if payload.get('pkg_type') == 'addon' else 'plugin'
        self.log(f"Starting manual install for {pkg_label}...")
        self.progress = self._create_progress("Installing package...", None, 0, 0)
        self.manual_install_worker = ManualInstallWorker(self.package_manager, payload)
        self.manual_install_worker.progress.connect(self.update_progress)
        self.manual_install_worker.progress.connect(self.log)
        self.manual_install_worker.finished.connect(self.manual_install_finished)
        self.manual_install_worker.start()

    def manual_install_finished(self, success, message):
        """Handle completion of manual installation.
        
        Args:
            success: bool - Whether installation succeeded
            message: str - Result message
        """
        try:
            self.progress.close()
        except Exception:
            pass
        if success:
            self.log(message)
            self._show_centered_message(QMessageBox.Icon.Information, "Manual install", message)
            self.refresh_package_lists()
        else:
            self.log(f"Manual install failed: {message}")
            self._show_centered_message(QMessageBox.Icon.Warning, "Manual install failed", message)
    
    def handle_install_conflict(self, result):
        """Handle file conflict detection during installation.
        
        Args:
            result: dict - Installation result with conflicts info
        """
        self.progress.close()
        
        is_monorepo = result.get('monorepo', False)
        conflicts = result.get('conflicts', {})
        
        # Build conflict message
        if is_monorepo:
            # Handle monorepo conflicts
            conflict_msg = "File conflicts detected in monorepo addons:\n\n"
            
            for addon_name, addon_conflicts in conflicts.items():
                lib_conflicts = addon_conflicts.get('libs', [])
                docs_conflict = addon_conflicts.get('docs', False)
                resources_conflict = addon_conflicts.get('resources', False)
                
                conflict_msg += f"Addon: {addon_name}\n"
                
                if lib_conflicts:
                    conflict_msg += "  Library Files:\n"
                    for conflict in lib_conflicts:
                        owner = conflict.get('owner', 'Unknown')
                        owner_source = conflict.get('owner_source', 'Unknown')
                        file_path = conflict.get('file', 'Unknown')
                        conflict_msg += f"    • {file_path}\n      (owned by '{owner}' from {owner_source})\n"
                
                if docs_conflict:
                    conflict_msg += "  • Documentation folder already exists\n"
                
                if resources_conflict:
                    conflict_msg += "  • Resources folder already exists\n"
                
                conflict_msg += "\n"
        else:
            # Handle single addon conflicts
            lib_conflicts = conflicts.get('libs', [])
            docs_conflict = conflicts.get('docs', False)
            resources_conflict = conflicts.get('resources', False)
            
            conflict_msg = "File conflicts detected during installation:\n\n"
            
            if lib_conflicts:
                conflict_msg += "Library Files:\n"
                for conflict in lib_conflicts:
                    owner = conflict.get('owner', 'Unknown')
                    owner_source = conflict.get('owner_source', 'Unknown')
                    file_path = conflict.get('file', 'Unknown')
                    conflict_msg += f"  • {file_path}\n    (owned by '{owner}' from {owner_source})\n"
                conflict_msg += "\n"
            
            if docs_conflict:
                conflict_msg += "• Documentation folder already exists\n\n"
            
            if resources_conflict:
                conflict_msg += "• Resources folder already exists\n\n"
        
        # Create custom dialog with scrollable text area
        dialog = QDialog(self)
        dialog.setWindowTitle("File Conflicts Detected")
        dialog.setMinimumWidth(600)
        dialog.setMaximumHeight(500)
        
        layout = QVBoxLayout(dialog)
        
        # Scrollable text area for conflict details
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(conflict_msg)
        layout.addWidget(text_edit)
        
        # Question label
        question_label = QLabel("Do you want to overwrite these files and continue installation?")
        question_label.setWordWrap(True)
        layout.addWidget(question_label)
        
        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        
        # Show dialog and handle response
        reply = dialog.exec()
        
        if reply == QDialog.DialogCode.Accepted:
            # Retry installation with force=True
            self.log("Retrying installation with conflict override...")
            self._retry_install_with_force()
        else:
            self.log("Installation cancelled by user")
    
    def _retry_install_with_force(self):
        """Retry failed installation by overriding conflict checks.
        
        Uses cached result to restart installation with force flag.
        """
        if not hasattr(self, '_last_install_params'):
            self._show_centered_message(QMessageBox.Icon.Critical, "Error", "Installation parameters not found")
            return
        
        params = self._last_install_params
        url = params['url']
        pkg_type = params['pkg_type']
        install_method = params['install_method']
        branch = params.get('branch')
        release_asset_url = params.get('release_asset_url')
        
        self.progress = QProgressDialog("Installing package...", None, 0, 0, self)
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.show()
        
        self.worker = InstallWorker(
            self.package_manager,
            url,
            pkg_type,
            install_method,
            branch=branch,
            force=True,
            plugin_variant=None,
            release_asset_url=release_asset_url,
            release_asset_name=self._last_install_params.get('release_asset_name'),
            selected_entrypoint=None
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.progress.connect(self.log)
        self.worker.finished.connect(self.install_finished)
        self.worker.conflict_detected.connect(self.handle_install_conflict)  # In case nested conflicts occur
        self.worker.variant_selection_requested.connect(self.handle_variant_selection)
        self.worker.entrypoint_selection_requested.connect(self.handle_entrypoint_selection)
        self.worker.start()

    def handle_variant_selection(self, result):
        """Handle plugin variant selection request.
        
        Args:
            result: dict - Installation result with available variants
        """
        try:
            self.progress.close()
        except Exception:
            pass

        variants = result.get('variants', [])
        if not variants:
            return
        
        is_release_asset = result.get('is_release_asset', False)
        self._pending_variant_choices = variants

        choices = [v.get('name') for v in variants]
        choice, ok = QInputDialog.getItem(self, "Select plugin variant", "Select variant to install:", choices, 0, False)
        if not ok:
            self.log("Variant selection cancelled by user")
            return

        selected_info = next((v for v in variants if v.get('name') == choice), None)
        asset_url = None
        if selected_info:
            asset_url = selected_info.get('url') or selected_info.get('download_url')

        if is_release_asset:
            self._last_install_params['release_asset_name'] = choice

        self._retry_install_with_variant(choice, is_release_asset=is_release_asset, asset_url=asset_url)

    def handle_entrypoint_selection(self, result):
        """Handle addon lua file entrypoint selection.
        
        Args:
            result: dict - Installation result with available lua files
        """
        try:
            self.progress.close()
        except Exception:
            pass

        lua_files = result.get('lua_files', [])
        if not lua_files:
            self._show_centered_message(QMessageBox.Icon.Critical, "Error", "No lua files found")
            return
        
        # Store result data for retry
        self._pending_entrypoint_result = result
        
        choice, ok = QInputDialog.getItem(
            self, 
            "Select Addon Entrypoint", 
            "Multiple lua files found. Select the main addon file:", 
            lua_files, 
            0, 
            False
        )
        if not ok:
            self.log("Entrypoint selection cancelled by user")
            return

        # Retry installation with selected entrypoint
        self._retry_with_entrypoint(choice, result)

    def _retry_with_entrypoint(self, entrypoint_name, result):
        """Retry installation with selected lua entrypoint.
        
        Args:
            entrypoint_name: str - Selected lua filename
            result: dict - Original installation result
        """
        # Check if this is a manual install
        addon_path = result.get('addon_path')
        if addon_path:
            # Manual installation
            payload = {
                'pkg_type': 'addon',
                'addon_path': addon_path,
                'selected_entrypoint': entrypoint_name
            }
            
            self.log(f"Retrying manual install with entrypoint: {entrypoint_name}")
            self.progress = self._create_progress("Installing package...", None, 0, 0)
            self.manual_install_worker = ManualInstallWorker(self.package_manager, payload)
            self.manual_install_worker.progress.connect(self.update_progress)
            self.manual_install_worker.progress.connect(self.log)
            self.manual_install_worker.finished.connect(self.manual_install_finished)
            self.manual_install_worker.entrypoint_selection_requested.connect(self.handle_entrypoint_selection)
            self.manual_install_worker.start()
        else:
            # Git or release installation
            if not hasattr(self, '_last_install_params'):
                self._show_centered_message(QMessageBox.Icon.Critical, "Error", "Installation parameters not found")
                return

            params = self._last_install_params
            params['selected_entrypoint'] = entrypoint_name
            
            url = params['url']
            pkg_type = params['pkg_type']
            install_method = params['install_method']
            branch = params.get('branch')
            release_asset_url = params.get('release_asset_url')
            release_asset_name = params.get('release_asset_name')
            
            self.log(f"Retrying installation with entrypoint: {entrypoint_name}")
            self.progress = QProgressDialog("Installing package...", None, 0, 0, self)
            self.progress.setWindowModality(Qt.WindowModality.WindowModal)
            self.progress.show()

            self.worker = InstallWorker(
                self.package_manager,
                url,
                pkg_type,
                install_method,
                branch=branch,
                force=False,
                plugin_variant=None,
                release_asset_url=release_asset_url,
                release_asset_name=release_asset_name,
                selected_entrypoint=entrypoint_name
            )
            self.worker.progress.connect(self.update_progress)
            self.worker.progress.connect(self.log)
            self.worker.finished.connect(self.install_finished)
            self.worker.conflict_detected.connect(self.handle_install_conflict)
            self.worker.variant_selection_requested.connect(self.handle_variant_selection)
            self.worker.entrypoint_selection_requested.connect(self.handle_entrypoint_selection)
            self.worker.start()

    def _retry_install_with_variant(self, variant_name, is_release_asset=False, asset_url=None):
        """Retry installation with selected plugin variant.
        
        Args:
            variant_name: str - Selected variant name
            is_release_asset: bool - Whether variant is from release
            asset_url: Optional str - Specific asset URL for variant
        """
        if not hasattr(self, '_last_install_params'):
            self._show_centered_message(QMessageBox.Icon.Critical, "Error", "Installation parameters not found")
            return

        params = self._last_install_params
        url = params['url']
        pkg_type = params['pkg_type']
        install_method = params['install_method']
        branch = params.get('branch')
        release_asset_url = params.get('release_asset_url')
        
        plugin_variant = variant_name
        
        if is_release_asset:
            plugin_variant = None
            if asset_url:
                release_asset_url = asset_url
            params['release_asset_name'] = variant_name
        
        params['release_asset_url'] = release_asset_url

        self.progress = QProgressDialog("Installing package...", None, 0, 0, self)
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.show()

        self.worker = InstallWorker(
            self.package_manager,
            url,
            pkg_type,
            install_method,
            branch=branch,
            force=False,
            plugin_variant=plugin_variant,
            release_asset_url=release_asset_url,
            release_asset_name=self._last_install_params.get('release_asset_name'),
            selected_entrypoint=None
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.progress.connect(self.log)
        self.worker.finished.connect(self.install_finished)
        self.worker.conflict_detected.connect(self.handle_install_conflict)
        self.worker.variant_selection_requested.connect(self.handle_variant_selection)
        self.worker.entrypoint_selection_requested.connect(self.handle_entrypoint_selection)
        self.worker.start()
    
    def refresh_package_lists(self):
        """Refresh package list displays for addons and plugins."""
        packages = self.package_tracker.get_all_packages()
        
        self.addons_list.clear()
        self.plugins_list.clear()
        
        addon_count = self._populate_package_list(self.addons_list, packages.get('addons', {}), 'addon')
        plugin_count = self._populate_package_list(self.plugins_list, packages.get('plugins', {}), 'plugin')
        
        self.tabs.setTabText(0, f"Addons ({addon_count})")
        self.tabs.setTabText(1, f"Plugins ({plugin_count})")
        
        # refresh the script editor lists
        if self.current_script:
            self.populate_script_ui()
    
    def _populate_package_list(self, tree_widget, packages, pkg_type):
        """Populate tree widget with packages from tracker.
        
        Args:
            tree_widget: QTreeWidget - Widget to populate
            packages: dict - Packages dict from tracker
            pkg_type: str - 'addon' or 'plugin'
        
        Returns:
            int - Number of packages added
        """
        categories = {
            'pre-installed': [],
            'git': [],
            'release': [],
            'manual': []
        }
        
        for name, info in packages.items():
            install_method = info.get('install_method', 'pre-installed')
            if install_method not in categories:
                install_method = 'git'
            categories[install_method].append((name, info))
        
        category_labels = {
            'pre-installed': 'Pre-installed',
            'git': 'Cloned from Git',
            'release': 'Installed from Release',
            'manual': 'Manually installed'
        }
        
        total_count = 0
        for category_key in ['pre-installed', 'git', 'release', 'manual']:
            items = categories[category_key]
            if not items:
                continue
            
            category_item = QTreeWidgetItem(tree_widget)
            category_item.setText(0, f"{category_labels[category_key]} ({len(items)})")
            category_item.setData(0, Qt.ItemDataRole.UserRole, {
                'is_category': True, 
                'category': category_key
            })
            font = QFont()
            font.setBold(True)
            category_item.setFont(0, font)
            category_item.setExpanded(True)  # Start expanded
            
            for name, info in sorted(items, key=lambda x: x[0].lower()):
                item = QTreeWidgetItem(category_item)
                item.setText(0, name)
                item.setData(0, Qt.ItemDataRole.UserRole, {
                    'type': pkg_type, 
                    'name': name, 
                    'info': info,
                    'category': category_key
                })
                total_count += 1
        
        return total_count
    
    def filter_packages(self, pkg_type):
        """Filter installed packages list by type.
        
        Args:
            pkg_type: str - 'addon' or 'plugin'
        """
        tree_widget = self.addons_list if pkg_type == 'addon' else self.plugins_list
        search_text = (self.addons_search.text() if pkg_type == 'addon' else self.plugins_search.text()).lower()

        # Iterate through top-level categories
        for i in range(tree_widget.topLevelItemCount()):
            category_item = tree_widget.topLevelItem(i)
            category_has_visible = False
            
            # Check children (packages)
            for j in range(category_item.childCount()):
                child = category_item.child(j)
                data = child.data(0, Qt.ItemDataRole.UserRole) or {}
                name = data.get('name', '')
                
                if not search_text or search_text in name.lower():
                    child.setHidden(False)
                    category_has_visible = True
                else:
                    child.setHidden(True)
            
            # Hide category if no children are visible
            category_item.setHidden(not category_has_visible and bool(search_text))
    
    def filter_script_list(self, pkg_type):
        """Filter script packages by search text.
        
        Args:
            pkg_type: str - 'addon' or 'plugin'
        """
        tree_widget = self.script_addons_list if pkg_type == 'addon' else self.script_plugins_list
        search_text = (self.script_addons_search.text() if pkg_type == 'addon' else self.script_plugins_search.text()).lower()

        # Iterate through top-level items
        for i in range(tree_widget.topLevelItemCount()):
            item = tree_widget.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            name = data.get('name', '')
            
            # Show item if search text matches the addon/plugin name
            if not search_text or search_text in name.lower():
                item.setHidden(False)
            else:
                item.setHidden(True)
    
    def filter_available_list(self, pkg_type):
        """Filter available packages by search text.
        
        Args:
            pkg_type: str - 'addon' or 'plugin'
        """
        tree_widget = self.available_addons_list if pkg_type == 'addon' else self.available_plugins_list
        search_text = (self.available_addons_search.text() if pkg_type == 'addon' else self.available_plugins_search.text()).lower()

        # Iterate through top-level categories
        for i in range(tree_widget.topLevelItemCount()):
            category_item = tree_widget.topLevelItem(i)
            category_has_visible = False
            
            # Check children (available packages)
            for j in range(category_item.childCount()):
                child = category_item.child(j)
                data = child.data(0, Qt.ItemDataRole.UserRole) or {}
                name = data.get('name', '')
                
                if not search_text or search_text in name.lower():
                    child.setHidden(False)
                    category_has_visible = True
                else:
                    child.setHidden(True)
            
            # Hide category if no children are visible
            category_item.setHidden(not category_has_visible and bool(search_text))
    
    def show_package_info(self, item):
        """Display detailed package information in info panel.
        
        Args:
            item: QTreeWidgetItem - Selected package item
        """
        data = item.data(0, Qt.ItemDataRole.UserRole)
        
        if data.get('is_category'):
            return
        
        info = data['info']
        
        info_text = f"Name: {data['name']}\n"
        info_text += f"Type: {data['type'].capitalize()}\n"
        
        if 'source' in info:
            info_text += f"Source: {info['source']}\n"
        
        if 'install_method' in info:
            info_text += f"Install Method: {info['install_method']}\n"
        
        if info.get('install_method') == 'git':
            if 'commit' in info:
                info_text += f"Commit: {info['commit']}\n"
            if 'branch' in info:
                info_text += f"Branch: {info['branch']}\n"
        elif info.get('install_method') == 'release':
            if 'release_tag' in info:
                info_text += f"Release: {info['release_tag']}\n"
        
        if 'installed_date' in info:
            info_text += f"Installed: {info['installed_date']}\n"
        
        self.info_text.setPlainText(info_text)
    
    
    def update_package(self, pkg_type):
        """Update selected installed package.
        
        Args:
            pkg_type: str - 'addon' or 'plugin'
        """
        tree_widget = self.addons_list if pkg_type == "addon" else self.plugins_list
        selected_items = tree_widget.selectedItems()
        
        if not selected_items:
            self._show_centered_message(QMessageBox.Icon.Warning, "Error", f"Please select one or more {pkg_type}s to update")
            return
        
        # Filter out categories and get package names
        package_names = []
        for item in selected_items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data.get('is_category'):
                package_names.append(data['name'])
        
        if not package_names:
            self._show_centered_message(QMessageBox.Icon.Warning, "Error", "Please select packages, not categories")
            return
        
        if len(package_names) == 1:
            # Single package update
            package_name = package_names[0]
            self.log(f"Starting update for {package_name}...")
            self.progress = self._create_progress(f"Updating {package_name}...", None, 0, 0)
            self._last_update_params = {
                'package_name': package_name,
                'pkg_type': pkg_type,
                'release_asset_url': None,
                'release_asset_name': None,
                'manual_payload': None
            }
            self.update_worker = UpdateWorker(self.package_manager, package_name, pkg_type)
            self.update_worker.progress.connect(self.update_progress)
            self.update_worker.progress.connect(self.log)
            self.update_worker.finished.connect(self.update_finished)
            self.update_worker.variant_selection_requested.connect(self.handle_update_variant_selection)
            self.update_worker.manual_update_requested.connect(self.handle_manual_update_request)
            self.update_worker.start()
        else:
            # Multiple packages - use batch update
            self.log(f"Starting batch update of {len(package_names)} {pkg_type}s...")
            self.batch_progress = self._create_progress(f"Updating {pkg_type}s...", "Cancel", 0, len(package_names))
            self.batch_worker = BatchUpdateWorker(self.package_manager, package_names, pkg_type)
            self.batch_worker.progress.connect(self.batch_update_progress)
            self.batch_worker.log.connect(self.log)
            self.batch_worker.finished.connect(self.batch_update_finished)
            self.batch_progress.canceled.connect(self.batch_worker.cancel)
            self.batch_worker.start()
    
    def update_finished(self, success, message, already_updated=False):
        """Handle package update completion.
        
        Args:
            success: bool - Whether update succeeded
            message: str - Result message
            already_updated: bool - Whether package was already up-to-date
        """
        try:
            self.progress.close()
        except Exception:
            pass
        self._last_update_params = None
        
        if success:
            self.log(message)
            if already_updated:
                self._show_centered_message(QMessageBox.Icon.Information, "Already up-to-date", message)
            else:
                self._show_centered_message(QMessageBox.Icon.Information, "Success", message)
            self.refresh_package_lists()
        else:
            self.log(f"Update failed: {message}")
            self._show_centered_message(QMessageBox.Icon.Warning, "Update failed", message)

    def handle_update_variant_selection(self, result):
        """Handle plugin variant selection for update.
        
        Args:
            result: dict - Update result with available variants
        """
        try:
            self.progress.close()
        except Exception:
            pass

        variants = result.get('variants', [])
        if not variants:
            self.update_finished(False, "No release variants are available to select for this package.", False)
            return

        choices = [v.get('name') for v in variants if v.get('name')]
        if not choices:
            self.update_finished(False, "Unable to determine variant names for this release.", False)
            return

        choice, ok = QInputDialog.getItem(self, "Select release asset", "Choose the release asset to update with:", choices, 0, False)
        if not ok:
            self.update_finished(False, "Variant selection cancelled by user.", False)
            return

        selected_info = next((v for v in variants if v.get('name') == choice), None)
        if not selected_info:
            self.update_finished(False, "Selected variant information was not found.", False)
            return

        asset_url = selected_info.get('url') or selected_info.get('download_url') or selected_info.get('browser_download_url')
        if not asset_url:
            self.update_finished(False, "Selected variant is missing a download URL.", False)
            return

        if not self._last_update_params:
            self._last_update_params = {
                'package_name': result.get('package_name'),
                'pkg_type': result.get('pkg_type'),
                'release_asset_url': None,
                'release_asset_name': None,
                'manual_payload': None
            }
        else:
            if result.get('package_name'):
                self._last_update_params['package_name'] = result['package_name']
            if result.get('pkg_type'):
                self._last_update_params['pkg_type'] = result['pkg_type']

        self._last_update_params['release_asset_url'] = asset_url
        self._last_update_params['release_asset_name'] = selected_info.get('name')

        self._retry_update_with_variant()

    def _retry_update_with_variant(self):
        """Retry package update with selected plugin variant.
        
        Uses cached update parameters to restart with chosen variant.
        """
        if not self._last_update_params:
            self._show_centered_message(QMessageBox.Icon.Critical, "Error", "Update parameters not found.")
            return

        package_name = self._last_update_params.get('package_name')
        pkg_type = self._last_update_params.get('pkg_type')
        release_asset_url = self._last_update_params.get('release_asset_url')
        release_asset_name = self._last_update_params.get('release_asset_name')
        self._last_update_params['manual_payload'] = None

        if not package_name or not pkg_type:
            self._show_centered_message(QMessageBox.Icon.Critical, "Error", "Missing package information for update retry.")
            return

        self.log(f"Retrying update for {package_name} with asset {release_asset_name or 'selected variant'}...")
        self.progress = self._create_progress(f"Updating {package_name}...", None, 0, 0)
        self.update_worker = UpdateWorker(
            self.package_manager,
            package_name,
            pkg_type,
            release_asset_url=release_asset_url,
            release_asset_name=release_asset_name
        )
        self.update_worker.progress.connect(self.update_progress)
        self.update_worker.progress.connect(self.log)
        self.update_worker.finished.connect(self.update_finished)
        self.update_worker.variant_selection_requested.connect(self.handle_update_variant_selection)
        self.update_worker.manual_update_requested.connect(self.handle_manual_update_request)
        self.update_worker.start()

    def handle_manual_update_request(self, result):
        """Handle manual update requirement during package update.
        
        Args:
            result: dict - Update result indicating manual update needed
        """
        try:
            self.progress.close()
        except Exception:
            pass

        package_name = result.get('package_name')
        pkg_type = result.get('pkg_type')
        reason = result.get('reason', 'manual')

        if package_name:
            if reason == 'unknown-source':
                self.log(f"{package_name} has no source URL; prompting for manual files.")
            else:
                self.log(f"{package_name} was installed manually; prompting for new files.")

        dialog = ManualPackageDialog(self, mode='update', pkg_type=pkg_type, package_name=package_name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.update_finished(False, "Manual update cancelled by user.", False)
            return

        payload = dialog.get_payload()
        if not payload:
            self.update_finished(False, "Manual update cancelled.", False)
            return

        if not self._last_update_params:
            self._last_update_params = {
                'package_name': package_name,
                'pkg_type': pkg_type,
                'release_asset_url': None,
                'release_asset_name': None,
                'manual_payload': None
            }
        else:
            self._last_update_params['package_name'] = package_name
            self._last_update_params['pkg_type'] = pkg_type

        self._last_update_params['manual_payload'] = payload
        self._retry_update_with_manual()

    def _retry_update_with_manual(self):
        """Retry package update using manual file selection.
        
        Opens manual update dialog to collect user file selections.
        """
        if not self._last_update_params:
            self._show_centered_message(QMessageBox.Icon.Critical, "Error", "Update parameters not found.")
            return

        package_name = self._last_update_params.get('package_name')
        pkg_type = self._last_update_params.get('pkg_type')
        manual_payload = self._last_update_params.get('manual_payload')

        if not package_name or not pkg_type or not manual_payload:
            self._show_centered_message(QMessageBox.Icon.Critical, "Error", "Manual update data missing.")
            return

        self.log(f"Retrying update for {package_name} with manual input...")
        self.progress = self._create_progress(f"Updating {package_name}...", None, 0, 0)
        self.update_worker = UpdateWorker(
            self.package_manager,
            package_name,
            pkg_type,
            manual_payload=manual_payload
        )
        self.update_worker.progress.connect(self.update_progress)
        self.update_worker.progress.connect(self.log)
        self.update_worker.finished.connect(self.update_finished)
        self.update_worker.variant_selection_requested.connect(self.handle_update_variant_selection)
        self.update_worker.manual_update_requested.connect(self.handle_manual_update_request)
        self.update_worker.start()

    def batch_update(self, pkg_type):
        """Update multiple packages at once.
        
        Args:
            pkg_type: str - 'addon' or 'plugin'
        """
        packages = self.package_tracker.get_all_packages()
        type_key = f"{pkg_type}s"
        package_list = packages.get(type_key, {})
        
        if not package_list:
            self._show_centered_message(QMessageBox.Icon.Information, "No packages", f"No {pkg_type}s to update")
            return
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(f"Update all {len(package_list)} {pkg_type}s?")
        msg.setInformativeText("This may take a while.")
        msg.setWindowTitle("Batch update")
        msg.setMinimumSize(420, 140)
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.show()
        self._center_widget(msg)

        if msg.exec() == QMessageBox.StandardButton.Yes:
            self.log(f"Starting batch update of {len(package_list)} {pkg_type}s...")
            
            self.batch_progress = self._create_progress(f"Updating {pkg_type}s...", "Cancel", 0, len(package_list))
            
            self.batch_worker = BatchUpdateWorker(self.package_manager, list(package_list.keys()), pkg_type)
            self.batch_worker.progress.connect(self.batch_update_progress)
            self.batch_worker.log.connect(self.log)
            self.batch_worker.finished.connect(self.batch_update_finished)
            
            self.batch_progress.canceled.connect(self.batch_worker.cancel)
            
            self.batch_worker.start()
    
    def batch_update_progress(self, message, current, total):
        """Update batch update progress display.
        
        Args:
            message: str - Progress message
            current: int - Current package number
            total: int - Total packages
        """
        self.batch_progress.setLabelText(message)
        self.batch_progress.setValue(current)
        QApplication.processEvents()
    
    def batch_update_finished(self, updated, failed, skipped):
        """Handle batch update completion.
        
        Args:
            updated: int - Number of successfully updated packages
            failed: int - Number of failed updates
            skipped: int - Number of skipped packages
        """
        self.batch_progress.close()
        self.refresh_package_lists()
        
        summary = f"Batch update complete!\n\nUpdated: {updated}\nAlready up-to-date: {skipped}\nFailed: {failed}"
        self.log(f"Batch update complete: {updated} updated, {skipped} skipped, {failed} failed")
        self._show_centered_message(QMessageBox.Icon.Information, "Batch update complete", summary)
    
    def remove_package(self, pkg_type):
        """Remove selected package from installation.
        
        Args:
            pkg_type: str - 'addon' or 'plugin'
        """
        tree_widget = self.addons_list if pkg_type == "addon" else self.plugins_list
        selected_items = tree_widget.selectedItems()
        
        if not selected_items:
            self._show_centered_message(QMessageBox.Icon.Warning, "Error", f"Please select one or more {pkg_type}s to remove")
            return
        
        # Filter out categories and get package names
        package_names = []
        for item in selected_items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data.get('is_category'):
                package_names.append(data['name'])
        
        if not package_names:
            self._show_centered_message(QMessageBox.Icon.Warning, "Error", "Please select packages, not categories")
            return
        
        # Confirm removal
        if len(package_names) == 1:
            confirm_text = f"Are you sure you want to remove '{package_names[0]}'?"
        else:
            confirm_text = f"Are you sure you want to remove {len(package_names)} {pkg_type}s?\n\n" + "\n".join(package_names)
        
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(confirm_text)
        msg.setWindowTitle("Confirm removal")
        msg.setMinimumSize(420, 140)
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.show()
        self._center_widget(msg)

        if msg.exec() == QMessageBox.StandardButton.Yes:
            success_count = 0
            failed = []
            
            for package_name in package_names:
                self.log(f"Removing {package_name}...")
                result = self.package_manager.remove_package(package_name, pkg_type)
                
                if result['success']:
                    self.log(result['message'])
                    success_count += 1
                else:
                    self.log(f"Removal failed: {result['error']}")
                    failed.append(f"{package_name}: {result['error']}")
            
            self.refresh_package_lists()
            self.info_text.clear()
            
            if success_count > 0 and not failed:
                self._show_centered_message(QMessageBox.Icon.Information, "Success", f"Successfully removed {success_count} {pkg_type}(s)")
            elif success_count > 0 and failed:
                self._show_centered_message(QMessageBox.Icon.Warning, "Partial Success", f"Removed {success_count}, failed {len(failed)}:\n" + "\n".join(failed))
            else:
                self._show_centered_message(QMessageBox.Icon.Critical, "Error", "Failed to remove packages:\n" + "\n".join(failed))
    
    def open_repository(self, pkg_type):
        """Open the repository URL in the default browser"""
        tree_widget = self.addons_list if pkg_type == "addon" else self.plugins_list
        selected_items = tree_widget.selectedItems()
        
        if not selected_items:
            self._show_centered_message(QMessageBox.Icon.Warning, "Error", f"Please select a {pkg_type} to view its repository")
            return
        
        # Get first non-category item
        for item in selected_items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data.get('is_category'):
                info = data.get('info', {})
                source = info.get('source')
                if source:
                    import webbrowser
                    webbrowser.open(source)
                    self.log(f"Opening repository: {source}")
                else:
                    self._show_centered_message(QMessageBox.Icon.Warning, "No Repository", "No repository URL found for this package")
                return
        
        self._show_centered_message(QMessageBox.Icon.Warning, "Error", "Please select a package, not a category")
    
    def open_readme(self, pkg_type):
        """Display README.md content in a popup window with markdown rendering"""
        tree_widget = self.addons_list if pkg_type == "addon" else self.plugins_list
        selected_items = tree_widget.selectedItems()
        
        if not selected_items:
            self._show_centered_message(QMessageBox.Icon.Warning, "Error", f"Please select a {pkg_type} to view its README")
            return
        
        # Get first non-category item
        for item in selected_items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data.get('is_category'):
                package_name = data.get('name')
                
                # Find README file
                if pkg_type == 'addon':
                    package_dir = self.package_manager.addons_dir / package_name
                else:
                    # For plugins, check docs folder
                    package_dir = self.package_manager.docs_dir / package_name
                
                readme_files = ['README.md', 'readme.md', 'Readme.md', 'README.MD', 'README.txt', 'readme.txt', 'INDEX.html', 'index.html', 'Index.html', 'README.html', 'readme.html', 'Readme.html', 'README.htm', 'readme.htm', 'Readme.htm']
                readme_path = None
                
                for readme_name in readme_files:
                    potential_path = package_dir / readme_name
                    if potential_path.exists():
                        readme_path = potential_path
                        break
                
                if readme_path:
                    try:
                        with open(readme_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                        
                        # Create dialog
                        dialog = QDialog(self)
                        dialog.setWindowTitle(f"{package_name} - README")
                        dialog.setMinimumSize(800, 600)
                        try:
                            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
                            dialog.setModal(False)
                            dialog.setWindowModality(Qt.WindowModality.NonModal)
                            dialog.setWindowFlag(Qt.WindowType.Window, True)
                        except Exception:
                            pass

                        layout = QVBoxLayout(dialog)
                        
                        # Determine content type
                        is_markdown = readme_path.suffix.lower() in ['.md', '.markdown']
                        is_html = readme_path.suffix.lower() in ['.html', '.htm']
                        
                        if WEBENGINE_AVAILABLE and (is_markdown or is_html):
                            markdown_viewer = MarkdownViewer(dialog)
                            if is_html:
                                # For HTML, set it directly without markdown parsing
                                markdown_viewer.set_html(content)
                            else:
                                # For markdown, use markdown parsing
                                markdown_viewer.set_markdown(content)
                            layout.addWidget(markdown_viewer)
                        else:
                            # Fallback to QTextEdit
                            text_display = QTextEdit()
                            text_display.setReadOnly(True)
                            
                            if is_markdown:
                                # Try to use built-in markdown support
                                text_display.setMarkdown(content)
                            else:
                                text_display.setPlainText(content)
                            
                            layout.addWidget(text_display)
                        
                        close_btn = QPushButton("Close")
                        close_btn.clicked.connect(dialog.close)
                        layout.addWidget(close_btn)

                        self._center_widget(dialog)
                        dialog.show()
                        
                    except Exception as e:
                        self._show_centered_message(QMessageBox.Icon.Critical, "Error", f"Failed to read README:\n{str(e)}")
                else:
                    self._show_centered_message(QMessageBox.Icon.Information, "No README", f"No README file found for {package_name}")
                return
        
        self._show_centered_message(QMessageBox.Icon.Warning, "Error", "Please select a package, not a category")
    
    def refresh_script_list(self):
        """Refresh script selector with available scripts."""
        scripts_dir = os.path.join(self.ashita_root, 'scripts')
        
        if not os.path.exists(scripts_dir):
            self.log("Scripts folder not found")
            return
        
        scripts = ScriptParser.get_all_scripts(scripts_dir)
        
        self.script_selector.clear()
        self.script_selector.addItems(scripts)
        
        # Default to default.txt if it exists
        if 'default.txt' in scripts:
            self.script_selector.setCurrentText('default.txt')
        elif scripts:
            self.script_selector.setCurrentIndex(0)
    
    def load_selected_script(self, filename):
        """Load selected script file for editing.
        
        Args:
            filename: str - Script filename to load
        """
        if not filename:
            return
        
        scripts_dir = os.path.join(self.ashita_root, 'scripts')
        script_path = os.path.join(scripts_dir, filename)
        
        if not os.path.exists(script_path):
            self.log(f"Script file not found: {filename}")
            return
        
        self.current_script = ScriptParser(script_path)
        self.current_script_path = script_path
        
        if self.current_script.parse():
            self.populate_script_ui()
            self.log(f"Loaded script: {filename}")
        else:
            self.log(f"Failed to parse script: {filename}")
    
    def populate_script_ui(self):
        """Populate script editor UI with current script data."""
        if not self.current_script:
            return
        
        # Temporarily block signals to prevent recursive updates
        self.script_plugins_list.blockSignals(True)
        self.script_addons_list.blockSignals(True)
        self.script_exec_list.blockSignals(True)
        self.script_config_list.blockSignals(True)
        
        # Populate plugins
        self.script_plugins_list.clear()
        for plugin in self.current_script.plugins:
            item = QTreeWidgetItem(self.script_plugins_list)
            item.setText(0, plugin['name'])
            item.setData(0, Qt.ItemDataRole.UserRole, plugin)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if plugin['enabled'] else Qt.CheckState.Unchecked)
        
        # Update plugins group title
        enabled_count = sum(1 for p in self.current_script.plugins if p['enabled'])
        disabled_count = len(self.current_script.plugins) - enabled_count
        self.script_plugins_group.setTitle(f"Plugins (Enabled: {enabled_count} | Disabled: {disabled_count})")
        
        # Populate addons
        self.script_addons_list.clear()
        for addon in self.current_script.addons:
            args_text = f" {addon['args']}" if addon['args'] else ""
            item = QTreeWidgetItem(self.script_addons_list)
            item.setText(0, f"{addon['name']}{args_text}")
            item.setData(0, Qt.ItemDataRole.UserRole, addon)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if addon['enabled'] else Qt.CheckState.Unchecked)
        
        # Update addons group title
        enabled_count = sum(1 for a in self.current_script.addons if a['enabled'])
        disabled_count = len(self.current_script.addons) - enabled_count
        self.script_addons_group.setTitle(f"Addons (Enabled: {enabled_count} | Disabled: {disabled_count})")
        
        # Populate exec commands
        self.script_exec_list.clear()
        all_execs = self.current_script.exec_binds + self.current_script.exec_aliases + self.current_script.exec_other
        enabled_count = 0
        for exec_item in all_execs:
            item_type = exec_item.get('type', 'exec')
            
            if item_type == 'exec':
                display_text = f"/exec {exec_item['path']}"
            elif item_type == 'bind':
                display_text = f"/bind {exec_item['path']}"
            elif item_type == 'alias':
                display_text = f"/alias {exec_item['path']}"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, exec_item)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if exec_item['enabled'] else Qt.CheckState.Unchecked)
            self.script_exec_list.addItem(item)
            if exec_item['enabled']:
                enabled_count += 1
        
        # Update exec group title
        disabled_count = len(all_execs) - enabled_count
        self.script_exec_group.setTitle(
            f"Keybinds and Alias (Enabled: {enabled_count} | Disabled: {disabled_count})"
        )
        
        # Set wait time
        self.wait_time_spin.setValue(self.current_script.wait_time)
        
        # Populate config commands
        self.script_config_list.clear()
        enabled_count = 0
        for cmd in self.current_script.config_commands:
            item = QListWidgetItem(cmd['command'])
            item.setData(Qt.ItemDataRole.UserRole, cmd)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if cmd['enabled'] else Qt.CheckState.Unchecked)
            self.script_config_list.addItem(item)
            if cmd['enabled']:
                enabled_count += 1
        
        # Update config group title
        disabled_count = len(self.current_script.config_commands) - enabled_count
        self.script_config_group.setTitle(
            f"Configuration Commands (Enabled: {enabled_count} | Disabled: {disabled_count})"
        )
        
        # Populate available plugins
        self.available_plugins_list.clear()
        all_packages = self.package_tracker.get_all_packages()
        installed_plugins = all_packages.get('plugins', {})
        script_plugin_names = {p['name'] for p in self.current_script.plugins}
        
        # Categorize available plugins
        categories = {
            'pre-installed': [],
            'git': [],
            'release': [],
            'manual': []
        }

        for plugin_name in sorted(installed_plugins.keys()):
            if plugin_name not in script_plugin_names:
                plugin_info = installed_plugins[plugin_name]
                install_method = plugin_info.get('install_method', 'pre-installed')
                if install_method not in categories:
                    install_method = 'git'
                categories[install_method].append(plugin_name)

        # Add category headers and items
        total_available_plugins = 0
        category_labels = {
            'pre-installed': 'Pre-installed',
            'git': 'Cloned from Git',
            'release': 'Installed from Release',
            'manual': 'Manually installed'
        }

        for key in ['pre-installed', 'git', 'release', 'manual']:
            names = categories.get(key, [])
            if not names:
                continue
            
            label = category_labels.get(key, key)
            cat_item = QTreeWidgetItem(self.available_plugins_list)
            cat_item.setText(0, f"{label} ({len(names)})")
            cat_item.setData(0, Qt.ItemDataRole.UserRole, {'is_category': True, 'category': key})
            font = QFont()
            font.setBold(True)
            cat_item.setFont(0, font)
            cat_item.setExpanded(True)
            
            for name in names:
                item = QTreeWidgetItem(cat_item)
                item.setText(0, name)
                item.setData(0, Qt.ItemDataRole.UserRole, {'name': name})
            
            total_available_plugins += len(names)
        
        # Update available plugins
        self.available_plugins_group.setTitle(f"Available Plugins ({total_available_plugins})")
        
        # Populate available addons
        self.available_addons_list.clear()
        installed_addons = all_packages.get('addons', {})
        script_addon_names = {a['name'] for a in self.current_script.addons}
        
        categories = {
            'pre-installed': [],
            'git': [],
            'release': []
        }

        for addon_name in sorted(installed_addons.keys()):
            if addon_name not in script_addon_names:
                addon_info = installed_addons[addon_name]
                install_method = addon_info.get('install_method', 'pre-installed')
                if install_method not in categories:
                    install_method = 'git'
                categories[install_method].append(addon_name)

        total_available_addons = 0
        category_labels = {
            'pre-installed': 'Pre-installed',
            'git': 'Cloned from Git',
            'release': 'Installed from Release'
        }

        for key in ['pre-installed', 'git', 'release']:
            names = categories.get(key, [])
            if not names:
                continue
            
            label = category_labels.get(key, key)
            cat_item = QTreeWidgetItem(self.available_addons_list)
            cat_item.setText(0, f"{label} ({len(names)})")
            cat_item.setData(0, Qt.ItemDataRole.UserRole, {'is_category': True, 'category': key})
            font = QFont()
            font.setBold(True)
            cat_item.setFont(0, font)
            cat_item.setExpanded(True)
            
            for name in names:
                item = QTreeWidgetItem(cat_item)
                item.setText(0, name)
                item.setData(0, Qt.ItemDataRole.UserRole, {'name': name})
            
            total_available_addons += len(names)
        
        # Update available addons group title with count
        self.available_addons_group.setTitle(f"Available Addons ({total_available_addons})")
        
        # Re-enable signals
        self.script_plugins_list.blockSignals(False)
        self.script_addons_list.blockSignals(False)
        self.script_exec_list.blockSignals(False)
        self.script_config_list.blockSignals(False)
    
    def on_tab_changed(self, index):
        """Handle tab change events.
        
        Args:
            index: int - Selected tab index
        """
        # Hide info panel when on Scripts tab (index 2)
        if index == 2:
            self.info_group.setVisible(False)
        else:
            self.info_group.setVisible(True)
    
    def on_script_item_changed(self, item, column, item_type):
        """Handle checkbox state changes"""
        if column is not None:
            # QTreeWidget - plugins/addons
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data:
                data['enabled'] = item.checkState(0) == Qt.CheckState.Checked
                # Update title counts
                if item_type == 'plugin':
                    enabled = sum(1 for p in self.current_script.plugins if p['enabled'])
                    disabled = len(self.current_script.plugins) - enabled
                    self.script_plugins_group.setTitle(f"Plugins (Enabled: {enabled} | Disabled: {disabled})")
                elif item_type == 'addon':
                    enabled = sum(1 for a in self.current_script.addons if a['enabled'])
                    disabled = len(self.current_script.addons) - enabled
                    self.script_addons_group.setTitle(f"Addons (Enabled: {enabled} | Disabled: {disabled})")
        else:
            # QListWidget - exec and config
            data = item.data(Qt.ItemDataRole.UserRole)
            if data:
                data['enabled'] = item.checkState() == Qt.CheckState.Checked
                if item_type == 'exec':
                    all_execs = self.current_script.exec_binds + self.current_script.exec_aliases + self.current_script.exec_other
                    enabled = sum(1 for e in all_execs if e['enabled'])
                    disabled = len(all_execs) - enabled
                    self.script_exec_group.setTitle(f"Keybinds and Alias (Enabled: {enabled} | Disabled: {disabled})")
                elif item_type == 'config':
                    enabled = sum(1 for c in self.current_script.config_commands if c['enabled'])
                    disabled = len(self.current_script.config_commands) - enabled
                    self.script_config_group.setTitle(f"Configuration Commands (Enabled: {enabled} | Disabled: {disabled})")

    def _sync_ui_to_script(self):
        """Sync UI states back to script model before saving"""
        if not self.current_script:
            return

        # Plugins - simple flat list now
        new_plugins = []
        for i in range(self.script_plugins_list.topLevelItemCount()):
            item = self.script_plugins_list.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data:
                data['enabled'] = item.checkState(0) == Qt.CheckState.Checked
                new_plugins.append(data)
        self.current_script.plugins = new_plugins

        # Addons - simple flat list now
        new_addons = []
        for i in range(self.script_addons_list.topLevelItemCount()):
            item = self.script_addons_list.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data:
                data['enabled'] = item.checkState(0) == Qt.CheckState.Checked
                new_addons.append(data)
        self.current_script.addons = new_addons

        # Execs: rebuild lists in order shown
        new_exec_binds = []
        new_exec_aliases = []
        new_exec_other = []
        for i in range(self.script_exec_list.count()):
            item = self.script_exec_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            enabled = item.checkState() == Qt.CheckState.Checked
            text = item.text().strip()
            # Determine type and path
            if text.startswith('/bind '):
                item_type = 'bind'
                path = text[len('/bind '):].strip()
            elif text.startswith('/alias '):
                item_type = 'alias'
                path = text[len('/alias '):].strip()
            elif text.startswith('/exec '):
                item_type = data.get('type', 'exec')
                path = text[len('/exec '):].strip()
            else:
                # fallback: use stored type or treat as exec
                item_type = data.get('type', 'exec')
                path = data.get('path', text)

            new_item = {'path': path, 'enabled': enabled, 'original': data.get('original', text), 'type': item_type}
            if item_type == 'bind':
                new_exec_binds.append(new_item)
            elif item_type == 'alias':
                new_exec_aliases.append(new_item)
            else:
                new_exec_other.append(new_item)

        self.current_script.exec_binds = new_exec_binds
        self.current_script.exec_aliases = new_exec_aliases
        self.current_script.exec_other = new_exec_other

        # Config commands
        new_configs = []
        for i in range(self.script_config_list.count()):
            item = self.script_config_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            enabled = item.checkState() == Qt.CheckState.Checked
            cmd_text = item.text().strip()
            new_configs.append({'command': cmd_text, 'enabled': enabled, 'original': data.get('original', cmd_text)})
        self.current_script.config_commands = new_configs
    
    def move_script_item(self, item_type, direction):
        """Move a plugin/addon up or down in the script"""
        if item_type == 'plugin':
            tree_widget = self.script_plugins_list
            items_list = self.current_script.plugins
        elif item_type == 'addon':
            tree_widget = self.script_addons_list
            items_list = self.current_script.addons
        else:
            return
        
        current_item = tree_widget.currentItem()
        if not current_item:
            return
        
        # Get current index in the list
        current_index = tree_widget.indexOfTopLevelItem(current_item)
        if current_index == -1:
            return
        
        new_index = current_index + direction
        
        if new_index < 0 or new_index >= tree_widget.topLevelItemCount():
            return
        
        # Swap in the data list
        items_list[current_index], items_list[new_index] = items_list[new_index], items_list[current_index]
        
        # Move the item in the UI without repopulating
        tree_widget.blockSignals(True)
        item_to_move = tree_widget.takeTopLevelItem(current_index)
        tree_widget.insertTopLevelItem(new_index, item_to_move)
        tree_widget.setCurrentItem(item_to_move)
        tree_widget.blockSignals(False)
    
    def add_exec_command(self):
        """Add new exec command to script."""
        from PyQt6.QtWidgets import QInputDialog
        
        # Prompt for command type
        items = ["Exec (Load script file)", "Bind (Keybind)", "Alias (Command alias)"]
        item, ok = QInputDialog.getItem(self, "Add Command", "Select command type:", items, 0, False)
        
        if not ok:
            return
        
        if item.startswith("Exec"):
            text, ok = QInputDialog.getText(self, "Add Exec Command", "Enter exec path (e.g., binds/CharacterName):")
            if ok and text:
                exec_item = {'path': text.strip(), 'enabled': True, 'original': f'/exec {text.strip()}', 'type': 'exec'}
                self.current_script.exec_other.append(exec_item)
                self.populate_script_ui()
        
        elif item.startswith("Bind"):
            text, ok = QInputDialog.getText(self, "Add Bind Command", "Enter bind command (e.g., insert /ashita):")
            if ok and text:
                exec_item = {'path': text.strip(), 'enabled': True, 'original': f'/bind {text.strip()}', 'type': 'bind'}
                self.current_script.exec_binds.append(exec_item)
                self.populate_script_ui()
        
        elif item.startswith("Alias"):
            text, ok = QInputDialog.getText(self, "Add Alias Command", "Enter alias command (e.g., /ls /linkshell):")
            if ok and text:
                exec_item = {'path': text.strip(), 'enabled': True, 'original': f'/alias {text.strip()}', 'type': 'alias'}
                self.current_script.exec_aliases.append(exec_item)
                self.populate_script_ui()
    
    def remove_exec_command(self):
        """Remove selected exec command from script."""
        current = self.script_exec_list.currentItem()
        if not current:
            self._show_centered_message(QMessageBox.Icon.Warning, "No selection", "Please select an exec command to remove")
            return
        
        data = current.data(Qt.ItemDataRole.UserRole)
        
        # Remove from appropriate list
        all_lists = [self.current_script.exec_binds, self.current_script.exec_aliases, self.current_script.exec_other]
        for lst in all_lists:
            if data in lst:
                lst.remove(data)
                break
        
        self.populate_script_ui()
    
    def add_config_command(self):
        """Add new config command to script."""
        from PyQt6.QtWidgets import QInputDialog
        
        text, ok = QInputDialog.getText(self, "Add Config Command", "Enter command (e.g., /fps 1):")
        
        if ok and text:
            cmd = text.strip()
            if not cmd.startswith('/'):
                cmd = '/' + cmd
            
            config_item = {'command': cmd, 'enabled': True, 'original': cmd}
            self.current_script.config_commands.append(config_item)
            self.populate_script_ui()
    
    def remove_config_command(self):
        """Remove selected config command from script."""
        current = self.script_config_list.currentItem()
        if not current:
            self._show_centered_message(QMessageBox.Icon.Warning, "No selection", "Please select a config command to remove")
            return
        
        data = current.data(Qt.ItemDataRole.UserRole)
        self.current_script.config_commands.remove(data)
        self.populate_script_ui()
    
    def add_to_script(self, item_type):
        """Add a plugin or addon from available list to the script"""
        if not self.current_script:
            return
        
        available_list = self.available_plugins_list if item_type == 'plugin' else self.available_addons_list
        current_item = available_list.currentItem()
        
        if not current_item:
            self._show_centered_message(QMessageBox.Icon.Warning, "No selection", f"Please select a {item_type} to add")
            return
        
        # Get the package name from UserRole data (handles category items)
        data = current_item.data(0, Qt.ItemDataRole.UserRole)
        if not data or 'name' not in data:
            # This is a category header, not a package
            self._show_centered_message(QMessageBox.Icon.Warning, "Invalid selection", "Please select a package, not a category")
            return
        
        package_name = data['name']
        
        # Add to the script model
        if item_type == 'plugin':
            self.current_script.plugins.append({'name': package_name, 'enabled': True})
        else:  # addon
            self.current_script.addons.append({'name': package_name, 'enabled': True})
        
        # Refresh UI to show updated lists
        self.populate_script_ui()
        self.log(f"Added {item_type} '{package_name}' to script")
    
    def remove_from_script(self, item_type):
        """Remove a plugin or addon from the script"""
        if not self.current_script:
            return
        
        script_list = self.script_plugins_list if item_type == 'plugin' else self.script_addons_list
        current_item = script_list.currentItem()
        
        if not current_item:
            self._show_centered_message(QMessageBox.Icon.Warning, "No selection", f"Please select a {item_type} to remove")
            return
        
        data = current_item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get('is_category'):
            return
        
        # Remove from the script model
        try:
            if item_type == 'plugin':
                self.current_script.plugins.remove(data)
            else:  # addon
                self.current_script.addons.remove(data)
        except ValueError:
            # If the item isn't present in the model for some reason, log and abort
            self.log(f"Warning: attempted to remove {item_type} '{data.get('name')}' but it was not found in script model")
            return
        
        # Refresh UI to show updated lists
        self.populate_script_ui()
        self.log(f"Removed {item_type} '{data['name']}' from script")
    
    def save_current_script(self):
        """Save current script changes to disk."""
        if not self.current_script:
            self._show_centered_message(QMessageBox.Icon.Warning, "No script", "No script is currently loaded")
            return
        
        # Update wait time from UI
        self.current_script.wait_time = self.wait_time_spin.value()
        # Sync UI states (checkboxes and ordering) back into the script model
        try:
            self._sync_ui_to_script()
            self.current_script.save()
            self.log(f"Script saved: {os.path.basename(self.current_script_path)}")
            self._show_centered_message(QMessageBox.Icon.Information, "Success", "Script saved successfully")
        except Exception as e:
            self.log(f"Failed to save script: {str(e)}")
            self._show_centered_message(QMessageBox.Icon.Critical, "Error", f"Failed to save script:\n{str(e)}")
    
    def open_settings(self):
        """Open application settings dialog."""
        dialog = SettingsDialog(self.package_tracker, self.ashita_root, self)
        dialog.exec()



def main():
    """Main application entry point. Initialize and run the Ashita Package Manager."""
    app = QApplication(sys.argv)
    app.setApplicationName("Ashita Package Manager")
    try:
        qdarktheme.setup_theme()
    except Exception:
        try:
            app.setStyleSheet(qdarktheme.load_stylesheet())
        except Exception:
            pass
    window = AshitaManagerUI()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
