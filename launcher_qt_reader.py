"""Qt article reader; imported cheaply and run in a dedicated launcher process."""

import io
import os
import queue
import sys
import threading
from html import escape
from pathlib import Path
from urllib.parse import urljoin, urlparse


def scroll_pixels(delta, platform, precise_tk=False):
    """Preserve small touchpad deltas instead of rounding to wheel notches."""
    return float(delta) * (12 if platform == "darwin" and not precise_tk else 60 / 120)


def touchpad_pixels(packed_delta):
    """Tk 8.7/9 stores signed vertical pixels in the low 16 bits of %D."""
    vertical = int(packed_delta) & 0xFFFF
    return vertical - 0x10000 if vertical & 0x8000 else vertical


def install_scroll_capture(root, owner, on_scroll, platform):
    """Capture raw Tk scroll data before class bindings in the modal reader."""
    import tkinter as tk

    tag = "NewsReaderScroll" + str(id(owner))
    precise = bool(root.tk.call("info", "commands", "::tk::PreciseScrollDeltas"))

    def receive(kind, raw):
        if not owner.winfo_exists() or getattr(owner, "_closing_animation", False):
            return "break"
        try:
            if kind == "touchpad":
                pixels = touchpad_pixels(raw)
            elif kind == "button":
                pixels = float(raw)
            else:
                pixels = scroll_pixels(raw, platform, precise)
        except (TypeError, ValueError):
            return "break"
        if pixels:
            on_scroll(pixels)
        return "break"

    command = root.register(receive)
    sequences = []
    for sequence, kind, raw in (
        ("<MouseWheel>", "wheel", "%D"),
        ("<TouchpadScroll>", "touchpad", "%D"),
        ("<Button-4>", "button", "60"),
        ("<Button-5>", "button", "-60"),
    ):
        try:
            root.tk.call("bind", tag, sequence,
                f'if {{[{command} {kind} {raw}] eq "break"}} {{break}}')
            sequences.append(sequence)
        except tk.TclError:
            pass  # Older Tk versions do not know TouchpadScroll.
    widgets = [root]
    for widget in widgets:
        widgets.extend(widget.winfo_children())
        widget.bindtags((tag, *widget.bindtags()))

    def cleanup():
        for widget in widgets:
            try:
                if widget.winfo_exists():
                    widget.bindtags(tuple(item for item in widget.bindtags() if item != tag))
            except tk.TclError:
                pass
        for sequence in sequences:
            root.tk.call("bind", tag, sequence, "")
        root.deletecommand(command)

    return cleanup


def article_html(title, blocks, theme, image_sizes=None, content_width=None):
    image_sizes = image_sizes or {}
    text = theme.get("text", "#e8dfcb")
    accent = theme.get("accent", "#e3c36e")
    surface = theme.get("surface", "#211d1b")
    pieces = [f"<h1>{escape(title)}</h1>"]
    for block in blocks:
        kind = block.get("kind", "p")
        if kind == "h1":
            continue
        if kind == "image":
            source = block.get("url", "")
            if source not in image_sizes:
                continue
            width, height = image_sizes[source]
            if content_width is not None and width > content_width:
                height = max(1, round(height * content_width / width))
                width = content_width
            pieces.append(
                f'<p align="center"><img src="{escape(source, quote=True)}" width="{width}" height="{height}"></p>'
            )
            continue
        tag = kind if kind in ("h2", "h3", "h4", "blockquote", "pre") else "p"
        content = escape(block.get("text", "")).replace("\n", "<br>")
        if kind == "li":
            content = "• " + content
        pieces.append(f"<{tag}>{content}</{tag}>")
        for link in dict.fromkeys(block.get("links", [])):
            if urlparse(link).scheme in ("http", "https"):
                pieces.append(f'<p><a href="{escape(link, quote=True)}">{escape(link)}</a></p>')
    return (
        f"<html><head><style>body {{background:{surface};color:{text};font-size:16px;}} "
        f"h1 {{font-size:28px;color:{accent};}} h2,h3,h4 {{color:{accent};}} "
        f"p {{margin-top:10px;margin-bottom:16px;}} a {{color:{accent};}} "
        "blockquote {margin-left:24px;} </style></head><body>" + "".join(pieces) + "</body></html>"
    )


