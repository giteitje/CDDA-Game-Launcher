import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys

import cddagl.globals as globals
from cddagl.constants import READ_BUFFER_SIZE, MAX_GAME_DIRECTORIES, \
    SAVES_WARNING_SIZE, RELEASES_URL, WORLD_FILES
from cddagl.ui.AboutDialog import AboutDialog
from cddagl.ui.BackupsTab import BackupsTab
from cddagl.ui.ExceptionWindow import ExceptionWindow
from cddagl.ui.LauncherUpdateDialog import LauncherUpdateDialog
from cddagl.ui.ModsTab import ModsTab
from cddagl.ui.SoundpacksTab import SoundpacksTab
from cddagl.ui.UpdateGroupBox import UpdateGroupBox

try:
    from os import scandir
except ImportError:
    from scandir import scandir

import arrow

import gettext
_ = gettext.gettext
ngettext = gettext.ngettext

from babel.core import Locale

from io import BytesIO

import html5lib
from lxml import etree
from urllib.parse import urljoin

import rarfile

from distutils.version import LooseVersion

from pywintypes import error as PyWinError

from PyQt5.QtCore import (
    Qt, QTimer, QUrl, pyqtSignal, QByteArray, QStringListModel,
    QSize, QRect, QThread)
from PyQt5.QtGui import QIcon, QPainter, QColor, QFont
from PyQt5.QtWidgets import (
    QApplication, QWidget, QGridLayout, QGroupBox, QMainWindow,
    QVBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog, QToolButton,
    QProgressBar, QComboBox, QAction, QTabWidget, QCheckBox, QMessageBox, QStyle, QHBoxLayout,
    QSpinBox, QSizePolicy,
    QMenu)
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest

from cddagl.config import (
    get_config_value, set_config_value, new_version, get_build_from_sha256,
    new_build, config_true)
from cddagl.helpers.win32 import (
    get_ui_locale, activate_window, SimpleNamedPipe, process_id_from_path,
    wait_for_pid)
from cddagl.helpers.file_system import (
    clean_qt_path, retry_rmtree, sizeof_fmt)


from cddagl.__version__ import version

main_app = None

