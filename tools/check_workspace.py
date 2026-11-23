"""Offline integration checks for the live light workspace and its state mirrors."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
try:
    import cv2  # noqa: F401
except ModuleNotFoundError:
    sys.path.append(str(ROOT / '.venv/Lib/site-packages'))
    import cv2  # noqa: F401
sys.path.insert(0, str(ROOT / 'app'))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QThread, Qt, Signal
from PySide6.QtGui import QFontInfo, QMouseEvent, QPalette, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

import main
from workspace import SCALE, rect
from workspace_controls import BoundActionButton, BoundComboBox, BoundCheckBox


class FakeDepthWorker(QThread):
    finished_depth = Signal(object)
    failed = Signal(str)
    progress = Signal(str)
    retained = []

    def __init__(self, image, generation, model_id, depth_size):
        super().__init__()
        self.image, self.generation = image, generation
        self.model_id, self.depth_size = model_id, depth_size
        self.retained.append(self)

    def run(self):
        self.progress.emit('离线测试：生成相对深度')
        self.msleep(150)
        if not self.isInterruptionRequested():
            h, w = self.image.shape[:2]
            self.finished_depth.emit(np.tile(np.linspace(0, 1, w, dtype=np.float32), (h, 1)))


def run():
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('Workspace checks')
    output = ROOT / 'tmp/workspace-checks'
    output.mkdir(parents=True, exist_ok=True)
    live = []
    failures = []
    checked = []
    previous_path = main._SETTINGS_PATH
    previous_defaults = main.MOTION_DEFAULTS.copy()

    def wait_for(predicate, label, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError('Timed out: ' + label)
            app.processEvents()
            QTest.qWait(10)
        app.processEvents()

    def check(condition, label):
        if not condition:
            raise AssertionError(label)

    def new_window():
        window = main.MainWindow()
        window.resize(1366, 720)
        window.show()
        live.append(window)
        # Preserve event-log behavior without hardware enumeration in the UI test.
        window._device_profile = lambda: {'test': 'workspace-offline'}
        app.processEvents()
        return window

    def close_window(window):
        window.close()
        app.processEvents()
        if window in live:
            live.remove(window)

    def settle_render(window):
        window.render()
        wait_for(lambda: window.processed is not None, 'preview result')
        check(not failures, 'GUI error: ' + '; '.join(failures))

    def type_number(page, row, text):
        page.scroll.ensureWidgetVisible(row.value)
        app.processEvents()
        row.value.setFocus()
        QTest.keyClick(row.value, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(row.value, text)
        QTest.keyClick(row.value, Qt.Key.Key_Return)
        app.processEvents()

    def dialog_failure(*args, **_kwargs):
        failures.append(str(args[-1]) if args else 'unknown dialog error')
        return QMessageBox.StandardButton.Ok

    def drag_global(widget, delta, button=Qt.MouseButton.LeftButton):
        """Drag from fixed screen coordinates even when a handle relocates."""
        start = widget.rect().center()
        origin = widget.mapToGlobal(start)
        QTest.mousePress(widget, button, pos=start)
        for numerator in (1, 2, 3):
            target = origin + QPoint(round(delta.x() * numerator / 3),
                                     round(delta.y() * numerator / 3))
            local = widget.mapFromGlobal(target)
            move = QMouseEvent(QEvent.Type.MouseMove, QPointF(local), QPointF(target),
                               Qt.MouseButton.NoButton, button, Qt.KeyboardModifier.NoModifier)
            app.sendEvent(widget, move)
            app.processEvents()
        QTest.mouseRelease(widget, button, pos=widget.mapFromGlobal(origin + delta))
        app.processEvents(); QTest.qWait(10)

    def check_card_bounds(page):
        """Card heights may adapt; widths, action clearance and grids remain."""
        limit = page.card_height_limit()
        action_top = min(button.geometry().top() for button in page.actions.values())
        for name, panel in (('history', page.history_panel), ('parameters', page.parameters_panel)):
            target = page.panel_rects[name]
            check(page.CARD_MIN_HEIGHTS[name] <= target.height() <= limit + 1e-7,
                  name + ' card height is outside available bounds')
            check(target.bottom() <= action_top - 1,
                  name + ' card overlaps the foreground action shadow area')
            check(abs(panel.width() - rect(page.data['panels'][name]['rect']).width()) <= 1,
                  name + ' card width changed while its height adapted')

    def check_card_height_interaction(window):
        page = window.light_workspace
        window.resize(1366, 720); app.processEvents(); QTest.qWait(20)
        baseline = {name: page.panel_rects[name].height() for name in page.card_grips}
        list_height = page.history_list.height()
        settings_y = page.settings_button.y()
        viewport_height = page.scroll.viewport().height()
        window.resize(1920, 1080); app.processEvents(); QTest.qWait(20)
        for name, height in baseline.items():
            check(page.panel_rects[name].height() >= height + 350,
                  name + ' card did not use the extra vertical room')
        history_growth = page.history_panel.height() - round(baseline['history'])
        check(abs(page.history_list.height() - list_height - history_growth) <= 1,
              'Extra history card height did not enlarge the history list')
        check(abs(page.settings_button.y() - settings_y - history_growth) <= 1,
              'History settings block did not move together')
        check(page.scroll.viewport().height() >= viewport_height + 350,
              'Parameter card did not enlarge its scrolling viewport')
        check_card_bounds(page)
        check_card_grid(page)

        for name, delta in (('history', -120), ('parameters', -96)):
            grip = page.card_grips[name]
            before = page.panel_rects[name].height()
            other = 'parameters' if name == 'history' else 'history'
            other_height = page.panel_rects[other].height()
            drag_global(grip, QPoint(0, delta))
            check(abs(page.panel_rects[name].height() - (before + delta)) <= 2,
                  name + ' bottom grip did not follow the pointer')
            check(page.panel_rects[other].height() == other_height,
                  name + ' bottom grip changed the other card')
            store_ratio = float(window._store().value('workspace/' + name + 'HeightRatio'))
            check(abs(store_ratio - page.card_height_ratios[name]) < 1e-9,
                  name + ' manual height was not persisted on release')
            QTest.keyClick(grip, Qt.Key.Key_Up); app.processEvents()
            check(abs(page.panel_rects[name].height() - (before + delta - 8)) <= 2,
                  name + ' grip keyboard adjustment failed')
            QTest.keyClick(grip, Qt.Key.Key_Home); app.processEvents()
            check(abs(page.panel_rects[name].height() - page.CARD_MIN_HEIGHTS[name]) <= 1,
                  name + ' grip Home did not choose the minimum')
            QTest.mouseDClick(grip, Qt.MouseButton.LeftButton); app.processEvents()
            check(page.card_height_ratios[name] == 1.0
                  and abs(page.panel_rects[name].height() - page.card_height_limit()) <= 1,
                  name + ' double click did not restore automatic filling')

        drag_global(page.card_grips['parameters'], QPoint(0, -96))
        preferred_ratio = page.card_height_ratios['parameters']
        window.resize(1024, 640); app.processEvents(); QTest.qWait(20)
        check_card_bounds(page)
        check(page.card_height_ratios['parameters'] == preferred_ratio,
              'Window shrinking overwrote the preferred card height ratio')
        window.resize(1920, 1080); app.processEvents(); QTest.qWait(20)
        check(abs(page.panel_rects['parameters'].height() - preferred_ratio * page.card_height_limit()) <= 1,
              'Window growth did not restore the preferred card proportion')
        for name in page.card_grips:
            QTest.keyClick(page.card_grips[name], Qt.Key.Key_End)
        window.resize(1366, 720); app.processEvents(); QTest.qWait(20)
        check_card_bounds(page)

    def check_preview_adjustments(window):
        page = window.light_workspace
        layout = page.preview_layout
        original = page.previews['original'].canvas
        original._fit_rect()
        base_scale, zoom, pan = original._base_scale, original._zoom, QPointF(original._pan)
        for axis, delta, key in (('horizontal', QPoint(70, 0), Qt.Key.Key_Right),
                                 ('vertical', QPoint(0, 50), Qt.Key.Key_Down)):
            handle = layout.handles[axis]
            before = layout.ratios[axis]
            drag_global(handle, delta)
            check(layout.ratios[axis] > before, axis + ' preview divider did not drag')
            after_drag = layout.ratios[axis]
            QTest.keyClick(handle, key); app.processEvents()
            check(layout.ratios[axis] > after_drag,
                  axis + ' preview divider keyboard adjustment failed')
            QTest.keyClick(handle, Qt.Key.Key_Home); app.processEvents()
            default = getattr(layout, 'default_' + axis + '_ratio')
            check(abs(layout.ratios[axis] - default) < 1e-10,
                  axis + ' preview divider Home did not reset')
            drag_global(handle, delta)
            QTest.mouseDClick(handle, Qt.MouseButton.LeftButton); app.processEvents()
            check(abs(layout.ratios[axis] - default) < 1e-10,
                  axis + ' preview divider double click did not reset')
        check(original._base_scale == base_scale and original._zoom == zoom and original._pan == pan,
              'Preview partition resize changed image zoom or pan')

        controller = next(item for item in page.floating if item.widget is page.previews['effect'])
        controller.detach(); app.processEvents()
        old_placeholder = controller.placeholder.geometry()
        drag_global(layout.handles['vertical'], QPoint(0, 44))
        check(controller.placeholder.geometry() != old_placeholder,
              'Divider did not update the floating preview placeholder')
        target = controller.placeholder.geometry()
        controller.restore(); app.processEvents()
        check(page.previews['effect'].geometry() == target,
              'Floating preview did not restore to the adjusted partition')
        layout.reset(); app.processEvents()

        menu = original.view_menu()
        actions = {action.objectName(): action for action in menu.actions() if action.objectName()}
        expected_actions = {'preview.zoomIn', 'preview.zoomOut', 'preview.fit',
                            'preview.actualSize', 'preview.resetView', 'preview.resetLayout'}
        check(set(actions) == expected_actions and all(action.isEnabled() for action in actions.values()),
              'Loaded preview view menu is incomplete')
        neighbor_zoom = page.previews['effect'].canvas._zoom
        actions['preview.zoomIn'].trigger(); app.processEvents()
        check(original._zoom > zoom and page.previews['effect'].canvas._zoom == neighbor_zoom,
              'Preview zoom action did not remain independent')
        actions['preview.fit'].trigger(); app.processEvents()
        fit = original._fit_rect()
        check(fit.width() <= original.width() + 1e-7 and fit.height() <= original.height() + 1e-7,
              'Fit action does not contain the complete image')
        check(original._pan == QPointF() and original._zoom == 1.0,
              'Fit action did not reset zoom and pan')
        actions['preview.actualSize'].trigger(); app.processEvents()
        check(original._fit_rect().width() == original._pixmap.width()
              and original._fit_rect().height() == original._pixmap.height(),
              'Actual-size action did not use source pixels')
        center = original.rect().center()
        wheel = QWheelEvent(QPointF(center), QPointF(original.mapToGlobal(center)), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        app.sendEvent(original, wheel); app.processEvents()
        check(original._zoom > 1.0 and wheel.isAccepted(), 'Plain preview wheel did not zoom')
        drag_global(original, QPoint(16, 12), Qt.MouseButton.MiddleButton)
        check(not original._pan.isNull(), 'Plain middle-button drag did not pan the preview')
        QTest.mouseDClick(original, Qt.MouseButton.LeftButton); app.processEvents()
        fitted = original._fit_rect()
        check(fitted.width() <= original.width() + 1e-7 and fitted.height() <= original.height() + 1e-7,
              'Preview double click did not fit the image')
        actions['preview.resetView'].trigger(); app.processEvents()
        check(original._zoom == 1.0 and original._pan.isNull(), 'Reset view did not restore zoom and pan')
        cover = original._fit_rect()
        check(cover.width() >= original.width() - 1e-7 and cover.height() >= original.height() - 1e-7,
              'Reset view did not restore the source cover layout')
        check(abs(cover.width() / cover.height() - window.original.shape[1] / window.original.shape[0]) < 1e-9,
              'Preview zoom changed the image aspect ratio')
        menu.deleteLater()

    def check_card_grid(page, *, expanded_focus=False):
        """Check painted content columns and readable padding, not just hosts."""
        app.processEvents(); QTest.qWait(20); app.processEvents()
        row = page.rows['blur_slider']
        for widget, pixels, weight in (
                (page.history_title, 15, 700), (page.quality_label, 10, 700),
                (page.report_button, 11, 700), (page.quality, 8, 600),
                (page.lens.title, 15, 700), (page.focus.title, 15, 700),
                (row.label, 12, 600), (row.value.lineEdit(), 12, 600),
                (page.aperture, 12, 600), (page.full_bokeh, 12, 600),
                (page.ai_hint, 10, 400)):
            face = QFontInfo(widget.font())
            check((face.family(), face.pixelSize(), face.weight()) ==
                  ('Sarasa Gothic SC', pixels, weight),
                  f'{widget.objectName() or type(widget).__name__}: '
                  f'expected Sarasa {pixels}/{weight}, got {face.family()} {face.pixelSize()}/{face.weight()}')
        for group in (page.lens, page.focus):
            inner_left, inner_right = 18, group.width() - 18
            title_origin = group.title.mapTo(group, QPoint())
            check(title_origin.x() == inner_left and title_origin.x() + group.title.width() == inner_right,
                  group.title.text() + ' section title does not share the inset column')
            check(group.title.height() - group.title.fontMetrics().height() >= 4,
                  group.title.text() + ' title has insufficient vertical text padding')
            for row in page.rows.values():
                if not group.isAncestorOf(row) or not row.isVisibleTo(group):
                    continue
                label_origin = row.label.mapTo(group, QPoint())
                value_origin = row.value.mapTo(group, QPoint())
                slider_origin = row.slider.mapTo(group, QPoint())
                track = row.slider._track()
                check(label_origin.x() == inner_left,
                      row.label.text() + ' label is outside the shared left column')
                check(abs(slider_origin.x() + track.left() - (inner_left + 58 + 8)) <= 1,
                      row.label.text() + ' painted track start is misaligned')
                check(value_origin.x() + row.value.width() == inner_right,
                      row.label.text() + ' number does not share the right column')
                check(row.value.width() >= 44,
                      row.label.text() + ' number slot is too narrow')
                check(slider_origin.x() + track.right() + 4 <= value_origin.x(),
                      row.label.text() + ' slider thumb can overlap the number slot')
                check(row.label.width() >= row.label.fontMetrics().horizontalAdvance(row.label.text()),
                      row.label.text() + ' label is clipped')
            for combo in group.findChildren(BoundComboBox):
                if not combo.isVisibleTo(group):
                    continue
                origin = combo.mapTo(group, QPoint())
                check(origin.x() == inner_left + 58 + 8 and origin.x() + combo.width() == inner_right,
                      combo.currentText() + ' dropdown does not share the input columns')
            for check_box in group.findChildren(BoundCheckBox):
                if not check_box.isVisibleTo(group):
                    continue
                origin = check_box.mapTo(group, QPoint())
                check(check_box.height() == 26,
                      check_box.text() + ' checkbox row lost its 26 px interaction height')
                check(origin.x() == inner_left and origin.x() + check_box.width() == inner_right,
                      check_box.text() + ' checkbox row does not share the inset column')

        controls = [page.aperture]
        controls.extend(row for name, row in page.rows.items()
                        if name in ('blur_slider', 'bokeh_slider', 'bokeh_density', 'bokeh_size',
                                    'spherical_slider', 'cat_eye_slider', 'highlight_slider'))
        controls.extend(page.lens.findChildren(BoundComboBox))
        controls.extend(page.lens.findChildren(BoundCheckBox))
        controls = list(dict.fromkeys(controls))
        check(len(controls) == 11, 'The permanent lens section is missing a control')
        check(all(item.isVisibleTo(page.lens) for item in controls),
              'A lens control is hidden behind an expansion section')
        vertical = sorted((item.mapTo(page.lens, QPoint()).y(), item.height()) for item in controls)
        for (top, height), (next_top, _next_height) in zip(vertical, vertical[1:]):
            check(next_top - (top + height) >= 4,
                  'Permanent lens controls overlap or have inconsistent separation')
        before_visible = [item.isVisibleTo(page.lens) for item in controls]
        page.lens.title.click(); app.processEvents()
        check([item.isVisibleTo(page.lens) for item in controls] == before_visible,
              'Lens section title hides the permanent parameters')

        history_left, history_right = 14, page.history_panel.width() - 14
        for button in (page.settings_button, page.theme_button, page.motion_button,
                       page.help_button, page.report_button):
            origin = button.mapTo(page.history_panel, QPoint())
            check(origin.x() == history_left and origin.x() + button.width() == history_right,
                  button.text() + ' history action is outside the shared column')
        for group in (page.lens, page.focus):
            for button in group.findChildren(BoundActionButton):
                if not button.isVisibleTo(group):
                    continue
                origin = button.mapTo(group, QPoint())
                check(origin.x() >= 18 and origin.x() + button.width() <= group.width() - 18,
                      button.text() + ' action crosses a form inset')
        buttons = [*page.history_panel.findChildren(BoundActionButton),
                   *page.lens.findChildren(BoundActionButton),
                   *page.focus.findChildren(BoundActionButton)]
        for button in buttons:
            if not button.isVisible():
                continue
            metrics = button.fontMetrics()
            check(button.width() - metrics.horizontalAdvance(button.text()) >= 20,
                  button.text() + ' action has insufficient horizontal padding')
            check(button.height() - metrics.height() >= 6,
                  button.text() + ' action has insufficient vertical padding')
        if expanded_focus:
            hint = page.ai_hint.mapTo(page.focus, QPoint())
            check(hint.x() == 18 and hint.x() + page.ai_hint.width() == page.focus.width() - 18,
                  'AI information does not share the inset text column')

    def check_numeric_padding(page):
        """Exercise legal extrema without creating render/history requests."""
        for name, row in page.rows.items():
            before_value = row.source.value()
            before_geometry = row.value.geometry()
            was_blocked = row.source.blockSignals(True)
            try:
                for value in (row.source.minimum(), row.source.maximum(), 0):
                    row.source.setValue(value); row.sync()
                    edit = row.value.lineEdit()
                    text_width = edit.fontMetrics().horizontalAdvance(edit.displayText())
                    margins = edit.textMargins()
                    available = edit.contentsRect().width() - margins.left() - margins.right()
                    check(available >= text_width + 4,
                          f'{name} boundary has insufficient number padding: {edit.displayText()}')
                    check(row.value.geometry() == before_geometry,
                          name + ' legal values move the shared number column')
            finally:
                row.source.setValue(before_value); row.sync()
                row.source.blockSignals(was_blocked)

    def check_complete_parameter_viewport(page, dialog):
        """Every control can be reached in the narrowest floating card."""
        dialog.resize(300, 520); app.processEvents(); QTest.qWait(30)
        check(dialog.minimumWidth() >= 398 and dialog.width() >= 398,
              'Floating parameter window does not reserve the complete form width')
        viewport = page.scroll.viewport()
        check(viewport.width() >= page.content.width(),
              'Floating parameter content is wider than its visible viewport')
        check(not page.lens.disclosure.isVisibleTo(page.lens),
              'Permanent lens section exposes a collapse disclosure')
        was_expanded = page.focus._expanded
        if was_expanded:
            page.focus.title.click(); app.processEvents(); QTest.qWait(20)
        check(page.focus.disclosure.text() == '▸' and page.focus.disclosure.isVisibleTo(page.focus),
              'Closed focus section does not show its collapsed disclosure')
        page.scroll.ensureWidgetVisible(page.focus.title, 0, 8)
        app.processEvents(); QTest.qWait(10)
        page.focus.title.click(); app.processEvents(); QTest.qWait(30)
        check(page.focus._expanded and page.focus.disclosure.text() == '▾'
              and page.focus.extra.isVisibleTo(page.focus),
              'Focus disclosure does not show the expanded parameter section')
        first = page.focus.extra_layout.itemAt(0).widget()
        first_bounds = QRect(first.mapTo(viewport, QPoint()), first.size())
        check(viewport.rect().contains(first_bounds),
              'Expanding focus did not reveal its first additional control')

        widgets = [*page.rows.values(), *(row.value for row in page.rows.values()),
                   *page.content.findChildren(BoundComboBox),
                   *page.content.findChildren(BoundCheckBox),
                   *page.content.findChildren(BoundActionButton), page.ai_hint]
        widgets = sorted(dict.fromkeys(widgets),
                         key=lambda widget: widget.mapTo(page.content, QPoint()).y())
        for widget in widgets:
            check(widget.isVisibleTo(page.content),
                  widget.objectName() + ' cannot be reached after expanding focus')
            page.scroll.ensureWidgetVisible(widget, 0, 8)
            app.processEvents(); QTest.qWait(10)
            bounds = QRect(widget.mapTo(viewport, QPoint()), widget.size())
            check(bounds.left() >= 0 and bounds.right() < viewport.width(),
                  widget.objectName() + ' is horizontally clipped in the narrow floating card')
            check(viewport.rect().contains(bounds),
                  widget.objectName() + ' cannot enter the complete scrolling viewport')
        check_card_grid(page, expanded_focus=True)
        scrolled = page.scroll.verticalScrollBar().value()
        page.sync_motion(); app.processEvents(); QTest.qWait(20)
        check(page.scroll.verticalScrollBar().value() == scrolled,
              'Repeated motion synchronization jumps back to the first focus control')
        if not was_expanded:
            page.focus.title.click(); app.processEvents(); QTest.qWait(20)
            check(not page.focus._expanded and page.focus.disclosure.text() == '▸',
                  'Focus disclosure does not return to the collapsed state')

    with tempfile.TemporaryDirectory(prefix='bjdgq-workspace-') as directory:
        directory = Path(directory)
        main._SETTINGS_PATH = directory / 'settings.ini'
        main.MOTION_DEFAULTS['enabled'] = False
        with patch.object(main.QMessageBox, 'critical', side_effect=dialog_failure), \
                patch.object(main.QMessageBox, 'warning', side_effect=dialog_failure), \
                patch.object(main.QMessageBox, 'information', return_value=QMessageBox.StandardButton.Ok), \
                patch.object(main, 'reveal_in_explorer', return_value=True):
            try:
                w = new_window()
                w.welcome_page.buttons['start'].click()
                app.processEvents()
                page = w.light_workspace
                check(w.pages.currentWidget() is page, 'START did not select light workspace')
                check(not w.statusBar().isVisible(), 'Light workspace native status bar should be hidden')
                check(page.metadata.caption == '', 'Empty metadata contains placeholder text')
                check(page.metadata.y() < page.focus.y() < page.lens.y(),
                      'Parameter panel should show metadata, focus, then lens controls')
                check(page.actions['export'].isEnabled(), 'Empty workspace EXPORT should keep the source action available')
                with patch.object(main.QMessageBox, 'information', return_value=QMessageBox.StandardButton.Ok) as prompt:
                    page.actions['export'].click()
                    check(prompt.call_count == 1 and '先打开' in str(prompt.call_args.args[-1]), 'Empty EXPORT did not request an image')
                base_sizes = {key: panel.size() for key, panel in [('history', page.history_panel), ('parameters', page.parameters_panel)]}
                dx = page.width() - 4096 * SCALE
                for key in ('history', 'parameters'):
                    expected = rect(page.data['panels'][key]['rect']).translated(dx, 0).toAlignedRect()
                    actual = page.history_panel.geometry() if key == 'history' else page.parameters_panel.geometry()
                    check(actual.x() == expected.x() and actual.y() == expected.y()
                          and actual.width() == expected.width(), key + ' source anchors changed')
                check_card_bounds(page)
                check(abs(page.actions['import'].y() - page.actions['export'].y()) <= 1, 'Main actions are not aligned')
                for width, height in ((1024,768),(1100,850),(1280,800),(1366,720),
                                      (1600,900),(1920,1080),(2560,1080)):
                    w.resize(width,height);app.processEvents();QTest.qWait(10)
                    check(w.width()==width and w.height()==height,
                          'A hidden page constrains the light workspace size')
                    check(page.history_panel.width()==base_sizes['history'].width()
                          and page.parameters_panel.width()==base_sizes['parameters'].width(),
                          'Responsive background resize changed fixed panel widths')
                    check_card_bounds(page)
                    check(page.actions['import'].y()==page.actions['export'].y(),
                          'Responsive background resize moved the main action baseline')
                w.resize(1366,720);app.processEvents()
                w.grab().save(str(output / 'empty.png'))
                checked.append('light START, source anchors, metadata empty and export availability')
                check_card_grid(page)
                checked.append('shared form/history columns, permanent lens controls and action padding')
                check_card_height_interaction(w)
                checked.append('automatic card growth, independent height grips, keyboard bounds and persistence')

                main.MOTION.update(enabled=True, flock=True, particles=12)
                w._apply_motion_settings()
                app.processEvents()
                for name in ('original', 'effect'):
                    canvas = page.previews[name].canvas
                    check(canvas.palette().color(QPalette.ColorRole.Highlight) == Qt.GlobalColor.white, 'Flock is not white')
                    check(len(canvas._flock) == 12 and canvas._flock_timer.isActive(), 'Flock count or enabled state lost')
                main.MOTION['enabled'] = False
                w._apply_motion_settings()
                check(not page.previews['original'].canvas._flock_timer.isActive(), 'Flock timer did not stop')
                checked.append('white flock, count and animation switch')

                h, width = 120, 192
                yy, xx = np.mgrid[:h, :width]
                pixels = np.stack(((xx * 255 // width), (yy * 255 // h), ((xx + yy) * 255 // (width + h))), axis=2).astype(np.uint8)
                source = directory / '工作区图像.png'
                Image.fromarray(pixels).save(source)
                w.open_image_path(str(source))
                depth = np.tile(np.linspace(0, 1, width, dtype=np.float32), (h, 1))
                w.apply_depth_map(depth, '离线导入深度')
                settle_render(w)
                for name, canonical in [('original', w.before_canvas), ('effect', w.after_canvas), ('depth', w.depth_preview)]:
                    check(page.previews[name].canvas._pixmap is canonical._pixmap, name + ' pixmap was not shared')
                check('192' in page.metadata.caption and '120' in page.metadata.caption, 'Loaded metadata did not update')
                check(page.actions['export'].isEnabled(), 'Loaded EXPORT is disabled')
                checked.append('image/depth/render mirrors and metadata')
                check_preview_adjustments(w)
                checked.append('preview divider dragging, keyboard reset, floating placeholder, wheel/pan and view actions')

                page.lens.toggle_extra(); page.focus.toggle_extra(); app.processEvents()
                full_check = next(item for item in page.findChildren(BoundCheckBox) if item.source is w.full_luminance_bokeh)
                full_check.setChecked(True)
                app.processEvents()
                check(w.full_luminance_bokeh.isChecked() and page.rows['bokeh_size'].slider.maximum() == 600, 'Full-mode range did not synchronize')
                check_card_grid(page, expanded_focus=True)
                check_numeric_padding(page)
                type_number(page, page.rows['bokeh_size'], '555')
                check(w.bokeh_size.value() == 555, 'Percent keyboard input did not reach source')
                type_number(page, page.rows['focus_slider'], '37.6')
                check(w.focus_slider.value() == 376, 'Decimal keyboard input did not reach source')
                w.bokeh_size.setValue(411); app.processEvents()
                check(page.rows['bokeh_size'].value.value() == 411, 'Source-to-mirror number synchronization failed')
                saved = w._snapshot(); saved['blur_slider'] = 172; saved['bokeh_size'] = 512
                w._suppress_history = True
                try:
                    w._restore(saved)
                finally:
                    w._suppress_history = False
                app.processEvents()
                check(page.rows['blur_slider'].value.value() == 172 and page.rows['bokeh_size'].value.value() == 512, 'Blocked restore did not synchronize')
                for name in ('bokeh_size', 'focus_slider', 'spherical_slider'):
                    edit = page.rows[name].value.lineEdit()
                    check(edit.contentsRect().width() >= edit.fontMetrics().horizontalAdvance(edit.displayText()), name + ' text is clipped')
                checked.append('percentage/decimal keyboard input, ranges, two-way state and blocked restore')

                marker = 'offline/custom-model'
                w.model_combo.addItem('Offline custom model', marker)
                w.model_combo.setCurrentIndex(w.model_combo.count() - 1)
                app.processEvents()
                check(page.models.currentData() == marker, 'Dynamic model did not synchronize')
                w.model_combo.removeItem(w.model_combo.count() - 1); w.model_combo.setCurrentIndex(0); app.processEvents()
                w._history_timer.stop()
                w.history.reset('测试基线', w._snapshot(), w._signature()); w._refresh_log()
                before = w.aperture.currentIndex()
                page.aperture.setCurrentIndex((before + 1) % w.aperture.count())
                w._history_timer.stop(); w._commit_history()
                w.undo(); check(w.aperture.currentIndex() == before and page.aperture.currentIndex() == before, 'Aperture undo did not synchronize')
                w.redo(); check(page.aperture.currentIndex() == w.aperture.currentIndex() != before, 'Aperture redo did not synchronize')
                shape = next(item for item in page.findChildren(BoundComboBox) if item.source is w.bokeh_shape)
                before = w.bokeh_shape.currentIndex(); shape.setCurrentIndex((before + 1) % w.bokeh_shape.count())
                w._history_timer.stop(); w._commit_history(); w.undo()
                check(shape.currentIndex() == w.bokeh_shape.currentIndex() == before, 'Shape undo did not synchronize')
                w.redo(); check(shape.currentIndex() == w.bokeh_shape.currentIndex() != before, 'Shape redo did not synchronize')
                checked.append('dynamic models, aperture/shape history and mirror restoration')

                w.compare_btn.setChecked(True); app.processEvents()
                check(np.array_equal(w._display_image(), w.original), 'Compare shows processed image')
                check(page.previews['effect'].canvas._pixmap is w.after_canvas._pixmap, 'Compare mirror did not update')
                w.compare_btn.setChecked(False); page.range_check.setChecked(True)
                settle_render(w)
                check(page.range_check.isChecked() == w.range_check.isChecked(), 'Range check lost source state')
                canvas = page.previews['original'].canvas
                box = canvas._fit_rect(); point = QPoint(round(canvas.width() * .6), round(canvas.height() * .6))
                normalized_x = (point.x() - box.x()) / box.width()
                normalized_y = (point.y() - box.y()) / box.height()
                ix = min(width - 1, max(0, int(normalized_x * width)))
                iy = min(h - 1, max(0, int(normalized_y * h)))
                expected = round(float(w.depth[iy, ix]) * 1000)
                QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point); app.processEvents()
                check(w.focus_slider.value() == expected, 'New preview did not forward click focus coordinates')
                check(page.rows['focus_slider'].value.value() == expected / 10, 'Click focus did not update decimal mirror')
                checked.append('compare/range display and cropped-canvas focus click')

                canvas._fit_rect(); base_scale = canvas._base_scale
                image_box = canvas._fit_rect()
                w.resize(1566, 820); app.processEvents()
                resized = canvas._fit_rect()
                check(abs(resized.width() / resized.height() - width / h) < 1e-9, 'Resize deformed preview')
                check(canvas._base_scale == base_scale and abs(resized.width() - image_box.width()) < 1e-8, 'Resize recomputed image base scale')
                for name, widget in [('history', page.history_panel), ('parameters', page.parameters_panel)]:
                    check(widget.width() == base_sizes[name].width(), name + ' fixed width changed on resize')
                check_card_bounds(page)
                check(page.actions['import'].y() == page.actions['export'].y(), 'Resized actions are not aligned')
                check_card_grid(page, expanded_focus=True)
                w.resize(1366, 720); app.processEvents()
                check(len(page.floating) == 7, 'Floating components missing')
                expanded = {group: group._expanded for group in (page.lens, page.focus)}
                for group, was_expanded in expanded.items():
                    if was_expanded:
                        group.toggle_extra()
                app.processEvents(); QTest.qWait(20)
                closed_heights = {group: group.height() for group in expanded}
                initial_auto_fill = {item.widget: item.widget.autoFillBackground() for item in page.floating}
                for controller in page.floating:
                    controller.detach(); app.processEvents()
                    check(controller.floating is not None and controller.widget.isVisible(), 'Floating failed: ' + controller.title)
                    controller.floating.resize(700, 600); app.processEvents()
                    card_name = ('history' if controller.widget is page.history_panel else
                                 'parameters' if controller.widget is page.parameters_panel else None)
                    if card_name is not None:
                        check(not page.card_grips[card_name].isVisible(),
                              card_name + ' docked grip is visible while its card floats')
                    if card_name == 'parameters':
                        check_complete_parameter_viewport(page, controller.floating)
                    check_card_grid(page, expanded_focus=False)
                    controller.restore(); app.processEvents()
                    check(controller.floating is None and controller.placeholder is None, 'Restore failed: ' + controller.title)
                    check(controller.widget.isVisible(), 'Restored component hidden: ' + controller.title)
                    check(controller.widget.autoFillBackground() == initial_auto_fill[controller.widget],
                          'Floating restore changed autoFillBackground: ' + controller.title)
                    for group, before_height in closed_heights.items():
                        after_height = group.height()
                        natural_height = group.sizeHint().height()
                        check(abs(after_height - before_height) <= 1 or abs(after_height - natural_height) <= 1,
                              f'Closed group stretched after {controller.title}: before={before_height}, after={after_height}, hint={natural_height}')
                for group, was_expanded in expanded.items():
                    if was_expanded:
                        group.toggle_extra()
                app.processEvents()
                type_number(page, page.rows['bokeh_size'], '422')
                check(w.bokeh_size.value() == 422, 'Restored controls are disconnected')
                checked.append('fixed card widths/crop scale, seven floats, restored transparency and closed-group heights')
                check(page.focus.y() < page.lens.y(), 'Restoring floating groups changed their order')
                checked.append('minimum floating form width, complete scroll reachability and focus disclosure states')

                # Canonical restores block widget signals; detached cards live
                # outside the page subtree and must still refresh every mirror.
                for floating_widget in (page.lens, page.parameters_panel):
                    controller = next(item for item in page.floating if item.widget is floating_widget)
                    w._history_timer.stop(); w._commit_history()
                    baseline_size = w.bokeh_size.value()
                    baseline_checked = w.highlight_check.isChecked()
                    baseline_aperture = w.aperture.currentIndex()
                    controller.detach(); app.processEvents()
                    check(controller.floating is not None, 'Float before blocked restore failed')
                    highlight = next(item for item in page.lens.findChildren(BoundCheckBox)
                                     if item.source is w.highlight_check)
                    new_size = baseline_size + 17 if baseline_size <= 583 else baseline_size - 17
                    new_aperture = (baseline_aperture + 1) % w.aperture.count()
                    number = page.rows['bokeh_size'].value
                    number.setFocus()
                    QTest.keyClick(number, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
                    QTest.keyClicks(number, str(new_size))
                    QTest.keyClick(number, Qt.Key.Key_Return)
                    highlight.setChecked(not baseline_checked)
                    page.aperture.setCurrentIndex(new_aperture)
                    app.processEvents()
                    w._history_timer.stop(); w._commit_history()
                    check(w.bokeh_size.value() == new_size and w.highlight_check.isChecked() != baseline_checked
                          and w.aperture.currentIndex() == new_aperture, 'Detached edits missed canonical state')
                    w.undo(); app.processEvents()
                    check(number.value() == w.bokeh_size.value() == baseline_size,
                          controller.title + ' blocked undo left a stale number')
                    check(highlight.isChecked() == w.highlight_check.isChecked() == baseline_checked,
                          controller.title + ' blocked undo left a stale checkbox')
                    check(page.aperture.currentIndex() == w.aperture.currentIndex() == baseline_aperture,
                          controller.title + ' blocked undo left a stale combo')
                    w.redo(); app.processEvents()
                    check(number.value() == w.bokeh_size.value() == new_size,
                          controller.title + ' blocked redo left a stale number')
                    check(highlight.isChecked() == w.highlight_check.isChecked() == (not baseline_checked),
                          controller.title + ' blocked redo left a stale checkbox')
                    check(page.aperture.currentIndex() == w.aperture.currentIndex() == new_aperture,
                          controller.title + ' blocked redo left a stale combo')
                    controller.restore(); app.processEvents()
                    check(controller.widget.autoFillBackground() == initial_auto_fill[controller.widget],
                          'Editing while floating changed restored transparency: ' + controller.title)
                checked.append('detached lens/panel edits and blocked undo/redo refresh all mirror types')

                with patch.object(main, 'DepthWorker', FakeDepthWorker):
                    page.ai_button.click(); app.processEvents()
                    check(w.depth_worker is not None and not page.ai_button.isEnabled(), 'AI busy state did not synchronize')
                    wait_for(lambda: '离线测试' in page.ai_hint.text(), 'AI progress mirror')
                    wait_for(lambda: w.depth_worker is None and page.ai_button.isEnabled(), 'AI completion')
                    check(page.models.isEnabled() and page.previews['depth'].canvas._pixmap is w.depth_preview._pixmap, 'AI result mirror failed')
                checked.append('offline fake AI busy/progress/finished mirrors')

                before_size = w.bokeh_size.value()
                w.toggle_theme(); app.processEvents()
                wait_for(lambda: w._theme_transition is None, 'dark transition')
                check(w._theme_mode == 'dark' and w.pages.currentWidget() is w.editorial_background, 'Dark layout not restored')
                w.toggle_theme(); app.processEvents()
                wait_for(lambda: w._theme_transition is None, 'light transition')
                check(w.pages.currentWidget() is page and w.bokeh_size.value() == before_size, 'Theme round trip lost state')
                w.resize(1024,768);app.processEvents();QTest.qWait(20)
                check(w.width()==1024 and w.height()==768,
                      'Dark layout minimum leaked back into the light workspace')
                w.resize(1366,720);app.processEvents()
                checked.append('dark/light layout and parameter preservation')

                for layer in (False, True):
                    target = directory / ('透明.png' if layer else '效果.png')
                    options = {'layer': layer, 'format': 'PNG', 'scale': .5, 'aa': 2, 'quality': 95}
                    with patch.object(main.ExportDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
                            patch.object(main.ExportDialog, 'values', return_value=options), \
                            patch.object(main.ExportCompletionDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
                            patch.object(main.QFileDialog, 'getSaveFileName', return_value=(str(target), 'PNG')):
                        page.actions['export'].click()
                    check(target.is_file() and not failures, 'Actual export failed: ' + str(failures))
                    with Image.open(target) as result:
                        check(result.size == (96, 60), 'Output size changed')
                        check(result.mode == ('RGBA' if layer else 'RGB'), 'Output mode changed')
                checked.append('actual RGB and RGBA engine exports through light action')

                w._history_timer.stop(); w._commit_history(); warm_history = w.history
                warm_size = w.bokeh_size.value()
                QTest.keyClick(page.preview_layout.handles['horizontal'], Qt.Key.Key_Right)
                QTest.keyClick(page.preview_layout.handles['vertical'], Qt.Key.Key_Down)
                drag_global(page.card_grips['history'], QPoint(0, -48))
                drag_global(page.card_grips['parameters'], QPoint(0, -40))
                expected_layout_ratios = dict(page.preview_layout.ratios)
                expected_card_ratios = dict(page.card_height_ratios)
                w.show_welcome(); app.processEvents()
                check(w.pages.currentWidget() is w.welcome_page, 'Light workspace did not return home')
                check(not page.previews['original'].canvas._flock_timer.isActive(), 'Hidden flock did not stop')
                w.welcome_page.buttons['next'].click(); app.processEvents()
                check(w.pages.currentWidget() is page and w.history is warm_history, 'Warm NEXT lost workspace/history')
                w.grab().save(str(output / 'loaded.png'))
                close_window(w)
                w2 = new_window(); w2.welcome_page.buttons['next'].click(); app.processEvents()
                check(w2.pages.currentWidget() is w2.light_workspace, 'Cold NEXT chose wrong page')
                check(w2.bokeh_size.value() == warm_size and w2.light_workspace.rows['bokeh_size'].value.value() == warm_size, 'Cold NEXT lost mirror state')
                check(w2.original_full is not None and w2.depth is not None, 'Cold NEXT lost image/depth')
                for axis, ratio in expected_layout_ratios.items():
                    check(abs(w2.light_workspace.preview_layout.ratios[axis] - ratio) < 1e-9,
                          axis + ' preview proportion did not survive restart')
                for name, ratio in expected_card_ratios.items():
                    check(abs(w2.light_workspace.card_height_ratios[name] - ratio) < 1e-9,
                          name + ' manual card height did not survive restart')
                close_window(w2)
                checked.append('light workspace warm/cold welcome recovery')
                check(not failures, 'Unexpected GUI failures: ' + '; '.join(failures))
                for label in checked:
                    print('PASS:', label)
                print(f'PASS: {len(checked)} workspace integration groups; no network, explorer or real user settings used.')
            finally:
                for window in list(live):
                    close_window(window)
                for worker in FakeDepthWorker.retained:
                    if worker.isRunning():
                        worker.requestInterruption(); worker.wait(1000)
                main._SETTINGS_PATH = previous_path
                main.MOTION_DEFAULTS.clear(); main.MOTION_DEFAULTS.update(previous_defaults)


if __name__ == '__main__':
    run()
