"""Generate standalone explanatory SVG/PNG figures from the current engine.

Run from the project environment. This module is not imported by the app.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'figures'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(ROOT))
# The bundled Python can reuse Qt/OpenCV from an existing project environment.
# Import NumPy/Pillow first so incompatible copies cannot shadow their runtime.
try:
    import cv2  # noqa: F401
except ModuleNotFoundError:
    sys.path.append(str(ROOT / '.venv' / 'Lib' / 'site-packages'))
    import cv2  # noqa: F401

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from app.engine import BokehEngine

BLUE = '#2346E8'
RED = '#C44D39'
GREEN = '#247B64'
INK = '#19273E'
MUTED = '#53647B'
GRAY = '#D5DEED'


class Figure:
    def __init__(self, title, subtitle, height=1060):
        self.height = height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1440" height="{height}" viewBox="0 0 1440 {height}">',
                      '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="none" stroke="#60779E" stroke-width="1.5"/></marker></defs>',
                      f'<rect width="1440" height="{height}" fill="#F6F8FC"/>',
                      '<g font-family="Sarasa Gothic SC">']
        self.text(32, 54, title, 33, INK, True)
        self.text(32, 93, subtitle, 18, MUTED)

    def text(self, x, y, text, size=18, color=INK, bold=False, anchor='start'):
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{700 if bold else 400}" text-anchor="{anchor}">{escape(str(text))}</text>')

    def rect(self, x, y, w, h, fill='#FFFFFF', stroke=GRAY, radius=16):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')

    def box(self, x, y, w, h, title, lines, accent=BLUE):
        self.rect(x, y, w, h)
        self.parts.append(f'<rect x="{x+18}" y="{y+20}" width="5" height="25" rx="2" fill="{accent}"/>')
        self.text(x+36, y+41, title, 23, accent, True)
        for i, line in enumerate(lines):
            self.text(x+24, y+78+i*29, line, 18)

    def arrow(self, points):
        value = ' '.join(f'{x},{y}' for x,y in points)
        self.parts.append(f'<polyline points="{value}" fill="none" stroke="#60779E" stroke-width="2"/>')
        x,y=points[-1]; px,py=points[-2]
        length=max(((x-px)**2+(y-py)**2)**0.5,1e-6)
        ux,uy=(x-px)/length,(y-py)/length
        bx,by=x-ux*12,y-uy*12
        triangle=f'{x},{y} {bx-uy*6},{by+ux*6} {bx+uy*6},{by-ux*6}'
        self.parts.append(f'<polygon points="{triangle}" fill="#60779E"/>')

    def note(self, lines):
        for i,line in enumerate(lines):
            self.text(32, self.height-61+i*27, line, 17, MUTED)

    def save(self, stem):
        self.parts.append('</g></svg>')
        path = OUT / f'{stem}.svg'
        path.write_text('\n'.join(self.parts), encoding='utf-8')
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            raise ValueError(f'Invalid SVG: {path}')
        image = QImage(1440, self.height, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        if not image.save(str(OUT / f'{stem}.png')):
            raise OSError(f'Could not save {stem}.png')


def overview():
    f = Figure('01  从单张图片到散景效果', '当前源码流程 · 原图、相对深度与参数共同决定模拟结果')
    for x,title,lines in [
        (32,'图像输入',['解码图像、校正 EXIF 方向','生成预览或导出所需的目标尺寸']),
        (512,'深度输入',['AI / 导入深度图','无深度或选择中心：使用径向近似']),
        (992,'参数输入',['光圈、焦点、虚化与光斑参数','主体保护、边缘及输出设置'])]:
        f.box(x,135,416,125,title,lines)
        f.arrow([(x+208,260),(x+208,310)])
    f.box(32,310,1376,135,'共同准备：失焦分布与主体边界',[
        '深度对齐与归一化 → 计算 CoC（失焦量）→ 焦内软掩膜 → 边缘扩张 / 收缩与羽化',
        '保留目标尺寸原图和焦内掩膜；各模式分别构建背景工作图与外推底图。'])
    f.arrow([(720,445),(720,480),(360,480),(360,510)])
    f.arrow([(720,480),(1080,480),(1080,510)])
    f.box(32,510,656,260,'普通光斑模式',[
        '在编码图像上建立最长边不超过 1000 的背景工作图',
        '按背景有效像素外推被遮挡区域，构建背景底图',
        '按光圈形状生成三档 PSF，使用 FFT 卷积',
        '按失焦量混合各档背景结果',
        '恢复目标尺寸，再叠加独立的局部高光层',
        '独立高光层可使用 1× / 2× / 3× 超采样'])
    f.box(752,510,656,260,'高性能光斑模式',[
        '先解码为线性光，再缩小到背景工作分辨率',
        '背景外推 → 反光源估算 → 离焦与视场采样',
        '以宽谱波前 PSF 扩散所有有效源分布',
        '归一化颜色及覆盖权重，分别生成焦前 / 焦后层',
        '该分支没有独立高光覆盖层'], GREEN)
    f.arrow([(360,770),(360,840)])
    f.arrow([(1080,770),(1080,840)])
    f.box(32,840,1376,130,'合成与输出',[
        '恢复目标输出尺寸，保留原分辨率的焦内图像；高性能分支编码各层后按覆盖率合成。',
        '输出合成图，或输出颜色层与 alpha 一致的透明效果 PNG；预览与导出复用同一引擎。'])
    f.note(['内部背景计算最长边不超过 1000 像素；该限制不等于导出文件的像素尺寸。',
            '当前虚化算子使用 CPU；中心径向深度、单目相对深度及通用波前均属于近似。'])
    f.save('01-pipeline')


def optical():
    f = Figure('02  高性能模式：从源分布到点扩散函数', '局部反光估算与全亮度扩散分开处理，随后进入同一卷积路径')
    f.box(32,140,640,150,'线性背景底图',[
        'sRGB 解码先于区域平均，保留缩小时的亮度贡献',
        '背景外推减少主体颜色向失焦区域泄漏'])
    f.arrow([(352,290),(352,340)])
    f.box(32,340,640,260,'局部紧凑反光源估算',[
        '多尺度局部对比度 → 连通区域 → 尺寸与形状筛选',
        '将部分正残差光量重分布到较小的源足迹',
        '根据输入峰值估算有限的额外亮度',
        '高光补偿抑制额外估算增益，不删除暗部',
        '普通纹理与扩展光源仍保留并参与扩散'])
    f.box(736,140,672,190,'复数光瞳：形状、视场与相位',[
        'A：圆形 / 多边形光圈，与离轴偏移开口求交',
        'φ：有符号离焦相位 + 球差近似相位',
        'P = A · exp(iφ)；此处没有具体镜头处方'], GREEN)
    f.arrow([(1072,330),(1072,390)])
    f.box(736,390,672,210,'宽谱 PSF：傅里叶强度响应',[
        '对 0.85 / 1.00 / 1.17 三个相对波长分别计算',
        'PSFλ = |DFT(Pλ)|²，重采样到同一坐标',
        '权重 0.25 / 0.50 / 0.25；核总和归一化为 1',
        '离轴核质心回正，再缩放到目标核半径'], GREEN)
    f.arrow([(352,600),(352,670)])
    f.arrow([(1072,600),(1072,670)])
    f.box(32,670,1376,220,'按离焦档位与视场权重分配源贡献',[
        '焦内直通 + 焦后 6 档 + 焦前 6 档；相邻档位采用三角权重插值。',
        '猫眼关闭时使用轴上核；开启时最高使用 3 × 3 视场采样，按位置权重插值。',
        'RGB 与覆盖权重组成四通道一起卷积；按累计覆盖权重归一化颜色。',
        '只计算有效贡献区域并保留完整核边界；缓存生成的波前核。'])
    f.note(['核归一化控制扩散分布；额外反光亮度估算使整个流程不能表述为严格光量守恒。',
            '这是基于图像和相对深度的近似，没有逐面光线追迹、镜头透过率标定或完整光谱响应。'])
    f.save('02-wavefront')


def depth_composition():
    f = Figure('03  有符号离焦、层间覆盖与透明输出', '以下图示对应已有深度图时的分层；中心模式只产生一侧径向失焦')
    f.rect(32,140,656,430)
    f.text(56,182,'离焦档位的三角权重',23,BLUE,True)
    f.text(56,218,'t = 6c；wₖ = max(1 − |t − k|, 0)',19)
    left,top,width,height = 78,265,564,210
    def xy(t,weight):
        return left+(t+6)/12*width, top+height*(1-weight)
    f.parts.append(f'<path d="M{left},{top-12} V{top+height} H{left+width+8}" fill="none" stroke="#7186AB" stroke-width="1.5"/>')
    for k in range(-6,7):
        color = GREEN if k == 0 else BLUE if k < 0 else RED
        pts=[xy(t,max(1-abs(t-k),0)) for t in np.linspace(max(-6,k-1),min(6,k+1),25)]
        f.parts.append('<polyline points="'+' '.join(f'{x:.2f},{y:.2f}' for x,y in pts)+f'" fill="none" stroke="{color}" stroke-width="2.2"/>')
    for t in (-6,-3,0,3,6):
        x,y=xy(t,0)
        f.text(x,y+28,str(t),16,MUTED,anchor='middle')
    f.text(56,548,'负侧：焦后层       0：焦内直通       正侧：焦前层',17,MUTED)
    f.box(752,140,656,430,'深度与符号约定',[
        '有效深度图会对齐尺寸，并再次归一化。',
        'd 越大表示越近；c 的正负来自 d − focus。',
        'c 的绝对值由景深、光圈及虚化强度共同决定。',
        '例如 c = +0.27：t = +1.62。',
        '源贡献分到第 1、2 档，权重分别为 0.38、0.62。',
        '核函数相位的 defocus_sign = −sign(c)，',
        '它与深度分层使用的符号不可直接混称。'],GREEN)
    for x,title,lines,color in [
        (32,'焦后背景 C_far',['颜色 ÷ 累计覆盖权重','不足覆盖时回退至输入底图'],BLUE),
        (512,'焦内图像 I × S',['保留目标尺寸的原图细节','S 来自 CoC 与主体边缘设置'],GREEN),
        (992,'焦前层 C_near / A',['扩散颜色与覆盖率一起生成','主体保护可约束前层覆盖'],RED)]:
        f.box(x,630,416,135,title,lines,color)
        f.arrow([(x+208,765),(x+208,825)])
    f.box(32,825,1376,150,'最终混合与透明效果层',[
        '先将各失焦颜色层编码回 sRGB，再按焦内掩膜与焦前覆盖率混合原图。',
        'F = S(1 − A)，效果层 alpha = 1 − F；效果颜色采用相同的预乘贡献恢复。',
        '普通模式的效果 alpha = 1 − S；两条分支都可以返回合成图及透明图层。'])
    f.note(['图中的权重是源码离焦插值示意，不是标定物距或毫米单位的弥散圆。',
            '卷积和源贡献叠加在线性光空间；最终焦内 / 前后层混合位于编码空间。'])
    f.save('03-depth-composition')


def kernel_samples():
    cases=[
        ('圆形 · 轴上',0,0.0,0.0,0.0,1),
        ('六边形 · 轴上',6,0.0,0.0,0.0,1),
        ('圆形 · 离轴开口裁切',0,0.0,0.65,0.0,1),
        ('球差 +1 · 相位离焦 +1',0,1.0,0.0,0.0,1),
        ('球差 +1 · 相位离焦 −1',0,1.0,0.0,0.0,-1),
        ('球差 −1 · 相位离焦 +1',0,-1.0,0.0,0.0,1),
    ]
    kernels=[BokehEngine._wavefront_kernel(42,blades,spherical,fx,fy,sign) for _,blades,spherical,fx,fy,sign in cases]
    peak=max(float(k.max()) for k in kernels)
    f=Figure('04  引擎实际生成的 PSF 图样','调用 BokehEngine._wavefront_kernel · r = 42 工作像素 · 各核总和为 1，使用统一显示映射',1060)
    manifest=[]
    for index,((title,blades,spherical,fx,fy,sign),kernel) in enumerate(zip(cases,kernels)):
        x,y=32+(index%3)*480,140+(index//3)*405
        f.rect(x,y,416,365)
        f.text(x+20,y+38,title,21,INK,True)
        # One shared intensity scale preserves relative intensity differences.
        value=np.clip(kernel/peak,0,1)**(1/2.2)
        rgb=np.rint(value[...,None]*np.array([206,224,255])).astype(np.uint8)
        buffer=io.BytesIO(); Image.fromarray(rgb).save(buffer,format='PNG')
        encoded=base64.b64encode(buffer.getvalue()).decode('ascii')
        f.rect(x+88,y+59,240,240,fill='#000000',stroke='#22304B',radius=0)
        f.parts.append(f'<image x="{x+88}" y="{y+59}" width="240" height="240" xlink:href="data:image/png;base64,{encoded}"/>')
        f.text(x+20,y+328,f'叶片={blades or "圆形"}，球差={spherical:+.1f}，field=({fx:.2f},{fy:.2f})',15,MUTED)
        f.text(x+20,y+351,f'相位离焦={sign:+d}；sum={float(kernel.sum()):.6f}',15,MUTED)
        assert np.isfinite(kernel).all() and np.min(kernel)>=0
        assert abs(float(kernel.sum())-1)<1e-5
        manifest.append({'label':title,'radius':42,'bladeCount':blades,'spherical':spherical,'fieldX':fx,'fieldY':fy,'defocusSign':sign,'kernelSum':float(kernel.sum()),'peak':float(kernel.max())})
    f.note(['显示映射为 (K / 全部样本共同峰值)^(1/2.2)，蓝白色仅为展示配色，不代表实际光谱颜色。',
            '这是算法核的数值示例；不构成具体镜头、Zemax 工程或拍摄参数的仿真验证。'])
    f.save('04-psf-samples')
    return {'commonDisplayPeak':peak,'samples':manifest}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([])
    for path in sorted((ROOT/'app/assets/fonts').glob('Sarasa*.ttf')):
        QFontDatabase.addApplicationFont(str(path))
    overview(); optical(); depth_composition()
    samples=kernel_samples()
    snapshot={'basis':'current-source-not-lens-calibration','files':{},'psfSamples':samples}
    for relative in ('app/engine.py','app/bokeh_sources.py','app/depth_worker.py','app/main.py'):
        snapshot['files'][relative]=hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
    (OUT.parent/'source-snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    figures=[('01-pipeline','处理总览','图像、深度、两种模式与输出的关系。'),
             ('02-wavefront','高性能扩散','局部反光估算、复数光瞳、宽谱 PSF 及视场插值。'),
             ('03-depth-composition','深度与合成','离焦档位权重、近远层覆盖及透明效果图层。'),
             ('04-psf-samples','实际 PSF 样本','使用引擎函数生成六组可复现的核图样。')]
    sections='\n'.join(f'<section id="{stem}"><h2>{title}</h2><p>{caption} <a href="figures/{stem}.svg">打开矢量图</a></p><a href="figures/{stem}.png"><img src="figures/{stem}.png" alt="{title}"></a></section>' for stem,title,caption in figures)
    nav=' · '.join(f'<a href="#{stem}">{title}</a>' for stem,title,_ in figures)
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>光斑模拟流程图解</title>
<style>body{margin:0;background:#f3f6fc;color:#19273e;font:16px/1.7 "Microsoft YaHei",system-ui,sans-serif}main{max-width:1440px;margin:auto;padding:36px 20px}h1{font-size:32px;margin:0}h2{margin:0}a{color:#2346e8}nav{margin:20px 0}section{background:white;border:1px solid #d5deed;border-radius:16px;padding:22px;margin:28px 0}section p{color:#53647b;margin:8px 0 18px}img{width:100%;height:auto;display:block;border-radius:10px}footer{color:#53647b}@media print{body{background:white}main{padding:0}header,footer{display:none}section{border:0;padding:0;break-after:page;margin:0}section h2,section p{display:none}}</style>
<main><header><h1>光斑模拟流程图解</h1><p>依据当前源码绘制的独立技术介绍。图表可离线浏览；完整步骤、公式和参数作用见同目录 README.md。</p><nav>'''+nav+'''</nav></header>'''+sections+'''<footer>PNG 用于直接阅读，SVG 用于缩放或排版。生成脚本及源码指纹保存在本目录；介绍图不属于软件运行资源。</footer></main></html>'''
    (OUT.parent/'index.html').write_text(html,encoding='utf-8')
    print(f'Generated 4 SVG / 4 PNG figures in {OUT}')
    del app


if __name__=='__main__':
    main()