logger = logging.getLogger('cddagl')


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
        self.file_menu.setTitle(_('&File'))
        self.exit_action.setText(_('E&xit'))
        self.help_menu.setTitle(_('&Help'))
        if getattr(sys, 'frozen', False):
            self.update_action.setText(_('&Check for update'))
        self.about_action.setText(_('&About CDDA Game Launcher'))

        if self.about_dialog is not None:
            self.about_dialog.set_text()
        self.central_widget.set_text()

    def create_status_bar(self):
        status_bar = self.statusBar()
        status_bar.busy = 0

        status_bar.showMessage(_('Ready'))

    def create_central_widget(self):
        central_widget = CentralWidget()
        self.setCentralWidget(central_widget)
        self.central_widget = central_widget

    def create_menu(self):
        file_menu = QMenu(_('&File'))
        self.menuBar().addMenu(file_menu)
        self.file_menu = file_menu

        exit_action = QAction(_('E&xit'), self, triggered=self.close)
        file_menu.addAction(exit_action)
        self.exit_action = exit_action

        help_menu = QMenu(_('&Help'))
        self.menuBar().addMenu(help_menu)
        self.help_menu = help_menu

        if getattr(sys, 'frozen', False):
            update_action = QAction(_('&Check for update'), self,
                triggered=self.manual_update_check)
            self.update_action = update_action
            self.help_menu.addAction(update_action)
            self.help_menu.addSeparator()

        about_action = QAction(_('&About CDDA Game Launcher'), self,
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
                             encoding='utf8', method='html').decode('utf8')

                    body_divs = release.cssselect(
                        'div.release-body div.markdown-body')
                    if len(body_divs) > 0:
                        body = body_divs[0]
                        for anchor in body.cssselect('a'):
                            if 'href' in anchor.keys():
                                anchor.set('href', urljoin(RELEASES_URL,
                                    anchor.get('href')))
                        release_body = etree.tostring(body,
                            encoding='utf8', method='html').decode('utf8')

                    html_text = release_header + release_body

                    no_launcher_version_check_checkbox = QCheckBox()
                    no_launcher_version_check_checkbox.setText(_('Do not check '
                        'for new version of the CDDA Game Launcher on launch'))
                    check_state = (Qt.Checked if config_true(get_config_value(
                        'prevent_version_check_launch', 'False'))
                        else Qt.Unchecked)
                    no_launcher_version_check_checkbox.stateChanged.connect(
                        self.nlvcc_changed)
                    no_launcher_version_check_checkbox.setCheckState(
                        check_state)

                    launcher_update_msgbox = QMessageBox()
                    launcher_update_msgbox.setWindowTitle(_('Launcher update'))
                    launcher_update_msgbox.setText(_('You are using version '
                        '{version} but there is a new update for CDDA Game '
                        'Launcher. Would you like to update?').format(
                        version=version))
                    launcher_update_msgbox.setInformativeText(html_text)
                    launcher_update_msgbox.addButton(_('Update the launcher'),
                        QMessageBox.YesRole)
                    launcher_update_msgbox.addButton(_('Not right now'),
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
                                                             self, Qt.WindowTitleHint |
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
            up_to_date_msgbox.setWindowTitle(_('Up to date'))
            up_to_date_msgbox.setText(_('The CDDA Game Launcher is up to date.'
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
        #self.create_tilesets_tab()
        self.create_soundpacks_tab()
        #self.create_fonts_tab()
        self.create_settings_tab()

    def set_text(self):
        self.setTabText(self.indexOf(self.main_tab), _('Main'))
        self.setTabText(self.indexOf(self.backups_tab), _('Backups'))
        self.setTabText(self.indexOf(self.mods_tab), _('Mods'))
        #self.setTabText(self.indexOf(self.tilesets_tab), _('Tilesets'))
        self.setTabText(self.indexOf(self.soundpacks_tab), _('Soundpacks'))
        #self.setTabText(self.indexOf(self.fonts_tab), _('Fonts'))
        self.setTabText(self.indexOf(self.settings_tab), _('Settings'))

        self.main_tab.set_text()
        self.backups_tab.set_text()
        self.mods_tab.set_text()
        #self.tilesets_tab.set_text()
        self.soundpacks_tab.set_text()
        #self.fonts_tab.set_text()
        self.settings_tab.set_text()

    def create_main_tab(self):
        main_tab = MainTab()
        self.addTab(main_tab, _('Main'))
        self.main_tab = main_tab

    def create_backups_tab(self):
        backups_tab = BackupsTab()
        self.addTab(backups_tab, _('Backups'))
        self.backups_tab = backups_tab

    def create_mods_tab(self):
        mods_tab = ModsTab()
        self.addTab(mods_tab, _('Mods'))
        self.mods_tab = mods_tab

    def create_tilesets_tab(self):
        tilesets_tab = TilesetsTab()
        self.addTab(tilesets_tab, _('Tilesets'))
        self.tilesets_tab = tilesets_tab

    def create_soundpacks_tab(self):
        soundpacks_tab = SoundpacksTab()
        self.addTab(soundpacks_tab, _('Soundpacks'))
        self.soundpacks_tab = soundpacks_tab

    def create_fonts_tab(self):
        fonts_tab = FontsTab()
        self.addTab(fonts_tab, _('Fonts'))
        self.fonts_tab = fonts_tab

    def create_settings_tab(self):
        settings_tab = SettingsTab()
        self.addTab(settings_tab, _('Settings'))
        self.settings_tab = settings_tab


class MainTab(QWidget):
    def __init__(self):
        super(MainTab, self).__init__()

        game_dir_group_box = GameDirGroupBox()
        self.game_dir_group_box = game_dir_group_box

        update_group_box = UpdateGroupBox()
        self.update_group_box = update_group_box

        layout = QVBoxLayout()
        layout.addWidget(game_dir_group_box)
        layout.addWidget(update_group_box)
        self.setLayout(layout)

    def set_text(self):
        self.game_dir_group_box.set_text()
        self.update_group_box.set_text()

    def get_main_window(self):
        return self.parentWidget().parentWidget().parentWidget()

    def get_settings_tab(self):
        return self.parentWidget().parentWidget().settings_tab

    def get_soundpacks_tab(self):
        return self.parentWidget().parentWidget().soundpacks_tab

    def get_mods_tab(self):
        return self.parentWidget().parentWidget().mods_tab

    def get_backups_tab(self):
        return self.parentWidget().parentWidget().backups_tab

    def disable_tab(self):
        self.game_dir_group_box.disable_controls()
        self.update_group_box.disable_controls(True)

    def enable_tab(self):
        self.game_dir_group_box.enable_controls()
        self.update_group_box.enable_controls()


class SettingsTab(QWidget):
    def __init__(self):
        super(SettingsTab, self).__init__()

        launcher_settings_group_box = LauncherSettingsGroupBox()
        self.launcher_settings_group_box = launcher_settings_group_box

        update_settings_group_box = UpdateSettingsGroupBox()
        self.update_settings_group_box = update_settings_group_box

        layout = QVBoxLayout()
        layout.addWidget(launcher_settings_group_box)
        layout.addWidget(update_settings_group_box)
        self.setLayout(layout)

    def set_text(self):
        self.launcher_settings_group_box.set_text()
        self.update_settings_group_box.set_text()

    def get_main_window(self):
        return self.parentWidget().parentWidget().parentWidget()

    def get_main_tab(self):
        return self.parentWidget().parentWidget().main_tab

    def disable_tab(self):
        self.launcher_settings_group_box.disable_controls()
        self.update_settings_group_box.disable_controls()

    def enable_tab(self):
        self.launcher_settings_group_box.enable_controls()
        self.update_settings_group_box.enable_controls()


class GameDirGroupBox(QGroupBox):
    def __init__(self):
        super(GameDirGroupBox, self).__init__()

        self.shown = False
        self.exe_path = None
        self.restored_previous = False
        self.current_build = None

        self.exe_reading_timer = None
        self.update_saves_timer = None
        self.saves_size = 0

        self.dir_combo_inserting = False

        self.game_process = None
        self.game_process_id = None
        self.game_started = False

        layout = QGridLayout()

        dir_label = QLabel()
        layout.addWidget(dir_label, 0, 0, Qt.AlignRight)
        self.dir_label = dir_label

        dir_combo = QComboBox()
        dir_combo.setEditable(True)
        dir_combo.setInsertPolicy(QComboBox.InsertAtTop)
        dir_combo.currentIndexChanged.connect(self.dc_index_changed)
        self.dir_combo = dir_combo
        layout.addWidget(dir_combo, 0, 1)

        dir_combo_model = QStringListModel(json.loads(get_config_value(
            'game_directories', '[]')), self)
        dir_combo.setModel(dir_combo_model)
        self.dir_combo_model = dir_combo_model

        dir_change_button = QToolButton()
        dir_change_button.setText('...')
        dir_change_button.clicked.connect(self.set_game_directory)
        layout.addWidget(dir_change_button, 0, 2)
        self.dir_change_button = dir_change_button

        version_label = QLabel()       
        layout.addWidget(version_label, 1, 0, Qt.AlignRight)
        self.version_label = version_label

        version_value_label = QLineEdit()
        version_value_label.setReadOnly(True)
        layout.addWidget(version_value_label, 1, 1)
        self.version_value_label = version_value_label

        build_label = QLabel()
        layout.addWidget(build_label, 2, 0, Qt.AlignRight)
        self.build_label = build_label

        build_value_label = QLineEdit()
        build_value_label.setReadOnly(True)
        build_value_label.setText(_('Unknown'))
        layout.addWidget(build_value_label, 2, 1)
        self.build_value_label = build_value_label

        saves_label = QLabel()
        layout.addWidget(saves_label, 3, 0, Qt.AlignRight)
        self.saves_label = saves_label

        saves_value_edit = QLineEdit()
        saves_value_edit.setReadOnly(True)
        saves_value_edit.setText(_('Unknown'))
        layout.addWidget(saves_value_edit, 3, 1)
        self.saves_value_edit = saves_value_edit

        saves_warning_label = QLabel()
        icon = QApplication.style().standardIcon(QStyle.SP_MessageBoxWarning)
        saves_warning_label.setPixmap(icon.pixmap(16, 16))
        saves_warning_label.hide()
        layout.addWidget(saves_warning_label, 3, 2)
        self.saves_warning_label = saves_warning_label

        launch_game_button = QPushButton()
        
        launch_game_button.setEnabled(False)
        launch_game_button.setStyleSheet("font-size: 20px;")
        launch_game_button.clicked.connect(self.launch_game)
        layout.addWidget(launch_game_button, 4, 0, 1, 3)
        self.launch_game_button = launch_game_button

        restore_button = QPushButton()
        restore_button.setEnabled(False)
        restore_button.clicked.connect(self.restore_previous)
        layout.addWidget(restore_button, 5, 0, 1, 3)
        self.restore_button = restore_button

        self.setLayout(layout)
        self.set_text()

    def set_text(self):
        self.dir_label.setText(_('Directory:'))
        self.version_label.setText(_('Version:'))
        self.build_label.setText(_('Build:'))
        self.saves_label.setText(_('Saves:'))
        self.saves_warning_label.setToolTip(
            _('Your save directory might be large '
            'enough to cause significant delays during the update process.\n'
            'You might want to enable the "Do not copy or move the save '
            'directory" option in the settings tab.'))
        self.launch_game_button.setText(_('Launch game'))
        self.restore_button.setText(_('Restore previous version'))
        self.setTitle(_('Game'))

    def showEvent(self, event):
        if not self.shown:
            self.last_game_directory = None

            if (getattr(sys, 'frozen', False)
                and config_true(get_config_value('use_launcher_dir', 'False'))):
                game_directory = os.path.dirname(os.path.abspath(
                    os.path.realpath(sys.executable)))

                self.dir_combo.setEnabled(False)
                self.dir_change_button.setEnabled(False)

                self.set_dir_combo_value(game_directory)
            else:
                game_directory = get_config_value('game_directory')
                if game_directory is None:
                    cddagl_path = os.path.dirname(os.path.realpath(
                        sys.executable))
                    default_dir = os.path.join(cddagl_path, 'cdda')
                    game_directory = default_dir

                self.set_dir_combo_value(game_directory)

            self.game_directory_changed()

        self.shown = True

    def set_dir_combo_value(self, value):
        dir_model = self.dir_combo.model()

        index_list = dir_model.match(dir_model.index(0, 0), Qt.DisplayRole,
            value, 1, Qt.MatchFixedString)
        if len(index_list) > 0:
            self.dir_combo.setCurrentIndex(index_list[0].row())
        else:
            self.dir_combo_inserting = True
            self.dir_combo.insertItem(0, value)
            self.dir_combo_inserting = False

            self.dir_combo.setCurrentIndex(0)

    def disable_controls(self):
        self.dir_combo.setEnabled(False)
        self.dir_change_button.setEnabled(False)

        self.previous_lgb_enabled = self.launch_game_button.isEnabled()
        self.launch_game_button.setEnabled(False)
        self.previous_rb_enabled = self.restore_button.isEnabled()
        self.restore_button.setEnabled(False)

    def enable_controls(self):
        self.dir_combo.setEnabled(True)
        self.dir_change_button.setEnabled(True)
        self.launch_game_button.setEnabled(self.previous_lgb_enabled)
        self.restore_button.setEnabled(self.previous_rb_enabled)

    def restore_previous(self):
        self.disable_controls()

        main_tab = self.get_main_tab()
        update_group_box = main_tab.update_group_box
        update_group_box.disable_controls(True)

        self.restored_previous = False

        try:
            game_dir = self.dir_combo.currentText()
            previous_version_dir = os.path.join(game_dir, 'previous_version')

            if os.path.isdir(previous_version_dir) and os.path.isdir(game_dir):

                temp_dir = os.path.join(os.environ['TEMP'],
                    'CDDA Game Launcher')
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)

                temp_move_dir = os.path.join(temp_dir, 'moved')
                while os.path.exists(temp_move_dir):
                    temp_move_dir = os.path.join(temp_dir, 'moved-{0}'.format(
                        '%08x' % random.randrange(16**8)))
                os.makedirs(temp_move_dir)

                excluded_entries = set(['previous_version'])
                if config_true(get_config_value('prevent_save_move', 'False')):
                    excluded_entries.add('save')

                # Prevent moving the launcher if it's in the game directory
                if getattr(sys, 'frozen', False):
                    launcher_exe = os.path.abspath(sys.executable)
                    launcher_dir = os.path.dirname(launcher_exe)
                    if os.path.abspath(game_dir) == launcher_dir:
                        excluded_entries.add(os.path.basename(launcher_exe))

                for entry in os.listdir(game_dir):
                    if entry not in excluded_entries:
                        entry_path = os.path.join(game_dir, entry)
                        shutil.move(entry_path, temp_move_dir)

                excluded_entries = set()
                if config_true(get_config_value('prevent_save_move', 'False')):
                    excluded_entries.add('save')
                for entry in os.listdir(previous_version_dir):
                    if entry not in excluded_entries:
                        entry_path = os.path.join(previous_version_dir, entry)
                        shutil.move(entry_path, game_dir)

                for entry in os.listdir(temp_move_dir):
                    entry_path = os.path.join(temp_move_dir, entry)
                    shutil.move(entry_path, previous_version_dir)

                retry_rmtree(temp_move_dir)

                self.restored_previous = True
        except OSError as e:
            main_window = self.get_main_window()
            status_bar = main_window.statusBar()

            status_bar.showMessage(str(e))

        self.last_game_directory = None
        self.enable_controls()
        update_group_box.enable_controls()
        self.game_directory_changed()

    def focus_game(self):
        if self.game_process is None and self.game_process_id is None:
            return

        if self.game_process is not None:
            pid = self.game_process.pid
        elif self.game_process_id is not None:
            pid = self.game_process_id

        activate_window(pid)

    def launch_game(self):
        if self.game_started:
            return self.focus_game()

        if config_true(get_config_value('backup_on_launch', 'False')):
            main_tab = self.get_main_tab()
            backups_tab = main_tab.get_backups_tab()

            backups_tab.prune_auto_backups()

            name = '{auto}_{name}'.format(auto=_('auto'),
                name=_('before_launch'))

            backups_tab.after_backup = self.launch_game_process
            backups_tab.backup_saves(name)
        else:
            self.launch_game_process()

    def launch_game_process(self):
        if self.exe_path is None or not os.path.isfile(self.exe_path):
            main_window = self.get_main_window()
            status_bar = main_window.statusBar()

            status_bar.showMessage(_('Game executable not found'))
            
            self.launch_game_button.setEnabled(False)
            return
        
        self.get_main_window().setWindowState(Qt.WindowMinimized)
        exe_dir = os.path.dirname(self.exe_path)

        params = get_config_value('command.params', '').strip()
        if params != '':
            params = ' ' + params

        cmd = '"{exe_path}"{params}'.format(exe_path=self.exe_path,
            params=params)

        game_process = subprocess.Popen(cmd, cwd=exe_dir,
            startupinfo=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.game_process = game_process
        self.game_started = True

        if not config_true(get_config_value('keep_launcher_open', 'False')):
            self.get_main_window().close()
        else:
            main_window = self.get_main_window()
            status_bar = main_window.statusBar()

            status_bar.showMessage(_('Game process is running'))

            main_tab = self.get_main_tab()
            update_group_box = main_tab.update_group_box

            self.disable_controls()
            update_group_box.disable_controls(True)

            soundpacks_tab = main_tab.get_soundpacks_tab()
            mods_tab = main_tab.get_mods_tab()
            settings_tab = main_tab.get_settings_tab()
            backups_tab = main_tab.get_backups_tab()

            soundpacks_tab.disable_tab()
            mods_tab.disable_tab()
            settings_tab.disable_tab()
            backups_tab.disable_tab()

            self.launch_game_button.setText(_('Show current game'))
            self.launch_game_button.setEnabled(True)
            
            class ProcessWaitThread(QThread):
                ended = pyqtSignal()

                def __init__(self, process):
                    super(ProcessWaitThread, self).__init__()

                    self.process = process                    

                def __del__(self):
                    self.wait()

                def run(self):
                    self.process.wait()
                    self.ended.emit()

            def process_ended():
                self.process_wait_thread = None

                self.game_process = None
                self.game_started = False

                status_bar.showMessage(_('Game process has ended'))

                self.enable_controls()
                update_group_box.enable_controls()

                soundpacks_tab.enable_tab()
                mods_tab.enable_tab()
                settings_tab.enable_tab()
                backups_tab.enable_tab()

                self.launch_game_button.setText(_('Launch game'))

                self.get_main_window().setWindowState(Qt.WindowActive)

                self.update_saves()

                if config_true(get_config_value('backup_on_end', 'False')):
                    backups_tab.prune_auto_backups()

                    name = '{auto}_{name}'.format(auto=_('auto'),
                        name=_('after_end'))

                    backups_tab.backup_saves(name)

            process_wait_thread = ProcessWaitThread(self.game_process)
            process_wait_thread.ended.connect(process_ended)
            process_wait_thread.start()

            self.process_wait_thread = process_wait_thread

    def get_main_tab(self):
        return self.parentWidget()

    def get_main_window(self):
        return self.get_main_tab().get_main_window()

    def update_soundpacks(self):
        main_window = self.get_main_window()
        central_widget = main_window.central_widget
        soundpacks_tab = central_widget.soundpacks_tab

        directory = self.dir_combo.currentText()
        soundpacks_tab.game_dir_changed(directory)

    def update_mods(self):
        main_window = self.get_main_window()
        central_widget = main_window.central_widget
        mods_tab = central_widget.mods_tab

        directory = self.dir_combo.currentText()
        mods_tab.game_dir_changed(directory)

    def update_backups(self):
        main_window = self.get_main_window()
        central_widget = main_window.central_widget
        backups_tab = central_widget.backups_tab

        directory = self.dir_combo.currentText()
        backups_tab.game_dir_changed(directory)

    def clear_soundpacks(self):
        main_window = self.get_main_window()
        central_widget = main_window.central_widget
        soundpacks_tab = central_widget.soundpacks_tab

        soundpacks_tab.clear_soundpacks()

    def clear_mods(self):
        main_window = self.get_main_window()
        central_widget = main_window.central_widget
        mods_tab = central_widget.mods_tab

        mods_tab.clear_mods()

    def clear_backups(self):
        main_window = self.get_main_window()
        central_widget = main_window.central_widget
        backups_tab = central_widget.backups_tab

        backups_tab.clear_backups()

    def set_game_directory(self):
        options = QFileDialog.DontResolveSymlinks | QFileDialog.ShowDirsOnly
        directory = QFileDialog.getExistingDirectory(self,
                _('Game directory'), self.dir_combo.currentText(),
                options=options)
        if directory:
            self.set_dir_combo_value(clean_qt_path(directory))

    def dc_index_changed(self, index):
        if self.shown and not self.dir_combo_inserting:
            self.game_directory_changed()

    def game_directory_changed(self):
        directory = self.dir_combo.currentText()

        main_window = self.get_main_window()
        status_bar = main_window.statusBar()
        status_bar.clearMessage()

        self.exe_path = None
        
        main_tab = self.get_main_tab()
        update_group_box = main_tab.update_group_box

        if not os.path.isdir(directory):
            self.version_value_label.setText(_('Not a valid directory'))
        else:
            # Check for previous version
            previous_version_dir = os.path.join(directory, 'previous_version')
            self.restore_button.setEnabled(os.path.isdir(previous_version_dir))

            # Find the executable
            console_exe = os.path.join(directory, 'cataclysm.exe')
            tiles_exe = os.path.join(directory, 'cataclysm-tiles.exe')

            exe_path = None
            version_type = None
            if os.path.isfile(console_exe):
                version_type = _('console')
                exe_path = console_exe
            elif os.path.isfile(tiles_exe):
                version_type = _('tiles')
                exe_path = tiles_exe

            if version_type is None:
                self.version_value_label.setText(_('Not a CDDA directory'))
            else:
                self.exe_path = exe_path
                self.version_type = version_type
                if self.last_game_directory != directory:
                    self.update_version()
                    self.update_saves()
                    self.update_soundpacks()
                    self.update_mods()
                    self.update_backups()

        if self.exe_path is None:
            self.launch_game_button.setEnabled(False)
            update_group_box.update_button.setText(_('Install game'))
            self.restored_previous = False

            self.current_build = None
            self.build_value_label.setText(_('Unknown'))
            self.saves_value_edit.setText(_('Unknown'))
            self.clear_soundpacks()
            self.clear_mods()
            self.clear_backups()
        else:
            self.launch_game_button.setEnabled(True)
            update_group_box.update_button.setText(_('Update game'))

            self.check_running_process(self.exe_path)

        self.last_game_directory = directory
        if not (getattr(sys, 'frozen', False)
            and config_true(get_config_value('use_launcher_dir', 'False'))):
            set_config_value('game_directory', directory)

    def update_version(self):
        if (self.exe_reading_timer is not None
            and self.exe_reading_timer.isActive()):
            self.exe_reading_timer.stop()

            status_bar = main_window.statusBar()
            status_bar.removeWidget(self.reading_label)
            status_bar.removeWidget(self.reading_progress_bar)

            status_bar.busy -= 1

        main_window = self.get_main_window()

        status_bar = main_window.statusBar()
        status_bar.clearMessage()

        status_bar.busy += 1

        reading_label = QLabel()
        reading_label.setText(_('Reading: {0}').format(self.exe_path))
        status_bar.addWidget(reading_label, 100)
        self.reading_label = reading_label

        progress_bar = QProgressBar()
        status_bar.addWidget(progress_bar)
        self.reading_progress_bar = progress_bar

        timer = QTimer(self)
        self.exe_reading_timer = timer

        exe_size = os.path.getsize(self.exe_path)

        progress_bar.setRange(0, exe_size)
        self.exe_total_read = 0

        self.exe_sha256 = hashlib.sha256()
        self.last_bytes = None
        self.game_version = ''
        self.opened_exe = open(self.exe_path, 'rb')

        def timeout():
            bytes = self.opened_exe.read(READ_BUFFER_SIZE)
            if len(bytes) == 0:
                self.opened_exe.close()
                self.exe_reading_timer.stop()
                main_window = self.get_main_window()
                status_bar = main_window.statusBar()

                if self.game_version == '':
                    self.game_version = _('Unknown')
                else:
                    self.add_game_dir()

                self.version_value_label.setText(
                    _('{version} ({type})').format(version=self.game_version,
                    type=self.version_type))

                status_bar.removeWidget(self.reading_label)
                status_bar.removeWidget(self.reading_progress_bar)

                status_bar.busy -= 1
                if status_bar.busy == 0 and not self.game_started:
                    if self.restored_previous:
                        status_bar.showMessage(
                            _('Previous version restored'))
                    else:
                        status_bar.showMessage(_('Ready'))

                if status_bar.busy == 0 and self.game_started:
                    status_bar.showMessage(_('Game process is running'))

                sha256 = self.exe_sha256.hexdigest()

                new_version(self.game_version, sha256)

                build = get_build_from_sha256(sha256)

                if build is not None:
                    build_date = arrow.get(build['released_on'], 'UTC')
                    human_delta = build_date.humanize(arrow.utcnow(),
                        locale=globals.app_locale)
                    self.build_value_label.setText(_('{build} ({time_delta})'
                        ).format(build=build['build'], time_delta=human_delta))
                    self.current_build = build['build']

                    main_tab = self.get_main_tab()
                    update_group_box = main_tab.update_group_box

                    if (update_group_box.builds is not None
                        and len(update_group_box.builds) > 0
                        and status_bar.busy == 0
                        and not self.game_started):
                        last_build = update_group_box.builds[0]

                        message = status_bar.currentMessage()
                        if message != '':
                            message = message + ' - '

                        if last_build['number'] == self.current_build:
                            message = message + _('Your game is up to date')
                        else:
                            message = message + _('There is a new update '
                            'available')
                        status_bar.showMessage(message)

                else:
                    self.build_value_label.setText(_('Unknown'))
                    self.current_build = None

            else:
                last_frame = bytes
                if self.last_bytes is not None:
                    last_frame = self.last_bytes + last_frame

                match = re.search(
                    b'(?P<version>[01]\\.[A-F](-\\d+-g[0-9a-f]+)?)\\x00',
                    last_frame)
                if match is not None:
                    game_version = match.group('version').decode('ascii')
                    if len(game_version) > len(self.game_version):
                        self.game_version = game_version

                self.exe_total_read += len(bytes)
                self.reading_progress_bar.setValue(self.exe_total_read)
                self.exe_sha256.update(bytes)
                self.last_bytes = bytes

        timer.timeout.connect(timeout)
        timer.start(0)

        '''from PyQt5.QtCore import pyqtRemoveInputHook, pyqtRestoreInputHook
        pyqtRemoveInputHook()
        import pdb; pdb.set_trace()
        pyqtRestoreInputHook()'''

    def check_running_process(self, exe_path):
        pid = process_id_from_path(exe_path)

        if pid is not None:
            self.game_started = True
            self.game_process_id = pid

            main_window = self.get_main_window()
            status_bar = main_window.statusBar()

            if status_bar.busy == 0:
                status_bar.showMessage(_('Game process is running'))

            main_tab = self.get_main_tab()
            update_group_box = main_tab.update_group_box

            self.disable_controls()
            update_group_box.disable_controls(True)

            soundpacks_tab = main_tab.get_soundpacks_tab()
            mods_tab = main_tab.get_mods_tab()
            settings_tab = main_tab.get_settings_tab()
            backups_tab = main_tab.get_backups_tab()

            soundpacks_tab.disable_tab()
            mods_tab.disable_tab()
            settings_tab.disable_tab()
            backups_tab.disable_tab()

            self.launch_game_button.setText(_('Show current game'))
            self.launch_game_button.setEnabled(True)
            
            class ProcessWaitThread(QThread):
                ended = pyqtSignal()

                def __init__(self, pid):
                    super(ProcessWaitThread, self).__init__()

                    self.pid = pid

                def __del__(self):
                    self.wait()

                def run(self):
                    wait_for_pid(self.pid)
                    self.ended.emit()

            def process_ended():
                self.process_wait_thread = None

                self.game_process_id = None
                self.game_started = False

                status_bar.showMessage(_('Game process has ended'))

                self.enable_controls()
                update_group_box.enable_controls()

                soundpacks_tab.enable_tab()
                mods_tab.enable_tab()
                settings_tab.enable_tab()
                backups_tab.enable_tab()

                self.launch_game_button.setText(_('Launch game'))

                self.get_main_window().setWindowState(Qt.WindowActive)

                self.update_saves()

                if config_true(get_config_value('backup_on_end', 'False')):
                    backups_tab.prune_auto_backups()

                    name = '{auto}_{name}'.format(auto=_('auto'),
                        name=_('after_end'))

                    backups_tab.backup_saves(name)

            process_wait_thread = ProcessWaitThread(self.game_process_id)
            process_wait_thread.ended.connect(process_ended)
            process_wait_thread.start()

            self.process_wait_thread = process_wait_thread

    def add_game_dir(self):
        new_game_dir = self.dir_combo.currentText()

        game_dirs = json.loads(get_config_value('game_directories', '[]'))

        try:
            index = game_dirs.index(new_game_dir)
            if index > 0:
                del game_dirs[index]
                game_dirs.insert(0, new_game_dir)
        except ValueError:
            game_dirs.insert(0, new_game_dir)

        if len(game_dirs) > MAX_GAME_DIRECTORIES:
            del game_dirs[MAX_GAME_DIRECTORIES:]

        set_config_value('game_directories', json.dumps(game_dirs))

    def update_saves(self):
        self.game_dir = self.dir_combo.currentText()
        
        if (self.update_saves_timer is not None
            and self.update_saves_timer.isActive()):
            self.update_saves_timer.stop()
            self.saves_value_edit.setText(_('Unknown'))

        save_dir = os.path.join(self.game_dir, 'save')
        if not os.path.isdir(save_dir):
            self.saves_value_edit.setText(_('Not found'))
            return

        timer = QTimer(self)
        self.update_saves_timer = timer

        self.saves_size = 0
        self.saves_worlds = 0
        self.saves_characters = 0
        self.world_dirs = set()

        self.saves_scan = scandir(save_dir)
        self.next_scans = []
        self.save_dir = save_dir

        def timeout():
            try:
                entry = next(self.saves_scan)
                if entry.is_dir():
                    self.next_scans.append(entry.path)
                elif entry.is_file():
                    self.saves_size += entry.stat().st_size

                    if entry.name.endswith('.sav'):
                        world_dir = os.path.dirname(entry.path)
                        if self.save_dir == os.path.dirname(world_dir):
                            self.saves_characters += 1

                    if entry.name in WORLD_FILES:
                        world_dir = os.path.dirname(entry.path)
                        if (world_dir not in self.world_dirs
                            and self.save_dir == os.path.dirname(world_dir)):
                            self.world_dirs.add(world_dir)
                            self.saves_worlds += 1

                worlds_text = ngettext('World', 'Worlds', self.saves_worlds)

                characters_text = ngettext('Character', 'Characters',
                    self.saves_characters)

                self.saves_value_edit.setText(_('{world_count} {worlds} - '
                    '{character_count} {characters} ({size})').format(
                    world_count=self.saves_worlds,
                    character_count=self.saves_characters,
                    size=sizeof_fmt(self.saves_size),
                    worlds=worlds_text,
                    characters=characters_text))
            except StopIteration:
                if len(self.next_scans) > 0:
                    self.saves_scan = scandir(self.next_scans.pop())
                else:
                    # End of the tree
                    self.update_saves_timer.stop()
                    self.update_saves_timer = None

                    # Warning about saves size
                    if (self.saves_size > SAVES_WARNING_SIZE and
                        not config_true(get_config_value('prevent_save_move',
                            'False'))):
                        self.saves_warning_label.show()
                    else:
                        self.saves_warning_label.hide()

        timer.timeout.connect(timeout)
        timer.start(0)

    def analyse_new_build(self, build):
        game_dir = self.dir_combo.currentText()

        self.previous_exe_path = self.exe_path
        self.exe_path = None

        # Check for previous version
        previous_version_dir = os.path.join(game_dir, 'previous_version')
        self.previous_rb_enabled = os.path.isdir(previous_version_dir)

        console_exe = os.path.join(game_dir, 'cataclysm.exe')
        tiles_exe = os.path.join(game_dir, 'cataclysm-tiles.exe')

        exe_path = None
        version_type = None
        if os.path.isfile(console_exe):
            version_type = _('console')
            exe_path = console_exe
        elif os.path.isfile(tiles_exe):
            version_type = _('tiles')
            exe_path = tiles_exe

        if version_type is None:
            self.version_value_label.setText(_('Not a CDDA directory'))
            self.build_value_label.setText(_('Unknown'))
            self.current_build = None
            
            main_tab = self.get_main_tab()
            update_group_box = main_tab.update_group_box
            update_group_box.finish_updating()

            self.launch_game_button.setEnabled(False)
            
            main_window = self.get_main_window()
            status_bar = main_window.statusBar()
            status_bar.showMessage(_('No executable found in the downloaded '
                'archive. You might want to restore your previous version.'))
            
        else:
            if (self.exe_reading_timer is not None
                and self.exe_reading_timer.isActive()):
                self.exe_reading_timer.stop()

                status_bar = main_window.statusBar()
                status_bar.removeWidget(self.reading_label)
                status_bar.removeWidget(self.reading_progress_bar)

                status_bar.busy -= 1

            self.exe_path = exe_path
            self.version_type = version_type
            self.build_number = build['number']
            self.build_date = build['date']

            main_window = self.get_main_window()

            status_bar = main_window.statusBar()
            status_bar.clearMessage()

            status_bar.busy += 1

            reading_label = QLabel()
            reading_label.setText(_('Reading: {0}').format(self.exe_path))
            status_bar.addWidget(reading_label, 100)
            self.reading_label = reading_label

            progress_bar = QProgressBar()
            status_bar.addWidget(progress_bar)
            self.reading_progress_bar = progress_bar

            timer = QTimer(self)
            self.exe_reading_timer = timer

            exe_size = os.path.getsize(self.exe_path)

            progress_bar.setRange(0, exe_size)
            self.exe_total_read = 0

            self.exe_sha256 = hashlib.sha256()
            self.last_bytes = None
            self.game_version = ''
            self.opened_exe = open(self.exe_path, 'rb')

            def timeout():
                bytes = self.opened_exe.read(READ_BUFFER_SIZE)
                if len(bytes) == 0:
                    self.opened_exe.close()
                    self.exe_reading_timer.stop()
                    main_window = self.get_main_window()
                    status_bar = main_window.statusBar()

                    if self.game_version == '':
                        self.game_version = _('Unknown')
                    self.version_value_label.setText(
                        _('{version} ({type})').format(
                            version=self.game_version,
                            type=self.version_type))

                    build_date = arrow.get(self.build_date, 'UTC')
                    human_delta = build_date.humanize(arrow.utcnow(),
                        locale=globals.app_locale)
                    self.build_value_label.setText(_('{build} ({time_delta})'
                        ).format(build=self.build_number,
                            time_delta=human_delta))
                    self.current_build = self.build_number

                    status_bar.removeWidget(self.reading_label)
                    status_bar.removeWidget(self.reading_progress_bar)

                    status_bar.busy -= 1

                    sha256 = self.exe_sha256.hexdigest()

                    new_build(self.game_version, sha256, self.build_number,
                        self.build_date)

                    main_tab = self.get_main_tab()
                    update_group_box = main_tab.update_group_box

                    update_group_box.post_extraction()

                else:
                    last_frame = bytes
                    if self.last_bytes is not None:
                        last_frame = self.last_bytes + last_frame

                    match = re.search(
                        b'(?P<version>[01]\\.[A-F](-\\d+-g[0-9a-f]+)?)\\x00',
                        last_frame)
                    if match is not None:
                        game_version = match.group('version').decode('ascii')
                        if len(game_version) > len(self.game_version):
                            self.game_version = game_version

                    self.exe_total_read += len(bytes)
                    self.reading_progress_bar.setValue(self.exe_total_read)
                    self.exe_sha256.update(bytes)
                    self.last_bytes = bytes

            timer.timeout.connect(timeout)
            timer.start(0)

        if self.exe_path is None:
            self.previous_lgb_enabled = False
        else:
            self.previous_lgb_enabled = True


class LauncherSettingsGroupBox(QGroupBox):
    def __init__(self):
        super(LauncherSettingsGroupBox, self).__init__()

        layout = QGridLayout()

        command_line_parameters_label = QLabel()
        layout.addWidget(command_line_parameters_label, 0, 0, Qt.AlignRight)
        self.command_line_parameters_label = command_line_parameters_label

        command_line_parameters_edit = QLineEdit()
        command_line_parameters_edit.setText(get_config_value('command.params',
            ''))
        command_line_parameters_edit.editingFinished.connect(
            self.clp_changed)
        layout.addWidget(command_line_parameters_edit, 0, 1)
        self.command_line_parameters_edit = command_line_parameters_edit

        keep_launcher_open_checkbox = QCheckBox()
        check_state = (Qt.Checked if config_true(get_config_value(
            'keep_launcher_open', 'False')) else Qt.Unchecked)
        keep_launcher_open_checkbox.setCheckState(check_state)
        keep_launcher_open_checkbox.stateChanged.connect(self.klo_changed)
        layout.addWidget(keep_launcher_open_checkbox, 1, 0, 1, 2)
        self.keep_launcher_open_checkbox = keep_launcher_open_checkbox

        locale_group = QWidget()
        locale_group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        locale_layout = QHBoxLayout()
        locale_layout.setContentsMargins(0, 0, 0, 0)

        locale_label = QLabel()       
        locale_layout.addWidget(locale_label)
        self.locale_label = locale_label

        current_locale = get_config_value('locale', None)

        locale_combo = QComboBox()
        locale_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        locale_combo.addItem(_('System language or best match ({locale})'
            ).format(locale=get_ui_locale()), None)
        selected_index = 0
        for index, locale in enumerate(globals.available_locales):
            if locale == current_locale:
                selected_index = index + 1
            locale = Locale.parse(locale)
            locale_name = locale.display_name
            english_name = locale.english_name
            if locale_name != english_name:
                formatted_name = _('{locale_name} - {english_name}'
                    ).format(locale_name=locale_name, english_name=english_name)
            else:
                formatted_name = locale_name
            locale_combo.addItem(formatted_name, str(locale))
        locale_combo.setCurrentIndex(selected_index)
        locale_combo.currentIndexChanged.connect(self.locale_combo_changed)
        locale_layout.addWidget(locale_combo)
        self.locale_combo = locale_combo

        locale_group.setLayout(locale_layout)
        layout.addWidget(locale_group, 2, 0, 1, 2)
        self.locale_group = locale_group
        self.locale_layout = locale_layout

        allow_mul_insts_checkbox = QCheckBox()
        check_state = (Qt.Checked if config_true(get_config_value(
            'allow_multiple_instances', 'False')) else Qt.Unchecked)
        allow_mul_insts_checkbox.setCheckState(check_state)
        allow_mul_insts_checkbox.stateChanged.connect(self.ami_changed)
        layout.addWidget(allow_mul_insts_checkbox, 3, 0, 1, 2)
        self.allow_mul_insts_checkbox = allow_mul_insts_checkbox

        if getattr(sys, 'frozen', False):
            use_launcher_dir_checkbox = QCheckBox()
            check_state = (Qt.Checked if config_true(get_config_value(
                'use_launcher_dir', 'False')) else Qt.Unchecked)
            use_launcher_dir_checkbox.setCheckState(check_state)
            use_launcher_dir_checkbox.stateChanged.connect(self.uld_changed)
            layout.addWidget(use_launcher_dir_checkbox, 4, 0, 1, 2)
            self.use_launcher_dir_checkbox = use_launcher_dir_checkbox
        
            no_launcher_version_check_checkbox = QCheckBox()
            check_state = (Qt.Checked if config_true(get_config_value(
                'prevent_version_check_launch', 'False'))
                else Qt.Unchecked)
            no_launcher_version_check_checkbox.setCheckState(
                check_state)
            no_launcher_version_check_checkbox.stateChanged.connect(
                self.nlvcc_changed)
            layout.addWidget(no_launcher_version_check_checkbox, 5, 0, 1, 2)
            self.no_launcher_version_check_checkbox = (
                no_launcher_version_check_checkbox)

        self.setLayout(layout)
        self.set_text()

    def get_main_tab(self):
        return self.parentWidget().get_main_tab()

    def set_text(self):
        self.command_line_parameters_label.setText(
            _('Command line parameters:'))
        self.keep_launcher_open_checkbox.setText(
            _('Keep the launcher opened after launching the game'))
        self.locale_label.setText(_('Language:'))
        self.locale_combo.setItemText(0,
            _('System language or best match ({locale})').format(
                locale=get_ui_locale()))
        self.allow_mul_insts_checkbox.setText(_('Allow multiple instances of '
            'the launcher to be started'))
        if getattr(sys, 'frozen', False):
            self.use_launcher_dir_checkbox.setText(_('Use the launcher '
                'directory as the game directory'))
            self.no_launcher_version_check_checkbox.setText(_('Do not check '
                'for new version of the CDDA Game Launcher on launch'))
        self.setTitle(_('Launcher'))

    def locale_combo_changed(self, index):
        locale = self.locale_combo.currentData()
        set_config_value('locale', str(locale))

        if locale is not None:
            init_gettext(locale)
        else:
            preferred_locales = []

            system_locale = get_ui_locale()
            if system_locale is not None:
                preferred_locales.append(system_locale)

            locale = Locale.negotiate(preferred_locales, globals.available_locales)
            if locale is None:
                locale = 'en'
            else:
                locale = str(locale)
            init_gettext(locale)

        main_app.main_win.set_text()

        central_widget = main_app.main_win.central_widget
        main_tab = central_widget.main_tab
        game_dir_group_box = main_tab.game_dir_group_box
        update_group_box = main_tab.update_group_box

        game_dir_group_box.last_game_directory = None
        game_dir_group_box.game_directory_changed()

        update_group_box.refresh_builds()

    def nlvcc_changed(self, state):
        set_config_value('prevent_version_check_launch',
            str(state != Qt.Unchecked))

    def klo_changed(self, state):
        checked = state != Qt.Unchecked

        set_config_value('keep_launcher_open', str(checked))

        backup_on_end = (Qt.Checked if config_true(get_config_value(
            'backup_on_end', 'False')) else Qt.Unchecked)

        backups_tab = self.get_main_tab().get_backups_tab()

        if not (backup_on_end and not checked):
            backups_tab.backup_on_end_warning_label.hide()
        else:
            backups_tab.backup_on_end_warning_label.show()

    def clp_changed(self):
        set_config_value('command.params',
            self.command_line_parameters_edit.text())

    def ami_changed(self, state):
        checked = state != Qt.Unchecked
        set_config_value('allow_multiple_instances', str(checked))

    def uld_changed(self, state):
        checked = state != Qt.Unchecked
        set_config_value('use_launcher_dir', str(checked))

        central_widget = main_app.main_win.central_widget
        main_tab = central_widget.main_tab
        game_dir_group_box = main_tab.game_dir_group_box

        game_dir_group_box.dir_combo.setEnabled(not checked)
        game_dir_group_box.dir_change_button.setEnabled(not checked)

        game_dir_group_box.last_game_directory = None

        if getattr(sys, 'frozen', False) and checked:
            game_directory = os.path.dirname(os.path.abspath(
                os.path.realpath(sys.executable)))

            game_dir_group_box.set_dir_combo_value(game_directory)
        else:
            game_directory = get_config_value('game_directory')
            if game_directory is None:
                cddagl_path = os.path.dirname(os.path.realpath(
                    sys.executable))
                default_dir = os.path.join(cddagl_path, 'cdda')
                game_directory = default_dir

            game_dir_group_box.set_dir_combo_value(game_directory)

    def disable_controls(self):
        self.locale_combo.setEnabled(False)
        if getattr(sys, 'frozen', False):
            self.use_launcher_dir_checkbox.setEnabled(False)

    def enable_controls(self):
        self.locale_combo.setEnabled(True)
        if getattr(sys, 'frozen', False):
            self.use_launcher_dir_checkbox.setEnabled(True)


class UpdateSettingsGroupBox(QGroupBox):
    def __init__(self):
        super(UpdateSettingsGroupBox, self).__init__()

        layout = QGridLayout()

        prevent_save_move_checkbox = QCheckBox()
        check_state = (Qt.Checked if config_true(get_config_value(
            'prevent_save_move', 'False')) else Qt.Unchecked)
        prevent_save_move_checkbox.setCheckState(check_state)
        prevent_save_move_checkbox.stateChanged.connect(self.psmc_changed)
        layout.addWidget(prevent_save_move_checkbox, 0, 0, 1, 3)
        self.prevent_save_move_checkbox = prevent_save_move_checkbox

        keep_archive_copy_checkbox = QCheckBox()
        check_state = (Qt.Checked if config_true(get_config_value(
            'keep_archive_copy', 'False')) else Qt.Unchecked)
        keep_archive_copy_checkbox.setCheckState(check_state)
        keep_archive_copy_checkbox.stateChanged.connect(self.kacc_changed)
        layout.addWidget(keep_archive_copy_checkbox, 1, 0)
        self.keep_archive_copy_checkbox = keep_archive_copy_checkbox

        keep_archive_directory_line = QLineEdit()
        keep_archive_directory_line.setText(get_config_value(
            'archive_directory', ''))
        keep_archive_directory_line.editingFinished.connect(
            self.ka_directory_changed)
        layout.addWidget(keep_archive_directory_line, 1, 1)
        self.keep_archive_directory_line = keep_archive_directory_line

        ka_dir_change_button = QToolButton()
        ka_dir_change_button.setText('...')
        ka_dir_change_button.clicked.connect(self.set_ka_directory)
        layout.addWidget(ka_dir_change_button, 1, 2)
        self.ka_dir_change_button = ka_dir_change_button

        arb_timer = QTimer()
        arb_timer.setInterval(int(get_config_value(
            'auto_refresh_builds_minutes', '30')) * 1000 * 60)
        arb_timer.timeout.connect(self.arb_timeout)
        self.arb_timer = arb_timer
        if config_true(get_config_value('auto_refresh_builds', 'False')):
            arb_timer.start()

        arb_group = QWidget()
        arb_group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        arb_layout = QHBoxLayout()
        arb_layout.setContentsMargins(0, 0, 0, 0)

        auto_refresh_builds_checkbox = QCheckBox()
        check_state = (Qt.Checked if config_true(get_config_value(
            'auto_refresh_builds', 'False')) else Qt.Unchecked)
        auto_refresh_builds_checkbox.setCheckState(check_state)
        auto_refresh_builds_checkbox.stateChanged.connect(self.arbc_changed)
        arb_layout.addWidget(auto_refresh_builds_checkbox)
        self.auto_refresh_builds_checkbox = auto_refresh_builds_checkbox

        arb_min_spinbox = QSpinBox()
        arb_min_spinbox.setMinimum(1)
        arb_min_spinbox.setValue(int(get_config_value(
            'auto_refresh_builds_minutes', '30')))
        arb_min_spinbox.valueChanged.connect(self.ams_changed)
        arb_layout.addWidget(arb_min_spinbox)
        self.arb_min_spinbox = arb_min_spinbox

        arb_min_label = QLabel()
        arb_layout.addWidget(arb_min_label)
        self.arb_min_label = arb_min_label

        arb_group.setLayout(arb_layout)
        layout.addWidget(arb_group, 2, 0, 1, 3)
        self.arb_group = arb_group
        self.arb_layout = arb_layout

        self.setLayout(layout)
        self.set_text()

    def set_text(self):
        self.prevent_save_move_checkbox.setText(
            _('Do not copy or move the save directory'))
        self.prevent_save_move_checkbox.setToolTip(
            _('If your save directory size is '
            'large, it might take a long time to copy it during the update '
            'process.\nThis option might help you speed the whole thing but '
            'your previous version will lack the save directory.'))
        self.keep_archive_copy_checkbox.setText(
            _('Keep a copy of the downloaded '
            'archive in the following directory:'))
        self.auto_refresh_builds_checkbox.setText(
            _('Automatically refresh builds list every'))
        self.arb_min_label.setText(_('minutes'))
        self.setTitle(_('Update/Installation'))

    def get_settings_tab(self):
        return self.parentWidget()

    def get_main_tab(self):
        return self.get_settings_tab().get_main_tab()

    def arb_timeout(self):
        main_tab = self.get_main_tab()
        update_group_box = main_tab.update_group_box
        refresh_builds_button = update_group_box.refresh_builds_button

        if refresh_builds_button.isEnabled():
            update_group_box.refresh_builds()

    def ams_changed(self, value):
        set_config_value('auto_refresh_builds_minutes', value)
        self.arb_timer.setInterval(value * 1000 * 60)

    def arbc_changed(self, state):
        set_config_value('auto_refresh_builds', str(state != Qt.Unchecked))
        if state != Qt.Unchecked:
            self.arb_timer.start()
        else:
            self.arb_timer.stop()

    def psmc_changed(self, state):
        set_config_value('prevent_save_move', str(state != Qt.Unchecked))
        game_dir_group_box = self.get_main_tab().game_dir_group_box
        saves_warning_label = game_dir_group_box.saves_warning_label

        if state != Qt.Unchecked:
            saves_warning_label.hide()
        else:
            if game_dir_group_box.saves_size > SAVES_WARNING_SIZE:
                saves_warning_label.show()
            else:
                saves_warning_label.hide()

    def kacc_changed(self, state):
        set_config_value('keep_archive_copy', str(state != Qt.Unchecked))

    def set_ka_directory(self):
        options = QFileDialog.DontResolveSymlinks | QFileDialog.ShowDirsOnly
        directory = QFileDialog.getExistingDirectory(self,
                _('Archive directory'), self.keep_archive_directory_line.text(),
                options=options)
        if directory:
            self.keep_archive_directory_line.setText(clean_qt_path(directory))
            self.ka_directory_changed()

    def ka_directory_changed(self):
        set_config_value('archive_directory',
            self.keep_archive_directory_line.text())

    def disable_controls(self):
        pass

    def enable_controls(self):
        pass


class TilesetsTab(QTabWidget):
    def __init__(self):
        super(TilesetsTab, self).__init__()

    def set_text(self):
        pass

    def get_main_window(self):
        return self.parentWidget().parentWidget().parentWidget()

    def get_main_tab(self):
        return self.parentWidget().parentWidget().main_tab


class FontsTab(QTabWidget):
    def __init__(self):
        super(FontsTab, self).__init__()

        layout = QGridLayout()

        font_window = CataWindow(4, 4, QFont('Consolas'), 18, 9, 18, False)
        layout.addWidget(font_window, 0, 0)
        self.font_window = font_window

        self.setLayout(layout)

    def set_text(self):
        pass

    def get_main_window(self):
        return self.parentWidget().parentWidget().parentWidget()

    def get_main_tab(self):
        return self.parentWidget().parentWidget().main_tab


class CataWindow(QWidget):
    def __init__(self, terminalwidth, terminalheight, font, fontsize, fontwidth,
            fontheight, fontblending):
        super(CataWindow, self).__init__()

        self.terminalwidth = terminalwidth
        self.terminalheight = terminalheight

        self.cfont = font
        self.fontsize = fontsize
        self.cfont.setPixelSize(fontsize)
        self.cfont.setStyle(QFont.StyleNormal)
        self.fontwidth = fontwidth
        self.fontheight = fontheight
        self.fontblending = fontblending

        #self.text = '@@@\nBBB\n@@@\nCCC'
        self.text = '####\n####\n####\n####\n'

    def sizeHint(self):
        return QSize(self.terminalwidth * self.fontwidth,
            self.terminalheight * self.fontheight)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(0, 0, self.width(), self.height(), QColor(0, 0, 0))
        painter.setPen(QColor(99, 99, 99));
        painter.setFont(self.cfont)

        term_x = 0
        term_y = 0
        for char in self.text:
            if char == '\n':
                term_y += 1
                term_x = 0
                continue
            x = self.fontwidth * term_x
            y = self.fontheight * term_y

            rect = QRect(x, y, self.fontwidth, self.fontheight)
            painter.drawText(rect, 0, char)

            term_x += 1

        x = self.fontwidth * term_x
        y = self.fontheight * term_y

        rect = QRect(x, y, self.fontwidth, self.fontheight)

        painter.fillRect(rect, Qt.green)


def init_gettext(locale):
    locale_dir = os.path.join(globals.basedir, 'cddagl', 'locale')

    try:
        t = gettext.translation('cddagl', localedir=locale_dir,
            languages=[locale])
        global _
        _ = t.gettext
        global ngettext
        ngettext = t.ngettext
    except FileNotFoundError as e:
        logger.warning(_('Could not find translations for {locale} in '
            '{locale_dir} ({info})'
            ).format(locale=locale, locale_dir=locale_dir, info=str(e)))

    globals.app_locale = locale

def start_ui(bdir, locale, locales, single_instance):
    global main_app

    globals.basedir = bdir
    globals.available_locales = locales

    init_gettext(locale)

    if getattr(sys, 'frozen', False):
        rarfile.UNRAR_TOOL = os.path.join(bdir, 'UnRAR.exe')

    main_app = QApplication(sys.argv)

    launcher_icon_path = os.path.join(globals.basedir, 'cddagl', 'resources',
        'launcher.ico')
    main_app.setWindowIcon(QIcon(launcher_icon_path))

    main_win = MainWindow('CDDA Game Launcher')
    main_win.show()

    main_app.main_win = main_win
    main_app.single_instance = single_instance
    sys.exit(main_app.exec_())

def ui_exception(extype, value, tb):
    global main_app

    main_app.closeAllWindows()
    ex_win = ExceptionWindow(extype, value, tb)
    ex_win.show()
    main_app.ex_win = ex_win
