"""Offline histogram counts, rendering, shared views and image-state checks."""
from pathlib import Path
import os,sys,tempfile,time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT/'.venv/Lib/site-packages'));sys.path.insert(0,str(ROOT/'app'))
import cv2
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QComboBox
from histogram import HistogramModel,HistogramPlot,HistogramPanel
from design_dialogs import DesignDialog
import main


def check_counts(model,image):
    values={'luminance':cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),
            'red':image[:,:,0],'green':image[:,:,1],'blue':image[:,:,2]}
    for name,plane in values.items():
        expected=np.bincount(plane.ravel(),minlength=256)
        assert np.array_equal(model.counts[name],expected),(name,model.counts[name],expected)
        assert model.counts[name].sum()==image.shape[0]*image.shape[1]


def run():
    app=QApplication.instance() or QApplication([])
    output=ROOT/'tmp/histogram-checks';output.mkdir(parents=True,exist_ok=True)
    model=HistogramModel()
    large=HistogramModel()
    rgba=np.random.default_rng(81).integers(0,256,size=(600,1100,4),dtype=np.uint8)
    large.set_image(rgba);check_counts(large,rgba)
    pixels=np.array([[[255,0,0],[0,255,0],[0,0,255],[0,0,0]],
                     [[255,255,255],[125,60,201],[12,222,1],[37,37,37]]],dtype=np.uint8)
    model.set_image(pixels);check_counts(model,pixels)
    arrays={key:id(value) for key,value in model.counts.items()}
    panel=HistogramPanel(model=model,compact=True,collapsible=False)
    second=HistogramPanel(model=model,collapsible=False)
    panel.resize(267,90);panel.show();second.show();app.processEvents()
    assert all(button.isEnabled() for button in panel.channel_buttons.values())
    assert not panel.findChildren(QComboBox),'Histogram contains a channel dropdown'
    assert all(not button.isChecked() for button in panel.channel_buttons.values())
    for button in panel.channel_buttons.values():
        assert button.grab().toImage().pixelColor(4,13).name()=='#262626'
    for channel in ('red','green','blue'):
        panel.channel_buttons[channel].click();app.processEvents()
        assert model.channel==channel and second.channel_buttons[channel].isChecked()
        assert sum(button.isChecked() for button in panel.channel_buttons.values())==1
        button=panel.channel_buttons[channel]
        assert button.width()==26 and button.height()==26
        assert button.grab().toImage().pixelColor(4,13).name()=='#001eff'
        assert {key:id(value) for key,value in model.counts.items()}==arrays,'Channel change recomputed counts'
        panel.channel_buttons[channel].click();app.processEvents()
        assert model.channel=='luminance'
        assert all(not button.isChecked() for button in panel.channel_buttons.values())
    assert not model.set_channel('rgb'),'Removed RGB overlay is still selectable'
    model.set_image(pixels)
    assert {key:id(value) for key,value in model.counts.items()}==arrays,'Same image was counted again'
    plot=HistogramPlot(model=model);plot.resize(512,180);plot.show();app.processEvents()
    model.set_channel('red');app.processEvents()
    plot.grab().save(str(output/'white-bars.png'))
    with Image.open(output/'white-bars.png') as picture:
        rgb=np.asarray(picture.convert('RGB'),dtype=np.int16)
    assert np.max(np.abs(rgb[:,:,0]-rgb[:,:,1]))<=1 and np.max(np.abs(rgb[:,:,1]-rgb[:,:,2]))<=1
    assert np.any(rgb[:,:,0]>240) and np.any(rgb[:,:,0]==230)
    for empty in (None,np.empty((0,0,3),np.uint8)):
        model.set_image(empty);app.processEvents()
        assert all(not button.isEnabled() for button in panel.channel_buttons.values())
        assert all(not np.any(values) for values in model.counts.values()),'Empty image retained old counts'
    panel.close();second.close();plot.close()
    print('PASS: exact 256-bin RGB/luminance counts including striped RGBA, shared exclusive toggle-off buttons, channel-only cache, gray/white bars and empty data')

    saved_path=main._SETTINGS_PATH;previous_defaults=main.MOTION_DEFAULTS.copy()
    windows=[]
    with tempfile.TemporaryDirectory(prefix='histogram-check-') as folder:
        folder=Path(folder);main._SETTINGS_PATH=folder/'settings.ini';main.MOTION_DEFAULTS['enabled']=False
        try:
            w=main.MainWindow();windows.append(w);w._device_profile=lambda:{}
            w.resize(1366,720);w.show();app.processEvents();w.welcome_page.buttons['start'].click();app.processEvents()
            inline=w.light_workspace.metadata.histogram
            assert inline.model is w.histogram.model and all(not b.isEnabled() for b in inline.channel_buttons.values())
            # A known photo-like pattern is visible only in preview canvases.
            image=np.zeros((120,192,3),np.uint8)
            image[:,:,0]=np.arange(192,dtype=np.uint8)[None,:]
            image[:,:,1]=np.arange(120,dtype=np.uint8)[:,None]
            image[:,:,2]=190
            source=folder/'通道检查.png';Image.fromarray(image).save(source);w.open_image_path(str(source))
            deadline=time.monotonic()+15
            while w.processed is None and time.monotonic()<deadline:app.processEvents();QTest.qWait(10)
            assert w.processed is not None
            w._render_timer.stop();w._invalidate_preview()
            processed=np.zeros_like(image);processed[:]=[210,48,86]
            w.processed=processed;w.refresh_display();app.processEvents()
            shared=w.histogram.model;check_counts(shared,processed)
            before=w._signature();history=w.history
            inline.channel_buttons['green'].click();app.processEvents()
            assert shared.channel=='green' and w.histogram.channel_buttons['green'].isChecked()
            assert w._signature()==before and w.history is history,'Histogram selection changed editing history'
            w.compare_btn.setChecked(True);app.processEvents();check_counts(shared,image)
            w.compare_btn.setChecked(False);w.range_check.setChecked(True);app.processEvents();check_counts(shared,processed)
            assert w.after_canvas._pixmap is not None
            assert '192' in w.light_workspace.metadata.caption and '120' in w.light_workspace.metadata.caption
            assert not w.light_workspace.metadata.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            metadata=w.light_workspace.metadata
            metadata.grab().save(str(output/'metadata.png'))
            with Image.open(output/'metadata.png') as picture:
                band=np.asarray(picture.convert('RGB'))[round(metadata.histogram.geometry().bottom()*metadata.devicePixelRatioF())+2:]
            assert np.any(np.all(band==[128,142,255],axis=2)), 'Missing source metadata band'
            assert np.any(np.all(band==[0,21,121],axis=2)), 'Missing high-contrast metadata text'
            inline.plot.grab().save(str(output/'inline-no-photo.png'))
            with Image.open(output/'inline-no-photo.png') as picture:rgb=np.asarray(picture.convert('RGB'),dtype=np.int16)
            # The channel button is blue; only the actual bar field is neutral.
            bar_field=rgb[round(34*inline.plot.devicePixelRatioF()):]
            assert np.max(np.abs(bar_field[:,:,0]-bar_field[:,:,2]))<=1,'Photo leaked into the histogram plot'
            visited=[]
            def inspect_dialog(dialog):
                dialog.show();app.processEvents()
                panels=dialog.findChildren(HistogramPanel);assert len(panels)==1
                assert panels[0].model is shared
                panels[0].channel_buttons['blue'].click();app.processEvents()
                assert inline.channel_buttons['blue'].isChecked()
                # Subsequent render updates must refresh an already-open dialog.
                newer=np.full_like(processed,[19,230,77]);w.processed=newer;w.refresh_display()
                check_counts(panels[0].model,newer)
                visited.append(dialog.windowTitle());dialog.reject();return QDialog.DialogCode.Rejected
            with patch.object(DesignDialog,'exec',inspect_dialog):w.light_workspace._show_histogram()
            assert visited==['图像直方图']
            controller=next(c for c in w.light_workspace.floating if c.widget is w.light_workspace.parameters_panel)
            controller.detach();app.processEvents();assert inline.model is shared and inline.channel_buttons['blue'].isChecked()
            controller.restore();app.processEvents()
            theme=w._theme_mode
            w.theme_btn.click();app.processEvents();QTest.qWait(50)
            assert w._theme_mode!=theme
            assert w.histogram.model is shared and w.histogram.channel_buttons['blue'].isChecked()
            w.theme_btn.click();app.processEvents();QTest.qWait(50)
            assert w._theme_mode==theme
            empty=w._snapshot()
            for key in ('image_path','original','original_full','depth','depth_full','depth_name'):empty[key]=None
            empty['image_source_signature']=None
            w._restore(empty);app.processEvents()
            assert all(not np.any(values) for values in shared.counts.values())
            assert all(not b.isEnabled() for b in inline.channel_buttons.values())
            assert all(not b.isEnabled() for b in w.histogram.channel_buttons.values())
            assert inline.channel_buttons['blue'].isChecked(),'Clearing image reset channel preference'
            print('PASS: inline histogram replaces photo, source/effect switch, untinted statistics, live dialog sharing, floats/themes and restore-to-empty')
        finally:
            for window in reversed(windows):window.close()
            app.processEvents();main._SETTINGS_PATH=saved_path
            main.MOTION_DEFAULTS.clear();main.MOTION_DEFAULTS.update(previous_defaults)
    print('PASS: histogram checks; no network, Explorer or real user settings used.')


if __name__=='__main__':run()
