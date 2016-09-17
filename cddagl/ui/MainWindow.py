import sys
from distutils.version import LooseVersion
from io import BytesIO
from urllib.parse import urljoin

import html5lib
from PyQt5.QtCore import QByteArray, Qt, QUrl, QThread, pyqtSignal
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import QMainWindow, QMenu, QAction, QCheckBox, QMessageBox, \
    QTabWidget
from lxml import etree

from cddagl.__version__ import version
from cddagl.config import get_config_value, config_true, set_config_value
from cddagl.constants import RELEASES_URL
from cddagl.globals import gt
from cddagl.helpers.win32 import SimpleNamedPipe
from cddagl.ui.AboutDialog import AboutDialog
from cddagl.ui.BackupsTab import BackupsTab
from cddagl.ui.FontsTab import FontsTab
from cddagl.ui.LauncherUpdateDialog import LauncherUpdateDialog
from cddagl.ui.MainTab import MainTab
from cddagl.ui.ModsTab import ModsTab
from cddagl.ui.SettingsTab import SettingsTab
from cddagl.ui.SoundpacksTab import SoundpacksTab
from cddagl.ui.TilesetsTab import TilesetsTab


class MainWindow(QMainWindow):
    def __init__(self, title):
        super(MainWindow, self).__init__()

        self.setMinimumSize(440, 500)

        self.create_status_bar()
        self.create_central_widget()
        self.create_menu()

        self.shown = False
        self.qnam = QNetworkAccessManager()
        self.http_reply = None
        self.in_manual_update_check = False

        self.about_dialog = None

        geometry = get_config_value('window_geometry')
        if geometry is not None:
            qt_geometry = QByteArray.fromBase64(geometry.encode('utf8'))
            self.restoreGeometry(qt_geometry)

        self.setWindowTitle(title)

        if not config_true(get_config_value('allow_multiple_instances',
                                            'False')):
            self.init_named_pipe()

    def set_text(self):
        self.file_menu.setTitle(gt('&File'))
        self.exit_action.setText(gt('E&xit'))
        self.help_menu.setTitle(gt('&Help'))
        if getattr(sys, 'frozen', False):
            self.update_action.setText(gt('&Check for update'))
        self.about_action.setText(gt('&About CDDA Game Launcher'))

        if self.about_dialog is not None:
            self.about_dialog.set_text()
        self.central_widget.set_text()

    def create_status_bar(self):
        status_bar = self.statusBar()
        status_bar.busy = 0

        status_bar.showMessage(gt('Ready'))

    def create_central_widget(self):
        central_widget = CentralWidget()
        self.setCentralWidget(central_widget)
        self.central_widget = central_widget

    def create_menu(self):
        file_menu = QMenu(gt('&File'))
        self.menuBar().addMenu(file_menu)
        self.file_menu = file_menu

        exit_action = QAction(gt('E&xit'), self, triggered=self.close)
        file_menu.addAction(exit_action)
        self.exit_action = exit_action

        help_menu = QMenu(gt('&Help'))
        self.menuBar().addMenu(help_menu)
        self.help_menu = help_menu

        if getattr(sys, 'frozen', False):
            update_action = QAction(gt('&Check for update'), self,
                                    triggered=self.manual_update_check)
            self.update_action = update_action
            self.help_menu.addAction(update_action)
            self.help_menu.addSeparator()

        about_action = QAction(gt('&About CDDA Game Launcher'), self,
                               triggered=self.show_about_dialog)
        self.about_action = about_action
        self.help_menu.addAction(about_action)

    def show_about_dialog(self):
        if self.about_dialog is None:
            about_dialog = AboutDialog(self, Qt.WindowTitleHint |
                                       Qt.WindowCloseButtonHint)
            self.about_dialog = about_dialog

        self.about_dialog.exec()

    def check_new_launcher_version(self):
        self.lv_html = BytesIO()
        self.http_reply = self.qnam.get(QNetworkRequest(QUrl(RELEASES_URL)))
        self.http_reply.finished.connect(self.lv_http_finished)
        self.http_reply.readyRead.connect(self.lv_http_ready_read)

    def lv_http_finished(self):
        self.lv_html.seek(0)
        document = html5lib.parse(self.lv_html, treebuilder='lxml',
                                  encoding='utf8', namespaceHTMLElements=False)

        for release in document.getroot().cssselect('div.release.label-latest'):
            latest_version = None
            version_text = None
            for span in release.cssselect('ul.tag-references li:first-child '
                                          'span'):
                version_text = span.text
                if version_text.startswith('v'):
                    version_text = version_text[1:]
                latest_version = LooseVersion(version_text)

            if latest_version is not None:
                current_version = LooseVersion(version)

                if latest_version > current_version:
                    release_header = ''
                    release_body = ''

                    header_divs = release.cssselect(
                        'div.release-body div.release-header')
                    if len(header_divs) > 0:
                        header = header_divs[0]
                        for anchor in header.cssselect('a'):
                            if 'href' in anchor.keys():
                                anchor.set('href', urljoin(RELEASES_URL,
                                                           anchor.get('href')))
                        release_header = etree.tostring(header,
                                                        encoding='utf8',
                                                        method='html').decode(
                            'utf8')

                    body_divs = release.cssselect(
                        'div.release-body div.markdown-body')
                    if len(body_divs) > 0:
                        body = body_divs[0]
                        for anchor in body.cssselect('a'):
                            if 'href' in anchor.keys():
                                anchor.set('href', urljoin(RELEASES_URL,
                                                           anchor.get('href')))
                        release_body = etree.tostring(body,
                                                      encoding='utf8',
                                                      method='html').decode(
                            'utf8')

                    html_text = release_header + release_body

                    no_launcher_version_check_checkbox = QCheckBox()
                    no_launcher_version_check_checkbox.setText(
                        gt('Do not check '
                           'for new version of the CDDA Game Launcher on launch'))
                    check_state = (Qt.Checked if config_true(get_config_value(
                        'prevent_version_check_launch', 'False'))
                                   else Qt.Unchecked)
                    no_launcher_version_check_checkbox.stateChanged.connect(
                        self.nlvcc_changed)
                    no_launcher_version_check_checkbox.setCheckState(
                        check_state)

                    launcher_update_msgbox = QMessageBox()
                    launcher_update_msgbox.setWindowTitle(gt('Launcher update'))
                    launcher_update_msgbox.setText(gt('You are using version '
                                                      '{version} but there is a new update for CDDA Game '
                                                      'Launcher. Would you like to update?').format(
                        version=version))
                    launcher_update_msgbox.setInformativeText(html_text)
                    launcher_update_msgbox.addButton(gt('Update the launcher'),
                                                     QMessageBox.YesRole)
                    launcher_update_msgbox.addButton(gt('Not right now'),
                                                     QMessageBox.NoRole)
                    launcher_update_msgbox.setCheckBox(
                        no_launcher_version_check_checkbox)
                    launcher_update_msgbox.setIcon(QMessageBox.Question)

                    if launcher_update_msgbox.exec() == 0:
                        for item in release.cssselect(
                                'ul.release-downloads li a'):
                            if 'href' in item.keys():
                                url = urljoin(RELEASES_URL, item.get('href'))
                                if url.endswith('.exe'):
                                    launcher_update_dialog = (
                                        LauncherUpdateDialog(url, version_text,
                                                             self,
                                                             Qt.WindowTitleHint |
                                                             Qt.WindowCloseButtonHint))
                                    launcher_update_dialog.exec()

                                    if launcher_update_dialog.updated:
                                        self.close()
                else:
                    self.no_launcher_update_found()

    def nlvcc_changed(self, state):
        no_launcher_version_check_checkbox = (
            self.central_widget.settings_tab.launcher_settings_group_box.no_launcher_version_check_checkbox)
        no_launcher_version_check_checkbox.setCheckState(state)

    def manual_update_check(self):
        self.in_manual_update_check = True
        self.check_new_launcher_version()

    def no_launcher_update_found(self):
        if self.in_manual_update_check:
            up_to_date_msgbox = QMessageBox()
            up_to_date_msgbox.setWindowTitle(gt('Up to date'))
            up_to_date_msgbox.setText(gt('The CDDA Game Launcher is up to date.'
                                         ))
            up_to_date_msgbox.setIcon(QMessageBox.Information)

            up_to_date_msgbox.exec()

            self.in_manual_update_check = False

    def lv_http_ready_read(self):
        self.lv_html.write(self.http_reply.readAll())

    def init_named_pipe(self):
        class PipeReadWaitThread(QThread):
            read = pyqtSignal(bytes)

            def __init__(self):
                super(PipeReadWaitThread, self).__init__()

                try:
                    self.pipe = SimpleNamedPipe('cddagl_instance')
                except (OSError, PyWinError):
                    self.pipe = None

            def __del__(self):
                self.wait()

            def run(self):
                if self.pipe is None:
                    return

                while self.pipe is not None:
                    if self.pipe.connect() and self.pipe is not None:
                        try:
                            value = self.pipe.read(1024)
                            self.read.emit(value)
                        except (PyWinError, IOError):
                            pass

        def instance_read(value):
            if value == b'dupe':
                self.showNormal()
                self.raise_()
                self.activateWindow()

        pipe_read_wait_thread = PipeReadWaitThread()
        pipe_read_wait_thread.read.connect(instance_read)
        pipe_read_wait_thread.start()

        self.pipe_read_wait_thread = pipe_read_wait_thread

    def showEvent(self, event):
        if not self.shown:
            if not config_true(get_config_value('prevent_version_check_launch',
                                                'False')):
                if getattr(sys, 'frozen', False):
                    self.check_new_launcher_version()

        self.shown = True

    def save_geometry(self):
        geometry = self.saveGeometry().toBase64().data().decode('utf8')
        set_config_value('window_geometry', geometry)

        backups_tab = self.central_widget.backups_tab
        backups_tab.save_geometry()

    def closeEvent(self, event):
        update_group_box = self.central_widget.main_tab.update_group_box
        soundpacks_tab = self.central_widget.soundpacks_tab

        if update_group_box.updating:
            update_group_box.close_after_update = True
            update_group_box.update_game()

            if not update_group_box.updating:
                self.save_geometry()
                event.accept()
            else:
                event.ignore()
        elif soundpacks_tab.installing_new_soundpack:
            soundpacks_tab.close_after_install = True
            soundpacks_tab.install_new()

            if not soundpacks_tab.installing_new_soundpack:
                self.save_geometry()
                event.accept()
            else:
                event.ignore()
        else:
            self.save_geometry()
            event.accept()