def filter_hub_articles(articles, game="All games", query=""):
    words = query.casefold().split()
    return [item for item in articles
        if (game == "All games" or game in item.get("games", []))
        and all(word in (item.get("title", "") + " " + item.get("summary", "")).casefold()
            for word in words)]


def news_hub_html(articles, theme):
    accent = theme.get("accent", "#e3c36e")
    muted = theme.get("muted", "#a89d88")
    pieces = []
    for item in articles:
        target = item.get("url", "")
        if urlparse(target).scheme not in ("http", "https"):
            continue
        game = " · ".join(item.get("games", [])) or "World of Warcraft"
        pieces.append(f'<p><font color="{muted}">{escape(game.upper())}</font></p>'
            f'<h2><a href="{escape(target, quote=True)}">{escape(item.get("title", ""))}</a></h2>'
            f'<p>{escape(item.get("summary", "") or "Read the full article.")}</p><hr>')
    return (f'<html><head><style>body {{color:{theme.get("text", "#e8dfcb")};font-size:16px;}} '
        f'a {{color:{accent};text-decoration:none;}} h2 {{font-size:21px;margin-top:6px;}} '
        'p {margin-bottom:16px;} </style></head><body>' + ''.join(pieces) + '</body></html>')


def run_reader(url, theme, cache_dir, fonts_dir, commands=None, embedded=None):
    from shiboken6 import isValid
    # Qt owns its event loop; embedded mode never creates a native desktop window.
    if embedded is not None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QObject, QUrl, Signal, Slot, QTimer, QEvent, QPoint, QPointF, Qt, QRectF, QSize, qInstallMessageHandler
    from PySide6.QtGui import QFont, QFontDatabase, QImage, QTextDocument, QMouseEvent, QWheelEvent, QKeyEvent, QTextOption, QTextCursor, QPainter, QColor, QPolygonF, QFontMetrics
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QGridLayout,
        QButtonGroup,
        QLineEdit,
        QScrollArea,
        QSizePolicy,
        QLabel,
        QMainWindow,
        QPushButton,
        QTextBrowser,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    from launcher_news_services import cached_entry, fetch_article, fetch_reader_image

    previous_message_handler = None
    if embedded is not None:
        def qt_message_handler(kind, context, message):
            # This offscreen backend has no native window size hints to propagate.
            if message == "This plugin does not support propagateSizeHints()":
                return
            if previous_message_handler is not None:
                previous_message_handler(kind, context, message)
            else:
                stream = sys.stderr or sys.__stderr__
                if stream is not None:
                    stream.write(message + "\n")
                    stream.flush()
        previous_message_handler = qInstallMessageHandler(qt_message_handler)

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("WoW Launcher — Article Reader")
    family = app.font().family()
    for font in Path(fonts_dir).glob("*"):
        if font.suffix.lower() in (".ttf", ".otf"):
            identifier = QFontDatabase.addApplicationFont(str(font))
            families = QFontDatabase.applicationFontFamilies(identifier)
            if families and ("friz" in font.name.lower() or "friz" in families[0].lower()):
                family = families[0]
    app.setFont(QFont(family, 11))

    class ClassicButton(QPushButton):
        """Qt equivalent of the launcher's clipped, double-frame StoneButton."""
        def __init__(self, text, article=None):
            super().__init__(text)
            self.article = article
            self.hovered = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if article is not None:
                self.setFixedHeight(76)
                self.setAccessibleName(article.get("title", "News article"))

        def sizeHint(self):
            if self.article is not None:
                return QSize(200, 76)
            return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 30, 38)

        def minimumSizeHint(self):
            return QSize(80, self.sizeHint().height())

        def enterEvent(self, event):
            self.hovered = True
            self.update()
            super().enterEvent(event)

        def leaveEvent(self, event):
            self.hovered = False
            self.update()
            super().leaveEvent(event)

        def news_title_layout(self):
            font = QFont(self.font())
            font.setPointSize(14)
            font.setBold(True)
            metrics = QFontMetrics(font)
            width = max(1, self.width() - 46)
            measured = metrics.boundingRect(QRectF(0, 0, width, 1000).toRect(),
                Qt.TextFlag.TextWordWrap, self.article.get("title", "")).height()
            height = max(metrics.height(), min(metrics.height() * 2, measured))
            summary_font = QFont(self.font())
            summary_font.setPointSize(9)
            summary_font.setBold(False)
            return font, height, summary_font, QFontMetrics(summary_font).height()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if self.article is not None:
                _, title_height, _, summary_height = self.news_title_layout()
                height = 10 + title_height + 5 + summary_height + 10
                if self.height() != height:
                    self.setFixedHeight(height)

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            active = self.isEnabled() and (self.hovered or self.isChecked())
            if self.article is not None:
                self.paint_news_card(painter, active)
                return
            face = theme.get("window", "#100f12") if self.isDown() else theme.get(
                "control_active" if active else "panel", "#473a27" if active else "#211d1b")
            edge = theme.get("accent" if active else "border", "#e3c36e" if active else "#806b42")
            def outline(inset, cut):
                w, h = self.width() - inset, self.height() - inset
                return QPolygonF([QPointF(inset + cut, inset), QPointF(w - cut, inset),
                    QPointF(w, inset + cut), QPointF(w, h - cut), QPointF(w - cut, h),
                    QPointF(inset + cut, h), QPointF(inset, h - cut), QPointF(inset, inset + cut)])
            painter.setBrush(QColor(face))
            painter.setPen(QColor(edge if self.isEnabled() else theme.get("control", "#514333")))
            painter.drawPolygon(outline(2, 5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(theme.get("accent_dark", "#78613c")))
            painter.drawPolygon(outline(5, 3))
            shift = 1 if self.isDown() else 0
            color = theme.get("bright" if active else "accent", "#e3c36e")
            if not self.isEnabled():
                color = theme.get("muted", "#a89d88")
            if self.article is None:
                rect = QRectF(10, 6 + shift, self.width() - 20, self.height() - 12)
                font = QFont(self.font())
                font.setBold(True)
                font.setPointSize(10)
                painter.setFont(font)
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    painter.setPen(QColor("#100f12"))
                    painter.drawText(rect.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, self.text())
                painter.setPen(QColor(color))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
                return

        def paint_news_card(self, painter, active):
            # Match build_news_cards on the main menu: rectangular surface,
            # one thin border, a left accent strip, and a right chevron.
            background = theme.get("control" if active else "surface", "#181614")
            border = theme.get("accent" if active else "control", "#514333")
            painter.fillRect(self.rect(), QColor(background))
            painter.setPen(QColor(border))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(.5, .5, self.width() - 1, self.height() - 1))
            painter.fillRect(QRectF(1, 1, 3, self.height() - 2),
                QColor(theme.get("accent" if active else "accent_dark", "#78613c")))
            width = max(1, self.width() - 46)
            font, title_height, summary_font, summary_height = self.news_title_layout()
            painter.setFont(font)
            painter.setPen(QColor(theme.get("bright", "#fff1c7")))
            painter.drawText(QRectF(14, 10, width, title_height),
                Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, self.article.get("title", ""))
            painter.setFont(summary_font)
            painter.setPen(QColor(theme.get("muted", "#a89d88")))
            summary = self.article.get("summary", "") or "Read the full article."
            painter.drawText(QRectF(14, 15 + title_height, width, summary_height), Qt.AlignmentFlag.AlignLeft,
                painter.fontMetrics().elidedText(" ".join(summary.split()), Qt.TextElideMode.ElideRight, width))
            font.setPointSize(14)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(theme.get("accent" if active else "muted", "#a89d88")))
            painter.drawText(QRectF(self.width() - 27, 0, 18, self.height()),
                Qt.AlignmentFlag.AlignCenter, "›")

    class Bridge(QObject):
        article = Signal(int, object, str)
        image = Signal(int, str, bytes)

    class Document(QTextDocument):
        def loadResource(self, resource_type, name):
            # Resource requests must never perform networking on the GUI thread.
            if resource_type == QTextDocument.ResourceType.ImageResource:
                return QImage()
            return super().loadResource(resource_type, name)

    class Reader(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("WoW Launcher — Article Reader")
            self.resize(1040, 720)
            self.history = []
            self.index = -1
            self.generation = 0
            self.closed = False
            self.result = None
            self.image_sizes = {}
            self.loaded_images = {}
            self.hub_articles = []
            self.hub_failures = {}
            self.hub_active = False
            self.hub_loading = False
            self.hub_game = "All games"
            self.bridge = Bridge(self)
            self.bridge.article.connect(self.received)
            self.bridge.image.connect(self.received_image)
            panel = QWidget()
            self.setCentralWidget(panel)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(24, 18, 24, 18)
            row = QHBoxLayout()
            layout.addLayout(row)
            self.back = ClassicButton("‹ Back")
            self.forward = ClassicButton("Forward ›")
            reload_button = ClassicButton("Reload")
            home_button = ClassicButton("News home")
            close_button = ClassicButton("Close")
            for button in (self.back, self.forward, reload_button, home_button):
                row.addWidget(button)
            row.addStretch()
            row.addWidget(close_button)
            self.hub_controls = QWidget()
            hub_layout = QVBoxLayout(self.hub_controls)
            hub_layout.setContentsMargins(0, 4, 0, 8)
            heading = QHBoxLayout()
            heading.addWidget(QLabel("<b><font size='6'>WORLD OF WARCRAFT NEWS</font></b>"))
            heading.addStretch()
            hub_layout.addLayout(heading)
            self.search = QLineEdit()
            self.search.setPlaceholderText("Search article titles and summaries…")
            self.search.setClearButtonEnabled(True)
            hub_layout.addWidget(self.search)
            self.game_grid = QGridLayout()
            hub_layout.addLayout(self.game_grid)
            self.game_group = QButtonGroup(self)
            self.game_group.setExclusive(True)
            self.game_group.buttonClicked.connect(self.choose_game)
            self.search_timer = QTimer(self)
            self.search_timer.setSingleShot(True)
            self.search_timer.timeout.connect(self.render_hub)
            self.search.textChanged.connect(lambda _text: self.search_timer.start(80))
            layout.addWidget(self.hub_controls)
            self.hub_controls.hide()
            self.browser = QTextBrowser()
            self.browser.setDocument(Document(self.browser))
            self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.browser.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            self.browser.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            self.browser.setOpenLinks(False)
            self.browser.setOpenExternalLinks(False)
            self.browser.document().setDocumentMargin(32)
            self.reflow_timer = QTimer(self)
            self.reflow_timer.setSingleShot(True)
            self.reflow_timer.timeout.connect(self.reflow_article)
            self.last_content_width = 0
            self.browser.viewport().installEventFilter(self)
            layout.addWidget(self.browser)
            self.hub_list = QScrollArea()
            self.hub_list.setWidgetResizable(True)
            self.hub_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.hub_list.setFrameShape(QScrollArea.Shape.NoFrame)
            self.hub_items = QWidget()
            self.hub_items_layout = QVBoxLayout(self.hub_items)
            self.hub_items_layout.setContentsMargins(0, 0, 0, 0)
            self.hub_items_layout.setSpacing(4)
            self.hub_items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            self.hub_list.setWidget(self.hub_items)
            self.hub_list.hide()
            layout.addWidget(self.hub_list)
            self.status = QLabel("Loading article…")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            self.back.clicked.connect(lambda: self.travel(-1))
            self.forward.clicked.connect(lambda: self.travel(1))
            reload_button.clicked.connect(self.reload)
            close_button.clicked.connect(self.close)
            home_button.clicked.connect(self.home)
            self.browser.anchorClicked.connect(
                lambda target: self.navigate(urljoin(getattr(self, "current_url", ""), target.toString()))
            )
            self.setStyleSheet(
                f"QWidget {{background:{theme.get('panel', '#211d1b')};color:{theme.get('text', '#e8dfcb')};}} "
                f"QTextBrowser {{background:{theme.get('surface', '#181614')};border:1px solid {theme.get('control', '#514333')};}} "
                f"QPushButton {{background:{theme.get('control', '#514333')};padding:7px 12px;border-radius:4px;}} "
                f"QPushButton:hover {{background:{theme.get('control_active', '#665644')};}} "
                f"QPushButton:checked {{background:{theme.get('accent_dark', '#665644')};border:1px solid {theme.get('accent', '#e3c36e')};}} "
                f"QLineEdit {{background:{theme.get('surface', '#181614')};padding:9px;border:1px solid {theme.get('control', '#514333')};}} "
                f"QScrollBar:vertical {{background:{theme.get('surface', '#181614')};width:12px;}} "
                f"QScrollBar::handle:vertical {{background:{theme.get('control', '#514333')};min-height:30px;}}"
            )
            self.navigate(url)

        def home(self):
            self.generation += 1
            self.result = None
            self.hub_active = True
            self.hub_controls.show()
            self.browser.hide()
            self.hub_list.show()
            self.back.setEnabled(False)
            self.forward.setEnabled(False)
            self.setWindowTitle("World of Warcraft News — WoW Launcher")
            self.render_hub()
            if commands is not None:
                commands.put("home")

        def reload(self):
            if self.hub_active:
                if commands is not None:
                    commands.put("refresh_home")
            else:
                self.navigate(self.current_url, record=False, force=True)

        def choose_game(self, button):
            self.hub_game = button.text()
            self.render_hub()

        def set_hub_data(self, articles, failures, versions):
            self.hub_articles, self.hub_failures = articles, failures
            self.hub_loading = False
            games = ["All games", *versions]
            if [button.text() for button in self.game_group.buttons()] != games:
                for button in self.game_group.buttons():
                    self.game_group.removeButton(button)
                    self.game_grid.removeWidget(button)
                    button.deleteLater()
                for index, game in enumerate(games):
                    button = ClassicButton(game)
                    button.setCheckable(True)
                    button.setChecked(game == self.hub_game)
                    self.game_group.addButton(button)
                    columns = 3 if self.width() >= 900 else 2
                    self.game_grid.addWidget(button, index // columns, index % columns)
            if self.hub_active:
                self.render_hub()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if hasattr(self, "game_group"):
                columns = 3 if self.width() >= 900 else 2
                for index, button in enumerate(self.game_group.buttons()):
                    self.game_grid.addWidget(button, index // columns, index % columns)

        def render_hub(self):
            if not self.hub_active or self.closed:
                return
            found = filter_hub_articles(self.hub_articles, self.hub_game, self.search.text())
            while self.hub_items_layout.count():
                item = self.hub_items_layout.takeAt(0)
                item.widget().hide()
                item.widget().deleteLater()
            for article in found:
                button = ClassicButton(article.get("title", ""), article=article)
                button.clicked.connect(lambda _checked=False, target=article["url"]: self.navigate(target))
                self.hub_items_layout.addWidget(button)
            if not found:
                message = QLabel("Loading news…" if self.hub_loading else
                    "No articles match your search." if self.search.text().strip() else
                    "Could not refresh news. Try Reload." if (self.hub_failures.get(self.hub_game)
                        or self.hub_game == "All games" and self.hub_failures) else
                    "No recent articles for this game.")
                message.setWordWrap(True)
                message.setContentsMargins(16, 20, 16, 20)
                self.hub_items_layout.addWidget(message)
            self.status.setText(f"{len(found)} article{'s' if len(found) != 1 else ''} · {self.hub_game}" +
                (" · Updating…" if self.hub_loading else " · Select a headline to read"))
            self.hub_list.verticalScrollBar().setValue(0)

        def travel(self, direction):
            index = self.index + direction
            if index < 0 and self.hub_articles:
                self.home()
                return
            if 0 <= index < len(self.history):
                self.index = index
                self.navigate(self.history[index], record=False)

        def navigate(self, target, record=True, force=False):
            if target == "launcher:news":
                self.home()
                return
            if urlparse(target).scheme not in ("http", "https"):
                self.status.setText("This link is not an article URL.")
                return
            self.hub_active = False
            self.hub_controls.hide()
            self.hub_list.hide()
            self.browser.show()
            if record:
                del self.history[self.index + 1 :]
                self.history.append(target)
                self.index = len(self.history) - 1
            self.current_url = target
            self.generation += 1
            generation = self.generation
            self.result = None
            self.image_sizes.clear()
            self.loaded_images.clear()
            self.browser.setPlainText("Loading article…")
            self.status.setText("Loading article…")
            self.back.setEnabled(self.index > 0 or bool(self.hub_articles))
            self.forward.setEnabled(self.index < len(self.history) - 1)
            width = max(200, min(900, self.width() - 140))

            def load():
                try:
                    cached = cached_entry(cache_dir, "article", target)
                    if cached and not self.closed:
                        self.bridge.article.emit(generation, cached["data"], "Saved article • Updating…")
                    result, warning = fetch_article(target, cache_dir, force=force)
                    if self.closed:
                        return
                    self.bridge.article.emit(
                        generation,
                        result,
                        "Saved copy • Refresh unavailable" if warning else "Article reader",
                    )
                    # Decode and scale images off the Qt event loop.
                    seen = set()
                    for block in result[2]:
                        source = block.get("url") if block.get("kind") == "image" else None
                        if not source or source in seen:
                            continue
                        seen.add(source)
                        if len(seen) > 24 or self.closed or generation != self.generation:
                            break
                        try:
                            image = fetch_reader_image(source, cache_dir, width)
                            from PIL import Image

                            flat = Image.new("RGB", image.size, theme.get("surface", "#211d1b"))
                            flat.paste(image, (0, 0), image.getchannel("A"))
                            output = io.BytesIO()
                            flat.save(output, format="PNG")
                            self.bridge.image.emit(generation, source, output.getvalue())
                        except Exception:
                            continue  # Failed images stay hidden.
                except Exception as error:
                    if not self.closed:
                        self.bridge.article.emit(generation, None, str(error))

            threading.Thread(target=load, daemon=True).start()

        @Slot(int, object, str)
        def received(self, generation, result, message):
            if generation != self.generation or self.closed:
                return
            self.status.setText(message)
            if result is None:
                self.browser.setPlainText("Could not load article\n\n" + message)
                return
            if self.result == result:
                return
            self.result = result
            self.current_url = result[0]
            self.history[self.index] = result[0]
            self.setWindowTitle(result[1] + " — WoW Launcher")
            self.paint_article()

        def eventFilter(self, watched, event):
            if watched is self.browser.viewport() and event.type() == QEvent.Type.Resize:
                if self.content_width() != self.last_content_width:
                    self.reflow_timer.start(60)
            return super().eventFilter(watched, event)

        def content_width(self):
            # Reserve the document margins and a little room for paragraph indentation.
            return max(1, self.browser.viewport().width() - 2 * int(self.browser.document().documentMargin()) - 32)

        def reflow_article(self):
            if (self.result is not None and not self.closed
                    and self.content_width() != self.last_content_width):
                position = self.browser.verticalScrollBar().value()
                self.paint_article()
                self.browser.verticalScrollBar().setValue(position)

        def paint_article(self):
            self.last_content_width = self.content_width()
            self.browser.setHtml(article_html(self.result[1], self.result[2], theme,
                self.image_sizes, self.last_content_width))
            # Qt marks <pre> blocks nonbreaking by default; allow those to wrap too.
            block = self.browser.document().begin()
            while block.isValid():
                cursor = QTextCursor(block)
                formatting = block.blockFormat()
                formatting.setNonBreakableLines(False)
                cursor.setBlockFormat(formatting)
                block = block.next()
            for source, image in self.loaded_images.items():
                self.browser.document().addResource(
                    QTextDocument.ResourceType.ImageResource, QUrl(source), image
                )
            self.browser.document().markContentsDirty(0, self.browser.document().characterCount())

        @Slot(int, str, bytes)
        def received_image(self, generation, source, data):
            if generation != self.generation or self.closed or self.result is None:
                return
            image = QImage.fromData(data)
            if image.isNull():
                return
            self.loaded_images[source] = image
            self.image_sizes[source] = (image.width(), image.height())
            position = self.browser.verticalScrollBar().value()
            self.paint_article()
            self.browser.verticalScrollBar().setValue(position)

        def closeEvent(self, event):
            self.closed = True
            self.generation += 1
            event.accept()

    window = Reader()
    if embedded is not None:
        inputs, frames = embedded

        class Surface(QObject):
            def __init__(self):
                super().__init__(window)
                self.dirty = True
                self.capturing = False
                self.pressed = None
                self.hovered = None
                self.buttons = Qt.MouseButton.NoButton
                self.scroll_remainder = 0.0
                app.installEventFilter(self)
                self.timer = QTimer(self)
                self.timer.timeout.connect(self.tick)
                self.timer.start(40)

            def eventFilter(self, watched, event):
                if event.type() in (QEvent.Type.DeferredDelete, QEvent.Type.Destroy):
                    if watched is self.hovered:
                        self.hovered = None
                    if watched is self.pressed:
                        self.pressed = None
                        self.buttons = Qt.MouseButton.NoButton
                if not self.capturing and event.type() in (
                    QEvent.Type.Paint, QEvent.Type.Resize, QEvent.Type.LayoutRequest,
                ):
                    self.dirty = True
                return False

            def clear_deleted_targets(self):
                # A Python wrapper can outlive the underlying C++ widget after
                # a loading label/article button is replaced during a refresh.
                if self.hovered is not None and not isValid(self.hovered):
                    self.hovered = None
                if self.pressed is not None and not isValid(self.pressed):
                    self.pressed = None
                    self.buttons = Qt.MouseButton.NoButton

            def tick(self):
                self.clear_deleted_targets()
                for _ in range(100):
                    try:
                        event = inputs.get_nowait()
                    except queue.Empty:
                        break
                    kind = event[0]
                    if kind == "close":
                        window.close()
                        return
                    if kind == "navigate":
                        window.navigate(event[1])
                    elif kind == "hub_data":
                        window.set_hub_data(*event[1:])
                    elif kind == "hub_loading":
                        window.hub_loading = True
                        if event[1]:
                            window.set_hub_data(window.hub_articles, window.hub_failures, event[1])
                            window.hub_loading = True
                        window.render_hub()
                    elif kind == "resize":
                        window.resize(max(100, event[1]), max(100, event[2]))
                    elif kind == "scroll_pixels":
                        self.scroll_remainder += event[1]
                        pixels = int(self.scroll_remainder)
                        self.scroll_remainder -= pixels
                        scrollbar = (window.hub_list if window.hub_active else window.browser).verticalScrollBar()
                        scrollbar.setValue(scrollbar.value() - pixels)
                        self.dirty = True
                    elif kind in ("move", "press", "release", "wheel"):
                        self.clear_deleted_targets()
                        point = QPoint(event[1], event[2])
                        target = self.pressed or window.childAt(point) or window
                        local = QPointF(target.mapFrom(window, point))
                        if kind == "move" and target is not self.hovered:
                            from PySide6.QtGui import QEnterEvent
                            if self.hovered is not None:
                                app.sendEvent(self.hovered, QEvent(QEvent.Type.Leave))
                            self.hovered = target
                            app.sendEvent(target, QEnterEvent(local, QPointF(point), QPointF(point)))
                        if kind == "wheel":
                            target = (window.hub_list if window.hub_active else window.browser).viewport()
                            local = QPointF(target.mapFrom(window, point))
                            message = QWheelEvent(local, local, QPoint(), QPoint(0, event[3]),
                                self.buttons, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
                        else:
                            button = Qt.MouseButton.NoButton
                            event_type = QEvent.Type.MouseMove
                            if kind == "press":
                                self.pressed = target
                                target.setFocus(Qt.FocusReason.MouseFocusReason)
                                button = self.buttons = Qt.MouseButton.LeftButton
                                event_type = QEvent.Type.MouseButtonPress
                            elif kind == "release":
                                button = Qt.MouseButton.LeftButton
                                self.buttons = Qt.MouseButton.NoButton
                                event_type = QEvent.Type.MouseButtonRelease
                            message = QMouseEvent(event_type, local, local, button, self.buttons,
                                Qt.KeyboardModifier.NoModifier)
                        app.sendEvent(target, message)
                        if kind == "release":
                            self.pressed = None
                        self.dirty = True
                    elif kind == "key":
                        keys = {"Tab": Qt.Key.Key_Tab, "Return": Qt.Key.Key_Return,
                            "BackSpace": Qt.Key.Key_Backspace, "Delete": Qt.Key.Key_Delete,
                            "Left": Qt.Key.Key_Left, "Right": Qt.Key.Key_Right,
                            "space": Qt.Key.Key_Space, "Up": Qt.Key.Key_Up, "Down": Qt.Key.Key_Down,
                            "Prior": Qt.Key.Key_PageUp, "Next": Qt.Key.Key_PageDown,
                            "Home": Qt.Key.Key_Home, "End": Qt.Key.Key_End,
                            "Escape": Qt.Key.Key_Escape}
                        key = keys.get(event[1], ord(event[2].upper()) if len(event[2]) == 1 else 0)
                        modifiers = Qt.KeyboardModifier.NoModifier
                        if event[3] & 1:
                            modifiers |= Qt.KeyboardModifier.ShiftModifier
                        if event[3] & 4:
                            modifiers |= Qt.KeyboardModifier.ControlModifier
                        target = app.focusWidget() or window.browser
                        for event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                            app.sendEvent(target, QKeyEvent(event_type, key, modifiers, event[2]))
                        self.dirty = True
                if window.closed:
                    return
                if self.dirty:
                    self.capturing = True
                    try:
                        image = window.grab().toImage().convertToFormat(QImage.Format.Format_RGB888)
                        self.clear_deleted_targets()
                        frame = ("frame", image.width(), image.height(), image.bytesPerLine(),
                            bytes(image.constBits()), (self.hovered or window).cursor().shape().value)
                        try:
                            frames.put_nowait(frame)
                            self.dirty = False
                        except queue.Full:
                            pass  # Backpressure: keep only a bounded number of full frames.
                    finally:
                        self.capturing = False

        surface = Surface()
    window.show()
    app.exec()
    if embedded is not None:
        surface.timer.stop()
        app.removeEventFilter(surface)
        if commands is not None:
            commands.put("closed")
        frames.cancel_join_thread()
        qInstallMessageHandler(previous_message_handler)
