"""
Markdown Viewer
Displays markdown content with support for remote images using QWebEngineView and marked.js
"""

from pathlib import Path
from PyQt6.QtCore import pyqtProperty, pyqtSignal, QObject, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWidgets import QWidget, QVBoxLayout

class Document(QObject):
    """Document object for web channel communication"""
    textChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.m_text = ""

    def get_text(self):
        return self.m_text

    def set_text(self, text):
        if self.m_text == text:
            return
        self.m_text = text
        self.textChanged.emit(self.m_text)

    text = pyqtProperty(str, fget=get_text, fset=set_text, notify=textChanged)


class DownloadManager(QObject):
    """Downloads markdown content from URLs"""
    finished = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._manager.finished.connect(self.handle_finished)

    @property
    def manager(self):
        return self._manager

    def start_download(self, url):
        """Download markdown from a URL"""
        self.manager.get(QNetworkRequest(url))

    def handle_finished(self, reply):
        """Handle download completion"""
        if reply.error() != QNetworkReply.NetworkError.NoError:
            print(f"Download error: {reply.errorString()}")
            return
        
        raw_data = reply.readAll()
        text = raw_data.data().decode('utf-8')
        self.finished.emit(text)


class MarkdownViewer(QWidget):
    """Widget for displaying markdown content with remote image support"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Get the directory where this script is located
        current_dir = Path(__file__).parent
        
        # Create the document and web channel
        self.document = Document(self)
        self.download_manager = DownloadManager(self)
        
        # Setup web channel
        self.channel = QWebChannel(self)
        self.channel.registerObject("content", self.document)
        
        # Create web view
        self.view = QWebEngineView(self)
        self.view.page().setWebChannel(self.channel)
        
        # Enable loading remote resources from local content
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        
        # Load the markdown HTML template
        html_path = current_dir / "markdown.html"
        if html_path.exists():
            url = QUrl.fromLocalFile(str(html_path))
            self.view.load(url)
        else:
            print(f"Warning: markdown.html not found at {html_path}")
        
        # Setup layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        
        # Connect download manager to document
        self.download_manager.finished.connect(self.document.set_text)
    
    def set_markdown(self, markdown_text):
        """Set markdown content directly from a string"""
        self.document.set_text(markdown_text)
    
    def set_html(self, html_content):
        """Set raw HTML content directly (renders without markdown parsing)"""
        # For HTML, we need to pass it to the view directly
        html_wrapper = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/sindresorhus/github-markdown-css@5/github-markdown.css">
            <style>
                body {{
                    padding: 20px;
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """
        self.view.setHtml(html_wrapper)
    
    def load_markdown_file(self, file_path):
        """Load markdown from a local file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                markdown_text = f.read()
            self.set_markdown(markdown_text)
        except Exception as e:
            print(f"Error loading markdown file: {e}")
    
    def load_markdown_url(self, url):
        """Load markdown from a remote URL"""
        if isinstance(url, str):
            url = QUrl(url)
        self.download_manager.start_download(url)
