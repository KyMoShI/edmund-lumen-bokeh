"""Independent background, scenery and structural layers for the welcome page."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

from workspace_background import WorkspaceBackground


class WelcomeScene:
    def __init__(self, assets):
        self.assets=Path(assets)
        self.data=json.loads((self.assets/'scene.json').read_text(encoding='utf-8'))
        self.background=WorkspaceBackground(self.assets.parent/'workspace')
        self.scenery=QPixmap(str(self.assets/self.data['scenery']['file']))
        if self.scenery.isNull():
            raise RuntimeError('欢迎页场景图层资源不完整。')

    def geometry(self, viewport):
        viewport=QRectF(viewport)
        width,height=map(float,self.data['artboard'])
        ui_scale=min(viewport.width()/width,viewport.height()/height)
        scene_scale=viewport.height()/height
        crop=self.data['scenery']['sourceCrop']
        # The red scene and circles form one geometric group. A narrow page
        # can crop its sides while its bottom stays related to the brand row.
        bottom=max(crop[1]+crop[3],
                   *(item['center'][1]+item['radius']+self.data['circleStroke']['width']/2
                     for item in self.data['circles']))
        origin=QPointF((viewport.width()-width*scene_scale)/2,
                      viewport.height()-(height-bottom)*ui_scale-bottom*scene_scale)
        return ui_scale,scene_scale,origin

    def paint(self,painter,viewport):
        viewport=QRectF(viewport)
        if viewport.isEmpty():return
        ui_scale,scene_scale,origin=self.geometry(viewport)
        self.background.scale=ui_scale
        self.background.paint(painter,viewport,layers=('paper','waves'),soft_wave_overlap=True)
        painter.save()
        painter.setClipRect(viewport,Qt.ClipOperation.IntersectClip)
        painter.translate(viewport.topLeft())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        stroke=self.data['circleStroke']
        painter.setPen(QPen(QColor(stroke['color']),float(stroke['width'])*scene_scale))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for circle in self.data['circles']:
            cx,cy=circle['center'];radius=float(circle['radius'])*scene_scale
            center=origin+QPointF(cx*scene_scale,cy*scene_scale)
            painter.drawEllipse(center,radius,radius)
        x,y,width,height=map(float,self.data['scenery']['sourceCrop'])
        destination=QRectF(origin.x()+x*scene_scale,origin.y()+y*scene_scale,
                           width*scene_scale,height*scene_scale)
        painter.drawPixmap(destination,self.scenery,QRectF(self.scenery.rect()))
        painter.restore()
        # The source also puts its bottom gradient and structural lines above
        # the scenery. Their reference scale follows the UI, not its crop.
        self.background.paint(painter,viewport,layers=('bottom','structure'))
