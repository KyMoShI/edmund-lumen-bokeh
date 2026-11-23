"""Adjustable workspace preview partitions without reparenting the canvases.

The first layout is the source artboard's exact normalized arrangement. Moving
a divider changes one ratio while retaining the source gutters and small depth
preview insets. Floating previews are represented by their current placeholder;
their pixmaps, zoom and pan remain owned by the existing canvas instances.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class _Divider(QWidget):
    """A gutter-only splitter driven by global coordinates during dragging."""

    def __init__(self, controller, axis):
        super().__init__(controller.page)
        self.controller, self.axis = controller, axis
        self.setObjectName('PreviewDivider_' + axis)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setCursor(Qt.CursorShape.SplitHCursor if axis == 'horizontal'
                       else Qt.CursorShape.SplitVCursor)
        name = '原图与右侧预览宽度' if axis == 'horizontal' else '效果图与深度图高度'
        self.setAccessibleName('调整' + name)
        self.setToolTip('拖动调整' + name + '；双击或 Home 恢复默认比例。方向键微调，Shift 加速。')
        self._hover = False
        self._dragging = False
        self._keyboard_dirty = False
        self._drag_origin = 0.0
        self._drag_ratio = 0.0
        self._drag_saved_ratio = 0.0
        self.hide()

    def _coordinate(self, point):
        return point.x() if self.axis == 'horizontal' else point.y()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._dragging = True
        self._drag_origin = self._coordinate(event.globalPosition())
        self._drag_ratio = self.controller.effective_ratio(self.axis)
        self._drag_saved_ratio = self.controller.ratios[self.axis]
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            super().mouseMoveEvent(event)
            return
        delta = self._coordinate(event.globalPosition()) - self._drag_origin
        available = max(1.0, self.controller.available[self.axis])
        self.controller.set_ratio(self.axis, self._drag_ratio + delta / available)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.controller.persist()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.controller.reset(self.axis)
            self.update()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._dragging:
            self._dragging = False
            self.controller.set_ratio(self.axis, self._drag_saved_ratio)
            self.update()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Home:
            self.controller.reset(self.axis, persist=False)
            self._keyboard_dirty = True
            event.accept()
            return
        negative, positive = ((Qt.Key.Key_Left, Qt.Key.Key_Right)
                              if self.axis == 'horizontal'
                              else (Qt.Key.Key_Up, Qt.Key.Key_Down))
        if event.key() in (negative, positive):
            pixels = 24 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 8
            direction = -1 if event.key() == negative else 1
            available = max(1.0, self.controller.available[self.axis])
            ratio = self.controller.effective_ratio(self.axis) + direction * pixels / available
            self.controller.set_ratio(self.axis, ratio)
            self._keyboard_dirty = True
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._keyboard_dirty and not event.isAutoRepeat():
            self._keyboard_dirty = False
            self.controller.persist()
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        if self._keyboard_dirty:
            self._keyboard_dirty = False
            self.controller.persist()
        super().focusOutEvent(event)
        self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def hideEvent(self, event):
        if self._dragging or self._keyboard_dirty:
            self._dragging = self._keyboard_dirty = False
            self.controller.persist()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self._hover or self.hasFocus() or self._dragging
        center = QPointF(self.rect().center())
        horizontal = self.axis == 'horizontal'
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#EDF0FF'))
            if horizontal:
                bounds = QRectF(.5, center.y()-28, max(1, self.width()-1), 56)
            else:
                bounds = QRectF(center.x()-28, .5, 56, max(1, self.height()-1))
            painter.drawRoundedRect(bounds, 3, 3)
        painter.setPen(QPen(QColor('#001EFF' if active else '#C6CCE0'), 1.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if horizontal:
            painter.drawLine(QPointF(center.x(), center.y()-17),
                             QPointF(center.x(), center.y()+17))
        else:
            painter.drawLine(QPointF(center.x()-17, center.y()),
                             QPointF(center.x()+17, center.y()))


class PreviewLayout:
    """Lay out and persist the two independent workspace split ratios.

    ``horizontal`` means the original-versus-right-column width ratio.
    ``vertical`` means the effect-versus-depth height ratio. The handles are
    ordinary page children; the caller should raise its primary actions after
    ``relayout`` so they retain the workspace's existing foreground order.
    """

    KEYS = {'horizontal': 'workspace/previewHorizontalRatio',
            'vertical': 'workspace/previewVerticalRatio'}

    def __init__(self, page, *, scale=1/3):
        self.page = page
        self.scale = float(scale)
        self.source_panel = self._source_rect(page.data['panels']['preview']['rect'])
        self.source_previews = {
            name: self._source_rect(data['rect']) for name, data in page.data['previews'].items()
        }
        original, effect, depth = (self.source_previews[name]
                                   for name in ('original', 'effect', 'depth'))
        self.default_horizontal_ratio = original.width() / (original.width()+effect.width())
        self.default_vertical_ratio = effect.height() / (effect.height()+depth.height())
        self.horizontal_ratio = self._read('horizontal', self.default_horizontal_ratio)
        self.vertical_ratio = self._read('vertical', self.default_vertical_ratio)
        self.effective_horizontal_ratio = self.horizontal_ratio
        self.effective_vertical_ratio = self.vertical_ratio
        self.available = {'horizontal': 1.0, 'vertical': 1.0}
        self.bounds = {'horizontal': (0.0, 1.0), 'vertical': (0.0, 1.0)}
        self.handles = {axis: _Divider(self, axis) for axis in self.KEYS}
        self.rects = {}
        self._viewport = QRectF()

    @property
    def ratios(self):
        return {'horizontal': self.horizontal_ratio, 'vertical': self.vertical_ratio}

    def _source_rect(self, values):
        return QRectF(*(float(value)*self.scale for value in values))

    def _read(self, axis, default):
        try:
            value = float(self.page.owner._store().value(self.KEYS[axis], default))
        except (TypeError, ValueError, OverflowError):
            return default
        return value if math.isfinite(value) and 0 < value < 1 else default

    def persist(self):
        store = self.page.owner._store()
        for axis, value in self.ratios.items():
            store.setValue(self.KEYS[axis], value)
        store.sync()

    def effective_ratio(self, axis):
        return (self.effective_horizontal_ratio if axis == 'horizontal'
                else self.effective_vertical_ratio)

    def set_ratio(self, axis, value, *, persist=False):
        if axis not in self.KEYS:
            raise ValueError('Unknown preview split axis: ' + str(axis))
        value = float(value)
        if not math.isfinite(value):
            return
        lower, upper = self.bounds[axis]
        value = max(lower, min(upper, value))
        if axis == 'horizontal':
            self.horizontal_ratio = value
        else:
            self.vertical_ratio = value
        if not self._viewport.isEmpty():
            self.relayout(self._viewport)
        if persist:
            self.persist()

    def reset(self, axis=None, *, persist=True):
        if axis not in (None, 'horizontal', 'vertical'):
            raise ValueError('Unknown preview split axis: ' + str(axis))
        if axis in (None, 'horizontal'):
            self.horizontal_ratio = self.default_horizontal_ratio
        if axis in (None, 'vertical'):
            self.vertical_ratio = self.default_vertical_ratio
        if not self._viewport.isEmpty():
            self.relayout(self._viewport)
        if persist:
            self.persist()

    def _mapped_source_rects(self, viewport):
        base = self.source_panel
        sx, sy = viewport.width()/base.width(), viewport.height()/base.height()
        return {
            name: QRectF(viewport.x()+(source.x()-base.x())*sx,
                         viewport.y()+(source.y()-base.y())*sy,
                         source.width()*sx, source.height()*sy)
            for name, source in self.source_previews.items()
        }

    def relayout(self, preview_rect):
        self._viewport = QRectF(preview_rect)
        if self._viewport.isEmpty():
            for handle in self.handles.values():
                handle.hide()
            return
        targets = self._mapped_source_rects(self._viewport)
        original, effect, depth = (targets[name] for name in ('original', 'effect', 'depth'))
        horizontal = original.width()+effect.width()
        vertical = effect.height()+depth.height()
        self.available = {'horizontal': horizontal, 'vertical': vertical}
        min_width, min_height = min(96.0, horizontal*.4), min(80.0, vertical*.4)
        max_left = original.width()+min(effect.width(), depth.width())-min_width
        self.bounds = {'horizontal': (min_width/horizontal, max_left/horizontal),
                       'vertical': (min_height/vertical, 1-min_height/vertical)}
        h_lower, h_upper = self.bounds['horizontal']
        v_lower, v_upper = self.bounds['vertical']
        h_ratio = max(h_lower, min(h_upper, self.horizontal_ratio))
        v_ratio = max(v_lower, min(v_upper, self.vertical_ratio))
        self.effective_horizontal_ratio, self.effective_vertical_ratio = h_ratio, v_ratio
        dx = (h_ratio-self.default_horizontal_ratio)*horizontal
        dy = (v_ratio-self.default_vertical_ratio)*vertical
        if abs(dx) < 1e-10: dx = 0.0
        if abs(dy) < 1e-10: dy = 0.0
        original.setWidth(original.width()+dx)
        effect.setLeft(effect.left()+dx)
        depth.setLeft(depth.left()+dx)
        effect.setHeight(effect.height()+dy)
        depth.setTop(depth.top()+dy)
        self.rects = {name: QRectF(target) for name, target in targets.items()}
        for name, target in targets.items():
            self.page.active_slot(self.page.previews[name]).setGeometry(target.toAlignedRect())
        self._place_handle('horizontal', QRectF(original.right(), original.top(),
                           effect.left()-original.right(), original.height()))
        self._place_handle('vertical', QRectF(effect.left(), effect.bottom(),
                           max(effect.right(), depth.right())-effect.left(),
                           depth.top()-effect.bottom()))

    def _place_handle(self, axis, gutter):
        handle = self.handles[axis]
        if gutter.width() <= 0 or gutter.height() <= 0:
            handle.hide()
            return
        # Use the unpainted gap. Its narrowest dimension has a 6 px minimum
        # hit area on compact windows; the visible grip remains only 1.5 px.
        if axis == 'horizontal' and gutter.width() < 6:
            gutter.adjust(-(6-gutter.width())/2, 0, (6-gutter.width())/2, 0)
        if axis == 'vertical' and gutter.height() < 6:
            gutter.adjust(0, -(6-gutter.height())/2, 0, (6-gutter.height())/2)
        handle.setGeometry(gutter.toAlignedRect())
        handle.show()
        handle.raise_()
