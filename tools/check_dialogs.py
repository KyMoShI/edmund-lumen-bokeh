"""Offline UI checks for paper dialogs, completion navigation and long text."""
from pathlib import Path
import os,sys,tempfile
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT/'.venv/Lib/site-packages'));sys.path.insert(0,str(ROOT/'app'))
from PySide6.QtCore import QTimer,Qt,QUrl,QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QDialogButtonBox,QPushButton
from design_dialogs import DesignDialog,MessageDialog,DesignMessageBox
from project_help import ProjectHelpBrowser
import main


def run():
    app=QApplication.instance() or QApplication([])
    out=ROOT/'tmp/dialog-checks';out.mkdir(parents=True,exist_ok=True)
    previous_path=main._SETTINGS_PATH;previous_motion=main.MOTION_DEFAULTS.copy()
    windows=[]
    for group,names in (('welcome',('start','next')),('workspace',('import','export')),('completion',('exit','next'))):
        for name in names:
            pixels=np.asarray(Image.open(ROOT/'app/assets'/group/(name+'.png')).convert('RGBA'),dtype=np.int16)
            blue=(pixels[:,:,2]>pixels[:,:,0]+45)&(pixels[:,:,2]>pixels[:,:,1]+45)&(pixels[:,:,3]>20)
            assert not np.any(blue),(group,name,'blue text outline remains')
    print('PASS: all six English action assets use white text without blue strokes')
    with tempfile.TemporaryDirectory(prefix='bokeh-dialogs-') as folder:
        folder=Path(folder);main._SETTINGS_PATH=folder/'settings.ini';main.MOTION_DEFAULTS['enabled']=False
        try:
            w=main.MainWindow();windows.append(w);w._device_profile=lambda:{}
            w.resize(1366,720);w.show();app.processEvents();w.welcome_page.buttons['start'].click();app.processEvents()
            export=main.ExportDialog(w,1920,1080);windows.append(export);export.show();app.processEvents()
            assert isinstance(export,DesignDialog) and export.body.layout() is not None
            assert export.footer.isVisible() and export.rect().contains(export.footer.mapTo(export,export.footer.rect().bottomRight()))
            assert export.values()['format']=='PNG' and export.values()['quality']==95
            export.type_combo.setCurrentIndex(1);app.processEvents()
            assert export.values()['layer'] and not export.format_combo.isEnabled()
            assert export.layer_note.isVisible() and export.values()['format']=='PNG'
            export.type_combo.setCurrentIndex(0);export.format_combo.setCurrentIndex(export.format_combo.findData('JPEG'))
            assert export.quality_slider.isEnabled()
            export.scale_combo.setCurrentIndex(2);assert '960' in export.size_label.text()
            export.grab().save(str(out/'export-settings.png'));export.close()
            model=main.ModelImportDialog(w);windows.append(model);model.show();app.processEvents()
            model.repo_edit.setText('offline/example');model.name_edit.setText('测试模型')
            assert model.values()==('repo','offline/example','测试模型')
            model.local_edit.setText(str(folder));model._local_edited();assert model.values()[0]=='local'
            model.grab().save(str(out/'model-import.png'));model.close()
            print('PASS: export settings values, layer/format constraints and model inputs')

            captured=[]
            def inspect_exec(dialog):
                dialog.show();app.processEvents();QTest.qWait(20)
                assert isinstance(dialog,DesignDialog)
                captured.append(dialog.windowTitle())
                if dialog.windowTitle()=='项目说明':
                    browser=dialog.findChild(ProjectHelpBrowser)
                    assert browser.source().toLocalFile()==(ROOT/'README.md').as_posix()
                    for width in (800,460,720):
                        dialog.resize(width,530);app.processEvents()
                        image=browser.document().find(chr(0xfffc))
                        line=image.block().layout().lineAt(0)
                        with Image.open(ROOT/'design-system/baoyue-bokeh/pages/workspace-photo.png') as sample:
                            expected_height=line.naturalTextWidth()*sample.height/sample.width
                        assert abs(line.height()-expected_height)<=2
                        assert line.naturalTextWidth()<=browser.viewport().width()
                        assert browser.horizontalScrollBar().maximum()==0
                    browser.anchorClicked.emit(QUrl('docs/README.md'))
                    assert browser.toPlainText().startswith('项目文档') and browser.isBackwardAvailable()
                    browser.anchorClicked.emit(QUrl('../README.md#参数范围与精确输入'))
                    assert browser.verticalScrollBar().value()>0
                    back=next(b for b in dialog.footer.buttons() if b.text()=='上一页')
                    assert back.isVisible()
                    back.click();assert browser.toPlainText().startswith('项目文档')
                    back.click();assert not back.isVisible()
                dialog.grab().save(str(out/('secondary-'+str(len(captured))+'.png')))
                dialog.reject();return QDialog.DialogCode.Rejected
            with patch.object(DesignDialog,'exec',inspect_exec):
                w.show_motion_settings();w.show_readme();w.light_workspace._show_histogram()
            assert captured==['动画设置','项目说明','图像直方图'],captured
            print('PASS: animation/help/histogram share the dialog shell and cancellation semantics')
            # Relative links in the portable build can refer to source-only documentation.
            manual=folder/'README.md';manual.write_text('# 项目说明\n\n[算法图解](docs/guide.md)',encoding='utf-8')
            browser=ProjectHelpBrowser();browser.setSource(QUrl.fromLocalFile(str(manual)))
            notices=[];browser.unavailable.connect(notices.append)
            browser.anchorClicked.emit(QUrl('docs/guide.md'))
            assert browser.toPlainText().startswith('项目说明') and not browser.isBackwardAvailable()
            assert len(notices)==1 and 'guide.md' in notices[0]
            browser.close()
            print('PASS: help screenshots fit on resize, section links and history work, missing bundle links preserve the page')

            text='<故障> & '+('C:/测试文件夹/很长的文件名称/'*45)
            for severity in ('information','warning','critical','question'):
                box=MessageDialog(w,'消息测试',text,severity=severity);windows.append(box)
                box.show();app.processEvents()
                assert box.message.toPlainText()==text
                box.resize(400,260);app.processEvents()
                assert box.footer.isVisible() and box.rect().contains(box.footer.mapTo(box,box.footer.rect().bottomRight()))
                box.grab().save(str(out/('message-'+severity+'.png')))
                box.reject()
            def dismiss_message():
                for box in app.topLevelWidgets():
                    if isinstance(box,MessageDialog) and box.isVisible():
                        button=box.buttons.button(QDialogButtonBox.StandardButton.Ok)
                        if button:button.click()
            QTimer.singleShot(20,dismiss_message)
            assert DesignMessageBox.information(w,'提示','纯文本消息')==DesignMessageBox.StandardButton.Ok
            print('PASS: selectable plain error text, long paths and native QMessageBox result enums')

            image=folder/'测试图片.png';pixels=np.full((100,160,3),(35,90,155),dtype=np.uint8)
            Image.fromarray(pixels).save(image);w.open_image_path(str(image))
            w.blur_slider.setValue(74);w._history_timer.stop();w._commit_history()
            before=w._signature();history=w.history
            target=folder/'导出图片.png';Image.fromarray(pixels).save(target)
            original_exec=main.ExportCompletionDialog.exec
            def choose(which):
                def execute(dialog):
                    dialog.show();app.processEvents()
                    dialog.grab().save(str(out/('completion-'+which+'.png')))
                    QTimer.singleShot(20,dialog.buttons[which].click)
                    return original_exec(dialog)
                return execute
            with patch.object(main,'reveal_in_explorer',return_value=True) as reveal:
                with patch.object(main.ExportCompletionDialog,'exec',choose('next')):
                    w.show_export_completion(str(target))
                assert w.is_workspace_active() and w._signature()==before and w.history is history
                assert reveal.call_count==0,'Completion opened Explorer before a path click'
                long_path=str(folder/('长路径名称'*65)/'结果.png')
                dialog=main.ExportCompletionDialog(w,long_path,motion=main.MOTION,details='透明图层说明。');windows.append(dialog)
                dialog.fileRequested.connect(w._reveal_export);dialog.show();app.processEvents();QTest.qWait(20)
                assert '结果.png' in dialog.path_browser.toPlainText()
                assert dialog.path_browser.toPlainText()==str(Path(long_path).resolve())
                assert 0<=dialog.sequence_number<=9
                assert dialog.rect().contains(dialog.panel.geometry())
                dialog.path_browser.anchorClicked.emit(QUrl.fromLocalFile(long_path));assert reveal.call_count==1
                dialog.grab().save(str(out/'completion-long.png'))
                QTest.keyClick(dialog,Qt.Key.Key_Escape);app.processEvents();assert dialog.navigation=='next'
                animated=main.ExportCompletionDialog(w,str(target),motion={'enabled':True,'interval':8,'speed':1})
                windows.append(animated);animated.show();app.processEvents();QTest.qWait(280)
                assert animated._intro==1 and not animated._timer.isActive()
                w.resize(1100,850);app.processEvents()
                assert animated.size()==w.size() and animated.rect().contains(animated.panel.geometry())
                animated.reject();w.resize(1366,720);app.processEvents()
                with patch.object(main.ExportCompletionDialog,'exec',choose('exit')):
                    w.show_export_completion(str(target))
                assert w.pages.currentWidget() is w.welcome_page
                w.welcome_page.buttons['next'].click();app.processEvents()
                assert w.is_workspace_active() and w._signature()==before and w.history is history
            print('PASS: completion NEXT/EXIT/ESC, explicit file location, long path and preserved session/history')
            print('PASS: unified dialog checks; no network, Explorer launch or real user settings.')
        finally:
            for window in reversed(windows):
                window.close()
            app.processEvents();main._SETTINGS_PATH=previous_path
            main.MOTION_DEFAULTS.clear();main.MOTION_DEFAULTS.update(previous_motion)


if __name__=='__main__':run()
