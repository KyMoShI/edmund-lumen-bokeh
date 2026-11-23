"""Read the project's local Markdown pages in the paper help window."""
from pathlib import Path
import re

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImageReader, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextBrowser


class ProjectHelpBrowser(QTextBrowser):
    unavailable = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._images = []
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._follow_link)
        self.sourceChanged.connect(self._format_page)

    def _follow_link(self, link):
        url = self.source().resolved(link)
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
            return
        path = Path(url.toLocalFile())
        if not url.isLocalFile() or not path.exists():
            self.unavailable.emit(f"当前安装目录未包含此资料。可在完整项目源码中查看「{path.name}」。")
            return
        if path.is_dir() or path.suffix.lower() in (".png", ".jpg", ".jpeg", ".svg", ".pdf", ".html", ".drawio"):
            if not QDesktopServices.openUrl(url):
                self.unavailable.emit(f"系统无法打开此文件。\n{path}")
            return
        self.setSource(url)

    def _format_page(self, url):
        doc = self.document()
        self._images.clear()
        headings = set()
        block = doc.begin()
        while block.isValid():
            cursor = QTextCursor(block)
            fmt = block.blockFormat()
            if fmt.nonBreakableLines():
                # Let long commands wrap on screen while preserving copied text.
                fmt.setNonBreakableLines(False)
                cursor.setBlockFormat(fmt)
            if fmt.headingLevel() and block.text():
                base = re.sub(r"[^\w -]", "", block.text().lower()).strip().replace(" ", "-")
                slug = base
                suffix = 1
                while slug in headings:
                    slug = f"{base}-{suffix}"
                    suffix += 1
                headings.add(slug)
                anchor = QTextCharFormat()
                anchor.setAnchor(True)
                anchor.setAnchorNames([slug])
                cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(anchor)
            block = block.next()

        image = doc.find("\ufffc")
        while not image.isNull():
            name = image.charFormat().toImageFormat().name()
            source = url.resolved(QUrl(name))
            width = QImageReader(source.toLocalFile()).size().width()
            if width > 0:
                self._images.append((image, width))
            image = doc.find("\ufffc", image)
        self._fit_images()
        if url.fragment():
            self.scrollToAnchor(url.fragment())

    def _fit_images(self):
        available = max(1, self.viewport().width() - 2 * self.document().documentMargin())
        for cursor, natural_width in self._images:
            width = min(natural_width, available)
            fmt = cursor.charFormat().toImageFormat()
            if fmt.width() != width:
                fmt.setWidth(width)
                cursor.setCharFormat(fmt)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_images()
