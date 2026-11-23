"""Responsive page-3 background with independent geometric anchors.

The fixed-width structural lines and squares use measured source geometry.
Wave paths retain a uniform scale, while the blue base extends only through
its horizontal middle slice so its sloped ends and vertical gradient survive
changes in viewport aspect ratio. The workspace owns its footer and version.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QLineF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap


class WorkspaceBackground:
    """Load workspace background assets once and paint into any viewport."""

    def __init__(self, assets):
        self.assets = Path(assets)
        self.data = json.loads((self.assets/'background.json').read_text(encoding='utf-8'))
        self.scale = float(self.data['referenceScale'])
        self.geometry = self.data['geometry']
        self.layers = {entry['name']: entry for entry in self.data['layers']}
        self.images = {}
        # Solid structural elements are vector-painted. Their source PNGs are
        # retained as extraction evidence, not allocated as runtime pixmaps.
        for name in ('wave-left', 'wave-right', 'bottom'):
            pixmap = QPixmap(str(self.assets/self.layers[name]['file']))
            if pixmap.isNull():
                raise ValueError(f'Missing workspace background artwork: {name}')
            self.images[name] = pixmap

    def paint(self, painter: QPainter, viewport: QRectF, *, layers=None, soft_wave_overlap=False):
        """Paint selected layers; the default preserves the workspace scene.

        Display pages can insert their independent artwork between waves and
        the bottom/structure without redrawing a flattened background image.
        """
        viewport = QRectF(viewport)
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        painter.save()
        try:
            painter.setClipRect(viewport, Qt.ClipOperation.IntersectClip)
            painter.translate(viewport.topLeft())
            width, height = viewport.width(), viewport.height()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            for layer in (('paper', 'waves', 'bottom', 'structure') if layers is None else layers):
                if layer == 'paper':
                    painter.fillRect(QRectF(0, 0, width, height), QColor(self.data['paperColor']))
                elif layer == 'waves':
                    self._paint_waves(painter, width, height, soft_overlap=soft_wave_overlap)
                elif layer == 'bottom':
                    self._paint_bottom(painter, width, height)
                elif layer == 'structure':
                    self._paint_structure(painter, width, height)
                else:
                    raise ValueError('Unknown background layer: ' + str(layer))
        finally:
            painter.restore()

    def _paint_waves(self, painter, width, height, *, soft_overlap=False):
        wave = self.geometry['waves']
        scale = height/float(self.data['artboard'][1])
        band = float(self.geometry['leftBand']['width'])*self.scale
        split = float(wave['sourceSplitX'])
        left_origin = band-float(wave['leftContentInset'])*scale
        gap = width-float(self.data['artboard'][0])*scale-left_origin
        threshold = float(wave['innerEdgeFadeGapThresholdLogical'])
        # A small change around the reference size must not cause a sudden
        # fade. Open a soft edge only when the two halves separate visibly.
        fade = min(float(wave['innerEdgeFadeDesignWidth'])*scale,
                   max(0.0, (gap-threshold)/2))
        overlap_fade=min(96*scale,max(0.0,(-gap-threshold)/2)) if soft_overlap else 0.0
        for name, side in (('wave-left', 'left'), ('wave-right', 'right')):
            crop = self.layers[name]['sourceCrop']
            x, y, source_width, source_height = map(float, crop)
            target_x = left_origin+x*scale if side == 'left' else width-(4096-x)*scale
            target = QRectF(target_x, y*scale, source_width*scale, source_height*scale)
            clip = QRectF(0 if side == 'left' else width/2, 0, width/2, height)
            painter.save()
            painter.setClipRect(clip, Qt.ClipOperation.IntersectClip)
            if overlap_fade>0:
                self._paint_faded_edge(painter,self.images[name],target,width/2,overlap_fade,side)
            elif fade <= 0:
                painter.drawPixmap(target, self.images[name], QRectF(self.images[name].rect()))
            else:
                edge = left_origin+split*scale if side == 'left' else width-(4096-split)*scale
                self._paint_faded_edge(painter, self.images[name], target, edge, fade, side)
            painter.restore()

    @staticmethod
    def _paint_faded_edge(painter, image, target, edge, fade, side):
        """Feather only newly exposed inner ends, without image-sized buffers."""
        source = QRectF(image.rect())
        factor = source.width()/target.width()
        left = target.left() if side == 'left' else max(target.left(), edge+fade)
        right = min(target.right(), edge-fade) if side == 'left' else target.right()

        def draw(start, end, opacity=1.0):
            start, end = max(start, target.left()), min(end, target.right())
            if end <= start:
                return
            painter.setOpacity(opacity)
            destination = QRectF(start, target.top(), end-start, target.height())
            sample = QRectF((start-target.left())*factor, 0, (end-start)*factor, source.height())
            painter.drawPixmap(destination, image, sample)

        original_opacity = painter.opacity()
        draw(left, right, original_opacity)
        # Source wave alpha is low, so 48 steps remain below a channel level
        # after composition; this avoids two large intermediate RGBA buffers.
        for index in range(48):
            t0, t1 = index/48, (index+1)/48
            if side == 'left':
                start, end = edge-fade+t0*fade, edge-fade+t1*fade
                opacity = 1-(t0+t1)/2
            else:
                start, end = edge+t0*fade, edge+t1*fade
                opacity = (t0+t1)/2
            draw(start, end, opacity*original_opacity)
        painter.setOpacity(original_opacity)

    def _paint_bottom(self, painter, width, height):
        source = self.layers['bottom']['sourceCrop']
        image = self.images['bottom']
        x, y, source_width, source_height = map(float, source)
        first, last = map(float, self.geometry['bottom']['sourceSliceX'])
        left_width, right_width = (first-x)*self.scale, (x+source_width-last)*self.scale
        target_height = source_height*self.scale
        target_top = height-(2160-y)*self.scale
        # Normal editor sizes exceed the combined cap width. Tiny thumbnail
        # viewports retain their slopes by clipping, never squeezing the caps.
        painter.drawPixmap(QRectF(0, target_top, left_width, target_height), image,
                           QRectF(0, 0, first-x, source_height))
        middle_width = width-left_width-right_width
        if middle_width > 0:
            painter.drawPixmap(QRectF(left_width, target_top, middle_width, target_height), image,
                               QRectF(first-x, 0, last-first, source_height))
        painter.drawPixmap(QRectF(width-right_width, target_top, right_width, target_height), image,
                           QRectF(last-x, 0, x+source_width-last, source_height))

    def _paint_structure(self, painter, width, height):
        scale = self.scale
        band = self.geometry['leftBand']
        painter.fillRect(QRectF(0, 0, float(band['width'])*scale, height), QColor(band['color']))
        guides = self.geometry['guides']
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(guides['color']), float(guides['strokeWidth'])*scale))
        horizontal, vertical = guides['horizontal'], guides['vertical']
        start, end = float(horizontal['left'])*scale, width-float(horizontal['rightInset'])*scale
        if end > start:
            top = float(horizontal['top'])*scale
            painter.drawLine(QLineF(start, top, end, top))
        left, top = float(vertical['left'])*scale, float(vertical['top'])*scale
        painter.drawLine(QLineF(left, top, left, top+float(vertical['length'])*scale))
        squares = self.geometry['squares']
        size, top = float(squares['size'])*scale, float(squares['top'])*scale
        for inset in squares['rightInsets']:
            painter.fillRect(QRectF(width-float(inset)*scale-size, top, size, size), QColor(squares['color']))