class CentralWidget(QTabWidget):
    def __init__(self):
        super(CentralWidget, self).__init__()

        self.create_main_tab()
        self.create_backups_tab()
        self.create_mods_tab()
        # self.create_tilesets_tab()
        self.create_soundpacks_tab()
        # self.create_fonts_tab()
        self.create_settings_tab()

    def set_text(self):
        self.setTabText(self.indexOf(self.main_tab), gt('Main'))
        self.setTabText(self.indexOf(self.backups_tab), gt('Backups'))
        self.setTabText(self.indexOf(self.mods_tab), gt('Mods'))
        # self.setTabText(self.indexOf(self.tilesets_tab), _('Tilesets'))
        self.setTabText(self.indexOf(self.soundpacks_tab), gt('Soundpacks'))
        # self.setTabText(self.indexOf(self.fonts_tab), _('Fonts'))
        self.setTabText(self.indexOf(self.settings_tab), gt('Settings'))

        self.main_tab.set_text()
        self.backups_tab.set_text()
        self.mods_tab.set_text()
        # self.tilesets_tab.set_text()
        self.soundpacks_tab.set_text()
        # self.fonts_tab.set_text()
        self.settings_tab.set_text()

    def create_main_tab(self):
        main_tab = MainTab()
        self.addTab(main_tab, gt('Main'))
        self.main_tab = main_tab

    def create_backups_tab(self):
        backups_tab = BackupsTab()
        self.addTab(backups_tab, gt('Backups'))
        self.backups_tab = backups_tab

    def create_mods_tab(self):
        mods_tab = ModsTab()
        self.addTab(mods_tab, gt('Mods'))
        self.mods_tab = mods_tab

    def create_tilesets_tab(self):
        tilesets_tab = TilesetsTab()
        self.addTab(tilesets_tab, gt('Tilesets'))
        self.tilesets_tab = tilesets_tab

    def create_soundpacks_tab(self):
        soundpacks_tab = SoundpacksTab()
        self.addTab(soundpacks_tab, gt('Soundpacks'))
        self.soundpacks_tab = soundpacks_tab

    def create_fonts_tab(self):
        fonts_tab = FontsTab()
        self.addTab(fonts_tab, gt('Fonts'))
        self.fonts_tab = fonts_tab

    def create_settings_tab(self):
        settings_tab = SettingsTab()
        self.addTab(settings_tab, gt('Settings'))
        self.settings_tab = settings_tab
