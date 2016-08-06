import gettext
import json
import os
import random
import re
import shutil
import sys
import zipfile
from datetime import datetime
from io import BytesIO
from urllib.parse import urljoin

_ = gettext.gettext

import arrow
import html5lib
from PyQt5.QtCore import Qt, QUrl, QFileInfo, QThread, pyqtSignal, QTimer
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import QGroupBox, QGridLayout, QLabel, QButtonGroup, \
    QRadioButton, QComboBox, QToolButton, QPushButton, QMessageBox, QProgressBar

from cddagl import globals as globals
from cddagl.config import get_config_value, config_true, set_config_value
from cddagl.constants import BASE_URLS
from cddagl.helpers.file_system import retry_rmtree, sizeof_fmt
from cddagl.helpers.win32 import is_64_windows
from cddagl.ui.ProgressCopyTree import ProgressCopyTree

class UpdateGroupBox(QGroupBox):
    def __init__(self):
        super(UpdateGroupBox, self).__init__()

        self.shown = False
        self.updating = False
        self.close_after_update = False
        self.builds = []
        self.progress_copy = None

        self.qnam = QNetworkAccessManager()
        self.http_reply = None

        layout = QGridLayout()

        graphics_label = QLabel()
        layout.addWidget(graphics_label, 0, 0, Qt.AlignRight)
        self.graphics_label = graphics_label

        graphics_button_group = QButtonGroup()
        self.graphics_button_group = graphics_button_group

        tiles_radio_button = QRadioButton()
        layout.addWidget(tiles_radio_button, 0, 1)
        self.tiles_radio_button = tiles_radio_button
        graphics_button_group.addButton(tiles_radio_button)

        console_radio_button = QRadioButton()
        layout.addWidget(console_radio_button, 0, 2)
        self.console_radio_button = console_radio_button
        graphics_button_group.addButton(console_radio_button)

        graphics_button_group.buttonClicked.connect(self.graphics_clicked)

        platform_label = QLabel()
        layout.addWidget(platform_label, 1, 0, Qt.AlignRight)
        self.platform_label = platform_label

        platform_button_group = QButtonGroup()
        self.platform_button_group = platform_button_group

        x64_radio_button = QRadioButton()
        layout.addWidget(x64_radio_button, 1, 1)
        self.x64_radio_button = x64_radio_button
        platform_button_group.addButton(x64_radio_button)

        platform_button_group.buttonClicked.connect(self.platform_clicked)

        if not is_64_windows():
            x64_radio_button.setEnabled(False)

        x86_radio_button = QRadioButton()
        layout.addWidget(x86_radio_button, 1, 2)
        self.x86_radio_button = x86_radio_button
        platform_button_group.addButton(x86_radio_button)

        available_builds_label = QLabel()
        layout.addWidget(available_builds_label, 2, 0, Qt.AlignRight)
        self.available_builds_label = available_builds_label

        builds_combo = QComboBox()
        builds_combo.setEnabled(False)
        builds_combo.addItem(_('Unknown'))
        layout.addWidget(builds_combo, 2, 1, 1, 2)
        self.builds_combo = builds_combo

        refresh_builds_button = QToolButton()
        refresh_builds_button.clicked.connect(self.refresh_builds)
        layout.addWidget(refresh_builds_button, 2, 3)
        self.refresh_builds_button = refresh_builds_button

        update_button = QPushButton()
        update_button.setEnabled(False)
        update_button.setStyleSheet('font-size: 20px;')
        update_button.clicked.connect(self.update_game)
        layout.addWidget(update_button, 3, 0, 1, 4)
        self.update_button = update_button

        layout.setColumnStretch(1, 100)
        layout.setColumnStretch(2, 100)

        self.setLayout(layout)
        self.set_text()

    def set_text(self):
        self.graphics_label.setText(_('Graphics:'))
        self.tiles_radio_button.setText(_('Tiles'))
        self.console_radio_button.setText(_('Console'))
        self.platform_label.setText(_('Platform:'))
        self.x64_radio_button.setText(_('Windows x64 (64-bit)'))
        self.x86_radio_button.setText(_('Windows x86 (32-bit)'))
        self.available_builds_label.setText(_('Available builds:'))
        self.refresh_builds_button.setText(_('Refresh'))
        self.update_button.setText(_('Update game'))
        self.setTitle(_('Update/Installation'))

    def showEvent(self, event):
        if not self.shown:
            graphics = get_config_value('graphics')
            if graphics is None:
                graphics = 'Tiles'

            platform = get_config_value('platform')

            if platform == 'Windows x64':
                platform = 'x64'
            elif platform == 'Windows x86':
                platform = 'x86'

            if platform is None or platform not in ('x64', 'x86'):
                if is_64_windows():
                    platform = 'x64'
                else:
                    platform = 'x86'

            if graphics == 'Tiles':
                self.tiles_radio_button.setChecked(True)
            elif graphics == 'Console':
                self.console_radio_button.setChecked(True)

            if platform == 'x64':
                self.x64_radio_button.setChecked(True)
            elif platform == 'x86':
                self.x86_radio_button.setChecked(True)

            self.start_lb_request(BASE_URLS[graphics][platform])

        self.shown = True

    def update_game(self):
        if not self.updating:
            self.updating = True
            self.download_aborted = False
            self.backing_up_game = False
            self.extracting_new_build = False
            self.analysing_new_build = False
            self.in_post_extraction = False

            self.selected_build = self.builds[self.builds_combo.currentIndex()]

            main_tab = self.get_main_tab()
            game_dir_group_box = main_tab.game_dir_group_box

            latest_build = self.builds[0]
            if game_dir_group_box.current_build == latest_build['number']:
                confirm_msgbox = QMessageBox()
                confirm_msgbox.setWindowTitle(_('Game is up to date'))
                confirm_msgbox.setText(_('You already have the latest version.'
                    ))
                confirm_msgbox.setInformativeText(_('Are you sure you want to '
                    'update your game?'))
                confirm_msgbox.addButton(_('Update the game again'),
                    QMessageBox.YesRole)
                confirm_msgbox.addButton(_('I do not need to update the '
                    'game again'), QMessageBox.NoRole)
                confirm_msgbox.setIcon(QMessageBox.Question)

                if confirm_msgbox.exec() == 1:
                    self.updating = False
                    return

            game_dir_group_box.disable_controls()
            self.disable_controls()

            soundpacks_tab = main_tab.get_soundpacks_tab()
            mods_tab = main_tab.get_mods_tab()
            settings_tab = main_tab.get_settings_tab()
            backups_tab = main_tab.get_backups_tab()

            soundpacks_tab.disable_tab()
            mods_tab.disable_tab()
            settings_tab.disable_tab()
            backups_tab.disable_tab()

            game_dir = game_dir_group_box.dir_combo.currentText()

            try:
                if not os.path.exists(game_dir):
                    os.makedirs(game_dir)
                elif os.path.isfile(game_dir):
                    main_window = self.get_main_window()
                    status_bar = main_window.statusBar()

                    status_bar.showMessage(_('Cannot install game on a file'))

                    self.finish_updating()
                    return

                temp_dir = os.path.join(os.environ['TEMP'],
                    'CDDA Game Launcher')
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)

                download_dir = os.path.join(temp_dir, 'newbuild')
                while os.path.exists(download_dir):
                    download_dir = os.path.join(temp_dir, 'newbuild-{0}'.format(
                        '%08x' % random.randrange(16**8)))
                os.makedirs(download_dir)

                download_url = self.selected_build['url']

                url = QUrl(download_url)
                file_info = QFileInfo(url.path())
                file_name = file_info.fileName()

                self.downloaded_file = os.path.join(download_dir, file_name)
                self.downloading_file = open(self.downloaded_file, 'wb')

                self.download_game_update(download_url)

            except OSError as e:
                main_window = self.get_main_window()
                status_bar = main_window.statusBar()

                self.finish_updating()

                status_bar.showMessage(str(e))
        else:
            main_tab = self.get_main_tab()
            game_dir_group_box = main_tab.game_dir_group_box

            # Are we downloading the file?
            if self.download_http_reply.isRunning():
                self.download_aborted = True
                self.download_http_reply.abort()

                main_window = self.get_main_window()

                status_bar = main_window.statusBar()

                if game_dir_group_box.exe_path is not None:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Update cancelled'))
                else:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Installation cancelled'))
            elif self.backing_up_game:
                self.backup_timer.stop()

                main_window = self.get_main_window()
                status_bar = main_window.statusBar()

                status_bar.removeWidget(self.backup_label)
                status_bar.removeWidget(self.backup_progress_bar)

                status_bar.busy -= 1

                self.restore_backup()

                if game_dir_group_box.exe_path is not None:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Update cancelled'))
                else:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Installation cancelled'))

            elif self.extracting_new_build:
                self.extracting_timer.stop()

                main_window = self.get_main_window()
                status_bar = main_window.statusBar()

                status_bar.removeWidget(self.extracting_label)
                status_bar.removeWidget(self.extracting_progress_bar)

                status_bar.busy -= 1

                self.extracting_zipfile.close()

                download_dir = os.path.dirname(self.downloaded_file)
                retry_rmtree(download_dir)

                path = self.clean_game_dir()
                self.restore_backup()
                self.restore_previous_content(path)

                if game_dir_group_box.exe_path is not None:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Update cancelled'))
                else:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Installation cancelled'))
            elif self.analysing_new_build:
                game_dir_group_box.opened_exe.close()
                game_dir_group_box.exe_reading_timer.stop()

                main_window = self.get_main_window()
                status_bar = main_window.statusBar()

                status_bar.removeWidget(game_dir_group_box.reading_label)
                status_bar.removeWidget(game_dir_group_box.reading_progress_bar)

                status_bar.busy -= 1

                path = self.clean_game_dir()
                self.restore_backup()
                self.restore_previous_content(path)

                if game_dir_group_box.exe_path is not None:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Update cancelled'))
                else:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Installation cancelled'))
            elif self.in_post_extraction:
                self.in_post_extraction = False

                if self.progress_copy is not None:
                    self.progress_copy.stop()

                main_window = self.get_main_window()
                status_bar = main_window.statusBar()
                status_bar.clearMessage()

                path = self.clean_game_dir()
                self.restore_backup()
                self.restore_previous_content(path)

                if game_dir_group_box.exe_path is not None:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Update cancelled'))
                else:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Installation cancelled'))

            self.finish_updating()

    def clean_game_dir(self):
        game_dir = self.game_dir
        dir_list = os.listdir(game_dir)
        if len(dir_list) == 0 or (
            len(dir_list) == 1 and dir_list[0] == 'previous_version'):
            return None

        temp_dir = os.path.join(os.environ['TEMP'], 'CDDA Game Launcher')
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
        for entry in dir_list:
            if entry not in excluded_entries:
                entry_path = os.path.join(game_dir, entry)
                shutil.move(entry_path, temp_move_dir)

        return temp_move_dir

    def restore_previous_content(self, path):
        if path is None:
            return

        game_dir = self.game_dir
        previous_version_dir = os.path.join(game_dir, 'previous_version')
        if not os.path.exists(previous_version_dir):
            os.makedirs(previous_version_dir)

        for entry in os.listdir(path):
            entry_path = os.path.join(path, entry)
            shutil.move(entry_path, previous_version_dir)

    def restore_backup(self):
        game_dir = self.game_dir
        previous_version_dir = os.path.join(game_dir, 'previous_version')

        if os.path.isdir(previous_version_dir) and os.path.isdir(game_dir):

            for entry in os.listdir(previous_version_dir):
                if (entry == 'save' and
                    config_true(get_config_value('prevent_save_move',
                        'False'))):
                    continue
                entry_path = os.path.join(previous_version_dir, entry)
                shutil.move(entry_path, game_dir)

            retry_rmtree(previous_version_dir)

    def get_main_tab(self):
        return self.parentWidget()

    def get_main_window(self):
        return self.get_main_tab().get_main_window()

    def disable_controls(self, update_button=False):
        self.tiles_radio_button.setEnabled(False)
        self.console_radio_button.setEnabled(False)
        self.x64_radio_button.setEnabled(False)
        self.x86_radio_button.setEnabled(False)

        self.previous_bc_enabled = self.builds_combo.isEnabled()
        self.builds_combo.setEnabled(False)
        self.refresh_builds_button.setEnabled(False)

        self.previous_ub_enabled = self.update_button.isEnabled()
        if update_button:
            self.update_button.setEnabled(False)

    def enable_controls(self, builds_combo=False):
        self.tiles_radio_button.setEnabled(True)
        self.console_radio_button.setEnabled(True)
        if is_64_windows():
            self.x64_radio_button.setEnabled(True)
        self.x86_radio_button.setEnabled(True)

        self.refresh_builds_button.setEnabled(True)

        if builds_combo:
            self.builds_combo.setEnabled(True)
        else:
            self.builds_combo.setEnabled(self.previous_bc_enabled)

        self.update_button.setEnabled(self.previous_ub_enabled)

    def download_game_update(self, url):
        main_window = self.get_main_window()

        status_bar = main_window.statusBar()
        status_bar.clearMessage()

        status_bar.busy += 1

        downloading_label = QLabel()
        downloading_label.setText(_('Downloading: {0}').format(url))
        status_bar.addWidget(downloading_label, 100)
        self.downloading_label = downloading_label

        dowloading_speed_label = QLabel()
        status_bar.addWidget(dowloading_speed_label)
        self.dowloading_speed_label = dowloading_speed_label

        downloading_size_label = QLabel()
        status_bar.addWidget(downloading_size_label)
        self.downloading_size_label = downloading_size_label

        progress_bar = QProgressBar()
        status_bar.addWidget(progress_bar)
        self.downloading_progress_bar = progress_bar
        progress_bar.setMinimum(0)

        self.download_last_read = datetime.utcnow()
        self.download_last_bytes_read = 0
        self.download_speed_count = 0

        self.download_http_reply = self.qnam.get(QNetworkRequest(QUrl(url)))
        self.download_http_reply.finished.connect(self.download_http_finished)
        self.download_http_reply.readyRead.connect(
            self.download_http_ready_read)
        self.download_http_reply.downloadProgress.connect(
            self.download_dl_progress)

        main_tab = self.get_main_tab()
        game_dir_group_box = main_tab.game_dir_group_box

        if game_dir_group_box.exe_path is not None:
            self.update_button.setText(_('Cancel update'))
        else:
            self.update_button.setText(_('Cancel installation'))

    def download_http_finished(self):
        self.downloading_file.close()

        main_window = self.get_main_window()

        status_bar = main_window.statusBar()
        status_bar.removeWidget(self.downloading_label)
        status_bar.removeWidget(self.dowloading_speed_label)
        status_bar.removeWidget(self.downloading_size_label)
        status_bar.removeWidget(self.downloading_progress_bar)

        status_bar.busy -= 1

        if self.download_aborted:
            download_dir = os.path.dirname(self.downloaded_file)
            retry_rmtree(download_dir)
        else:
            # Test downloaded file
            status_bar.showMessage(_('Testing downloaded file archive'))

            class TestingZipThread(QThread):
                completed = pyqtSignal()
                invalid = pyqtSignal()
                not_downloaded = pyqtSignal()

                def __init__(self, downloaded_file):
                    super(TestingZipThread, self).__init__()

                    self.downloaded_file = downloaded_file

                def __del__(self):
                    self.wait()

                def run(self):
                    try:
                        with zipfile.ZipFile(self.downloaded_file) as z:
                            if z.testzip() is not None:
                                self.invalid.emit()
                                return
                    except zipfile.BadZipFile:
                        self.not_downloaded.emit()
                        return

                    self.completed.emit()

            def completed_test():
                self.test_thread = None

                status_bar.clearMessage()
                self.backup_current_game()

            def invalid():
                self.test_thread = None

                status_bar.clearMessage()
                status_bar.showMessage(_('Downloaded archive is invalid'))

                download_dir = os.path.dirname(self.downloaded_file)
                retry_rmtree(download_dir)
                self.finish_updating()

            def not_downloaded():
                self.test_thread = None

                status_bar.clearMessage()
                status_bar.showMessage(_('Could not download game'))

                download_dir = os.path.dirname(self.downloaded_file)
                retry_rmtree(download_dir)
                self.finish_updating()

            test_thread = TestingZipThread(self.downloaded_file)
            test_thread.completed.connect(completed_test)
            test_thread.invalid.connect(invalid)
            test_thread.not_downloaded.connect(not_downloaded)
            test_thread.start()

            self.test_thread = test_thread

    def backup_current_game(self):
        self.backing_up_game = True

        main_tab = self.get_main_tab()
        game_dir_group_box = main_tab.game_dir_group_box

        game_dir = game_dir_group_box.dir_combo.currentText()
        self.game_dir = game_dir

        main_window = self.get_main_window()
        status_bar = main_window.statusBar()

        backup_dir = os.path.join(game_dir, 'previous_version')
        if os.path.isdir(backup_dir):
            status_bar.showMessage(_('Deleting previous_version directory'))
            if not retry_rmtree(backup_dir):
                self.backing_up_game = False

                if game_dir_group_box.exe_path is not None:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Update cancelled'))
                else:
                    if status_bar.busy == 0:
                        status_bar.showMessage(_('Installation cancelled'))

                self.finish_updating()
                return
            status_bar.clearMessage()

        dir_list = os.listdir(game_dir)
        self.backup_dir_list = dir_list

        if (config_true(get_config_value('prevent_save_move', 'False'))
            and 'save' in dir_list):
            dir_list.remove('save')

        if getattr(sys, 'frozen', False):
            launcher_exe = os.path.abspath(sys.executable)
            launcher_dir = os.path.dirname(launcher_exe)
            if os.path.abspath(game_dir) == launcher_dir:
                launcher_name = os.path.basename(launcher_exe)
                if launcher_name in dir_list:
                    dir_list.remove(launcher_name)

        if len(dir_list) > 0:
            status_bar.showMessage(_('Backing up current game'))

            status_bar.busy += 1

            backup_label = QLabel()
            status_bar.addWidget(backup_label, 100)
            self.backup_label = backup_label

            progress_bar = QProgressBar()
            status_bar.addWidget(progress_bar)
            self.backup_progress_bar = progress_bar

            timer = QTimer(self)
            self.backup_timer = timer

            progress_bar.setRange(0, len(dir_list))

            os.makedirs(backup_dir)
            self.backup_dir = backup_dir
            self.backup_index = 0
            self.backup_current_display = True

            def timeout():
                self.backup_progress_bar.setValue(self.backup_index)

                if self.backup_index == len(self.backup_dir_list):
                    self.backup_timer.stop()

                    main_window = self.get_main_window()
                    status_bar = main_window.statusBar()

                    status_bar.removeWidget(self.backup_label)
                    status_bar.removeWidget(self.backup_progress_bar)

                    status_bar.busy -= 1
                    status_bar.clearMessage()

                    self.backing_up_game = False
                    self.extract_new_build()

                else:
                    backup_element = self.backup_dir_list[self.backup_index]

                    if self.backup_current_display:
                        self.backup_label.setText(_('Backing up {0}').format(
                            backup_element))
                        self.backup_current_display = False
                    else:
                        try:
                            shutil.move(os.path.join(self.game_dir,
                                backup_element), self.backup_dir)
                        except OSError as e:
                            self.backup_timer.stop()

                            main_window = self.get_main_window()
                            status_bar = main_window.statusBar()

                            status_bar.removeWidget(self.backup_label)
                            status_bar.removeWidget(self.backup_progress_bar)

                            status_bar.busy -= 1
                            status_bar.clearMessage()

                            self.finish_updating()

                            status_bar.showMessage(str(e))

                        self.backup_index += 1
                        self.backup_current_display = True

            timer.timeout.connect(timeout)
            timer.start(0)
        else:
            self.backing_up_game = False
            self.extract_new_build()

    def extract_new_build(self):
        self.extracting_new_build = True

        z = zipfile.ZipFile(self.downloaded_file)
        self.extracting_zipfile = z

        self.extracting_infolist = z.infolist()
        self.extracting_index = 0

        main_window = self.get_main_window()
        status_bar = main_window.statusBar()

        status_bar.busy += 1

        extracting_label = QLabel()
        status_bar.addWidget(extracting_label, 100)
        self.extracting_label = extracting_label

        progress_bar = QProgressBar()
        status_bar.addWidget(progress_bar)
        self.extracting_progress_bar = progress_bar

        timer = QTimer(self)
        self.extracting_timer = timer

        progress_bar.setRange(0, len(self.extracting_infolist))

        def timeout():
            self.extracting_progress_bar.setValue(self.extracting_index)

            if self.extracting_index == len(self.extracting_infolist):
                self.extracting_timer.stop()

                main_window = self.get_main_window()
                status_bar = main_window.statusBar()

                status_bar.removeWidget(self.extracting_label)
                status_bar.removeWidget(self.extracting_progress_bar)

                status_bar.busy -= 1

                self.extracting_new_build = False

                self.extracting_zipfile.close()

                # Keep a copy of the archive if selected in the settings
                if config_true(get_config_value('keep_archive_copy', 'False')):
                    archive_dir = get_config_value('archive_directory', '')
                    archive_name = os.path.basename(self.downloaded_file)
                    move_target = os.path.join(archive_dir, archive_name)
                    if (os.path.isdir(archive_dir)
                        and not os.path.exists(move_target)):
                        shutil.move(self.downloaded_file, archive_dir)

                download_dir = os.path.dirname(self.downloaded_file)
                retry_rmtree(download_dir)

                main_tab = self.get_main_tab()
                game_dir_group_box = main_tab.game_dir_group_box

                self.analysing_new_build = True
                game_dir_group_box.analyse_new_build(self.selected_build)

            else:
                extracting_element = self.extracting_infolist[
                    self.extracting_index]
                self.extracting_label.setText(_('Extracting {0}').format(
                    extracting_element.filename))

                self.extracting_zipfile.extract(extracting_element,
                    self.game_dir)

                self.extracting_index += 1

        timer.timeout.connect(timeout)
        timer.start(0)

    def asset_name(self, path, filename):
        asset_file = os.path.join(path, filename)

        if not os.path.isfile(asset_file):
            disabled_asset_file = os.path.join(path, filename + '.disabled')
            if not os.path.isfile(disabled_asset_file):
                return None
            else:
                asset_file_path = disabled_asset_file
        else:
            asset_file_path = asset_file

        try:
            with open(asset_file_path, 'r') as f:
                for line in f:
                    if line.startswith('NAME'):
                        space_index = line.find(' ')
                        name = line[space_index:].strip().replace(
                            ',', '')
                        return name
        except FileNotFoundError:
            return None
        return None

    def mod_ident(self, path):
        json_file = os.path.join(path, 'modinfo.json')
        if not os.path.isfile(json_file):
            json_file = os.path.join(path, 'modinfo.json.disabled')
        if os.path.isfile(json_file):
            try:
                with open(json_file, 'r') as f:
                    try:
                        values = json.load(f)
                        if isinstance(values, dict):
                            if values.get('type', '') == 'MOD_INFO':
                                return values.get('ident', None)
                        elif isinstance(values, list):
                            for item in values:
                                if (isinstance(item, dict)
                                    and item.get('type', '') == 'MOD_INFO'):
                                        return item.get('ident', None)
                    except ValueError:
                        pass
            except FileNotFoundError:
                return None

        return None

    def copy_next_dir(self):
        if self.in_post_extraction and len(self.previous_dirs) > 0:
            next_dir = self.previous_dirs.pop()
            src_path = os.path.join(self.previous_version_dir, next_dir)
            dst_path = os.path.join(self.game_dir, next_dir)
            if os.path.isdir(src_path) and not os.path.exists(dst_path):
                main_window = self.get_main_window()
                status_bar = main_window.statusBar()

                progress_copy = ProgressCopyTree(src_path, dst_path, status_bar,
                    _('{0} directory').format(next_dir))
                progress_copy.completed.connect(self.copy_next_dir)
                self.progress_copy = progress_copy
                progress_copy.start()
            else:
                self.copy_next_dir()
        elif self.in_post_extraction:
            self.progress_copy = None
            self.post_extraction_step2()

    def post_extraction(self):
        self.analysing_new_build = False
        self.in_post_extraction = True

        main_window = self.get_main_window()
        status_bar = main_window.statusBar()

        # Copy config, save, templates and memorial directory from previous
        # version
        previous_version_dir = os.path.join(self.game_dir, 'previous_version')
        if os.path.isdir(previous_version_dir) and self.in_post_extraction:

            previous_dirs = ['config', 'save', 'templates', 'memorial',
                'graveyard', 'save_backups']
            if (config_true(get_config_value('prevent_save_move', 'False')) and
                'save' in previous_dirs):
                previous_dirs.remove('save')

            self.previous_dirs = previous_dirs
            self.previous_version_dir = previous_version_dir

            self.progress_copy = None
            self.copy_next_dir()
        elif self.in_post_extraction:
            # New install
            self.in_post_extraction = False
            self.finish_updating()

    def post_extraction_step2(self):
        main_window = self.get_main_window()
        status_bar = main_window.statusBar()

        # Copy custom tilesets, mods and soundpack from previous version
        # tilesets
        tilesets_dir = os.path.join(self.game_dir, 'gfx')
        previous_tilesets_dir = os.path.join(self.game_dir, 'previous_version',
            'gfx')

        if (os.path.isdir(tilesets_dir) and os.path.isdir(previous_tilesets_dir)
            and self.in_post_extraction):
            status_bar.showMessage(_('Restoring custom tilesets'))

            official_set = {}
            for entry in os.listdir(tilesets_dir):
                if not self.in_post_extraction:
                    break

                entry_path = os.path.join(tilesets_dir, entry)
                if os.path.isdir(entry_path):
                    name = self.asset_name(entry_path, 'tileset.txt')
                    if name is not None and name not in official_set:
                        official_set[name] = entry_path

            previous_set = {}
            for entry in os.listdir(previous_tilesets_dir):
                if not self.in_post_extraction:
                    break

                entry_path = os.path.join(previous_tilesets_dir, entry)
                if os.path.isdir(entry_path):
                    name = self.asset_name(entry_path, 'tileset.txt')
                    if name is not None and name not in previous_set:
                        previous_set[name] = entry_path

            custom_set = set(previous_set.keys()) - set(official_set.keys())
            for item in custom_set:
                if not self.in_post_extraction:
                    break

                target_dir = os.path.join(tilesets_dir, os.path.basename(
                    previous_set[item]))
                if not os.path.exists(target_dir):
                    shutil.copytree(previous_set[item], target_dir)

            status_bar.clearMessage()

        # soundpacks
        soundpack_dir = os.path.join(self.game_dir, 'data', 'sound')
        previous_soundpack_dir = os.path.join(self.game_dir, 'previous_version',
            'data', 'sound')

        if (os.path.isdir(soundpack_dir) and os.path.isdir(
            previous_soundpack_dir) and self.in_post_extraction):
            status_bar.showMessage(_('Restoring custom soundpacks'))

            official_set = {}
            for entry in os.listdir(soundpack_dir):
                if not self.in_post_extraction:
                    break

                entry_path = os.path.join(soundpack_dir, entry)
                if os.path.isdir(entry_path):
                    name = self.asset_name(entry_path, 'soundpack.txt')
                    if name is not None and name not in official_set:
                        official_set[name] = entry_path

            previous_set = {}
            for entry in os.listdir(previous_soundpack_dir):
                if not self.in_post_extraction:
                    break

                entry_path = os.path.join(previous_soundpack_dir, entry)
                if os.path.isdir(entry_path):
                    name = self.asset_name(entry_path, 'soundpack.txt')
                    if name is not None and name not in previous_set:
                        previous_set[name] = entry_path

            custom_set = set(previous_set.keys()) - set(official_set.keys())
            if len(custom_set) > 0:
                self.soundpack_dir = soundpack_dir
                self.previous_soundpack_set = previous_set
                self.custom_soundpacks = list(custom_set)

                self.copy_next_soundpack()
            else:
                status_bar.clearMessage()
                self.post_extraction_step3()

        else:
            self.post_extraction_step3()

    def copy_next_soundpack(self):
        if self.in_post_extraction and len(self.custom_soundpacks) > 0:
            next_item = self.custom_soundpacks.pop()
            dst_path = os.path.join(self.soundpack_dir, os.path.basename(
                self.previous_soundpack_set[next_item]))
            src_path = self.previous_soundpack_set[next_item]
            if os.path.isdir(src_path) and not os.path.exists(dst_path):
                main_window = self.get_main_window()
                status_bar = main_window.statusBar()

                progress_copy = ProgressCopyTree(src_path, dst_path, status_bar,
                    _('{name} soundpack').format(name=next_item))
                progress_copy.completed.connect(self.copy_next_soundpack)
                self.progress_copy = progress_copy
                progress_copy.start()
            else:
                self.copy_next_soundpack()
        elif self.in_post_extraction:
            self.progress_copy = None

            main_window = self.get_main_window()
            status_bar = main_window.statusBar()
            status_bar.clearMessage()

            self.post_extraction_step3()

    def post_extraction_step3(self):
        if not self.in_post_extraction:
            return

        main_window = self.get_main_window()
        status_bar = main_window.statusBar()

        # mods
        mods_dir = os.path.join(self.game_dir, 'data', 'mods')
        previous_mods_dir = os.path.join(self.game_dir, 'previous_version',
            'data', 'mods')

        if (os.path.isdir(mods_dir) and os.path.isdir(previous_mods_dir) and
            self.in_post_extraction):
            status_bar.showMessage(_('Restoring custom mods'))

            official_set = {}
            for entry in os.listdir(mods_dir):
                entry_path = os.path.join(mods_dir, entry)
                if os.path.isdir(entry_path):
                    name = self.mod_ident(entry_path)
                    if name is not None and name not in official_set:
                        official_set[name] = entry_path
            previous_set = {}
            for entry in os.listdir(previous_mods_dir):
                entry_path = os.path.join(previous_mods_dir, entry)
                if os.path.isdir(entry_path):
                    name = self.mod_ident(entry_path)
                    if name is not None and name not in previous_set:
                        previous_set[name] = entry_path

            custom_set = set(previous_set.keys()) - set(official_set.keys())
            for item in custom_set:
                target_dir = os.path.join(mods_dir, os.path.basename(
                    previous_set[item]))
                if not os.path.exists(target_dir):
                    shutil.copytree(previous_set[item], target_dir)

            status_bar.clearMessage()

        if not self.in_post_extraction:
            return

        # Copy user-default-mods.json if present
        user_default_mods_file = os.path.join(mods_dir,
            'user-default-mods.json')
        previous_user_default_mods_file = os.path.join(previous_mods_dir,
            'user-default-mods.json')

        if (not os.path.exists(user_default_mods_file)
            and os.path.isfile(previous_user_default_mods_file)):
            status_bar.showMessage(_('Restoring user-default-mods.json'))

            shutil.copy2(previous_user_default_mods_file,
                user_default_mods_file)

            status_bar.clearMessage()

        # Copy custom fonts
        fonts_dir = os.path.join(self.game_dir, 'data', 'font')
        previous_fonts_dir = os.path.join(self.game_dir, 'previous_version',
            'data', 'font')

        if (os.path.isdir(fonts_dir) and os.path.isdir(previous_fonts_dir) and
            self.in_post_extraction):
            status_bar.showMessage(_('Restoring custom fonts'))

            official_set = set(os.listdir(fonts_dir))
            previous_set = set(os.listdir(previous_fonts_dir))

            custom_set = previous_set - official_set
            for entry in custom_set:
                source = os.path.join(previous_fonts_dir, entry)
                target = os.path.join(fonts_dir, entry)
                if os.path.isfile(source):
                    shutil.copy2(source, target)
                elif os.path.isdir(source):
                    shutil.copytree(source, target)

            status_bar.clearMessage()

        if not self.in_post_extraction:
            return

        main_tab = self.get_main_tab()
        game_dir_group_box = main_tab.game_dir_group_box

        if game_dir_group_box.previous_exe_path is not None:
            status_bar.showMessage(_('Update completed'))
        else:
            status_bar.showMessage(_('Installation completed'))

        if (game_dir_group_box.current_build is not None
            and status_bar.busy == 0):
            last_build = self.builds[0]

            message = status_bar.currentMessage()
            if message != '':
                message = message + ' - '

            if last_build['number'] == game_dir_group_box.current_build:
                message = message + _('Your game is up to date')
            else:
                message = message + _('There is a new update available')
            status_bar.showMessage(message)

        self.in_post_extraction = False

        self.finish_updating()

    def finish_updating(self):
        self.updating = False
        main_tab = self.get_main_tab()
        game_dir_group_box = main_tab.game_dir_group_box

        game_dir_group_box.enable_controls()
        self.enable_controls(True)

        game_dir_group_box.update_soundpacks()
        game_dir_group_box.update_mods()
        game_dir_group_box.update_backups()

        soundpacks_tab = main_tab.get_soundpacks_tab()
        mods_tab = main_tab.get_mods_tab()
        settings_tab = main_tab.get_settings_tab()
        backups_tab = main_tab.get_backups_tab()

        soundpacks_tab.enable_tab()
        mods_tab.enable_tab()
        settings_tab.enable_tab()
        backups_tab.enable_tab()

        if game_dir_group_box.exe_path is not None:
            self.update_button.setText(_('Update game'))
        else:
            self.update_button.setText(_('Install game'))

        if self.close_after_update:
            self.get_main_window().close()

    def download_http_ready_read(self):
        self.downloading_file.write(self.download_http_reply.readAll())

    def download_dl_progress(self, bytes_read, total_bytes):
        self.downloading_progress_bar.setMaximum(total_bytes)
        self.downloading_progress_bar.setValue(bytes_read)

        self.download_speed_count += 1

        self.downloading_size_label.setText(_('{bytes_read}/{total_bytes}'
            ).format(bytes_read=sizeof_fmt(bytes_read),
                     total_bytes=sizeof_fmt(total_bytes)))

        if self.download_speed_count % 5 == 0:
            delta_bytes = bytes_read - self.download_last_bytes_read
            delta_time = datetime.utcnow() - self.download_last_read

            bytes_secs = delta_bytes / delta_time.total_seconds()
            self.dowloading_speed_label.setText(_('{bytes_sec}/s').format(
                bytes_sec=sizeof_fmt(bytes_secs)))

            self.download_last_bytes_read = bytes_read
            self.download_last_read = datetime.utcnow()

    def start_lb_request(self, url):
        self.disable_controls(True)

        main_window = self.get_main_window()

        status_bar = main_window.statusBar()
        status_bar.clearMessage()

        status_bar.busy += 1

        self.builds_combo.clear()
        self.builds_combo.addItem(_('Fetching remote builds'))

        fetching_label = QLabel()
        fetching_label.setText(_('Fetching: {url}').format(url=url))
        self.base_url = url
        status_bar.addWidget(fetching_label, 100)
        self.fetching_label = fetching_label

        progress_bar = QProgressBar()
        status_bar.addWidget(progress_bar)
        self.fetching_progress_bar = progress_bar

        progress_bar.setMinimum(0)

        self.lb_html = BytesIO()
        self.http_reply = self.qnam.get(QNetworkRequest(QUrl(url)))
        self.http_reply.finished.connect(self.lb_http_finished)
        self.http_reply.readyRead.connect(self.lb_http_ready_read)
        self.http_reply.downloadProgress.connect(self.lb_dl_progress)

    def lb_http_finished(self):
        main_window = self.get_main_window()

        status_bar = main_window.statusBar()
        status_bar.removeWidget(self.fetching_label)
        status_bar.removeWidget(self.fetching_progress_bar)

        main_tab = self.get_main_tab()
        game_dir_group_box = main_tab.game_dir_group_box

        status_bar.busy -= 1

        if not game_dir_group_box.game_started:
            if status_bar.busy == 0:
                status_bar.showMessage(_('Ready'))

            self.enable_controls()
        else:
            if status_bar.busy == 0:
                status_bar.showMessage(_('Game process is running'))

        self.lb_html.seek(0)
        document = html5lib.parse(self.lb_html, treebuilder='lxml',
            encoding='utf8', namespaceHTMLElements=False)

        builds = []
        for row in document.getroot().cssselect('tr'):
            build = {}
            for index, cell in enumerate(row.cssselect('td')):
                if index == 1:
                    if len(cell) > 0 and cell[0].text.startswith(
                        'cataclysmdda'):
                        anchor = cell[0]
                        url = urljoin(self.base_url, anchor.get('href'))
                        name = anchor.text

                        build_number = None
                        match = re.search(
                            'cataclysmdda-[01]\\.[A-F]-(?P<build>\d+)', name)
                        if match is not None:
                            build_number = match.group('build')

                        build['url'] = url
                        build['name'] = name
                        build['number'] = build_number
                elif index == 2:
                    # build date
                    str_date = cell.text.strip()
                    if str_date != '':
                        build_date = datetime.strptime(str_date,
                            '%Y-%m-%d %H:%M')
                        build['date'] = build_date

            if 'url' in build:
                builds.append(build)

        if len(builds) > 0:
            builds.reverse()
            self.builds = builds

            self.builds_combo.clear()
            for index, build in enumerate(builds):
                build_date = arrow.get(build['date'], 'UTC')
                human_delta = build_date.humanize(arrow.utcnow(),
                    locale=globals.app_locale)

                if index == 0:
                    self.builds_combo.addItem(
                        _('{number} ({delta}) - latest').format(
                        number=build['number'], delta=human_delta))
                else:
                    self.builds_combo.addItem(_('{number} ({delta})').format(
                        number=build['number'], delta=human_delta))

            if not game_dir_group_box.game_started:
                self.builds_combo.setEnabled(True)
                self.update_button.setEnabled(True)
            else:
                self.previous_bc_enabled = True
                self.previous_ub_enabled = True

            if game_dir_group_box.exe_path is not None:
                self.update_button.setText(_('Update game'))

                if (game_dir_group_box.current_build is not None
                    and status_bar.busy == 0
                    and not game_dir_group_box.game_started):
                    last_build = self.builds[0]

                    message = status_bar.currentMessage()
                    if message != '':
                        message = message + ' - '

                    if last_build['number'] == game_dir_group_box.current_build:
                        message = message + _('Your game is up to date')
                    else:
                        message = message + _('There is a new update available')
                    status_bar.showMessage(message)
            else:
                self.update_button.setText(_('Install game'))

        else:
            self.builds = None

            self.builds_combo.clear()
            self.builds_combo.addItem(_('Could not find remote builds'))
            self.builds_combo.setEnabled(False)

    def lb_http_ready_read(self):
        self.lb_html.write(self.http_reply.readAll())

    def lb_dl_progress(self, bytes_read, total_bytes):
        self.fetching_progress_bar.setMaximum(total_bytes)
        self.fetching_progress_bar.setValue(bytes_read)

    def refresh_builds(self):
        selected_graphics = self.graphics_button_group.checkedButton()
        selected_platform = self.platform_button_group.checkedButton()

        if selected_graphics is self.tiles_radio_button:
            selected_graphics = 'Tiles'
        elif selected_graphics is self.console_radio_button:
            selected_graphics = 'Console'

        if selected_platform is self.x64_radio_button:
            selected_platform = 'x64'
        elif selected_platform is self.x86_radio_button:
            selected_platform = 'x86'

        url = BASE_URLS[selected_graphics][selected_platform]

        self.start_lb_request(url)

    def graphics_clicked(self, button):
        if button is self.tiles_radio_button:
            config_value = 'Tiles'
        elif button is self.console_radio_button:
            config_value = 'Console'

        set_config_value('graphics', config_value)

        self.refresh_builds()

    def platform_clicked(self, button):
        if button is self.x64_radio_button:
            config_value = 'x64'
        elif button is self.x86_radio_button:
            config_value = 'x86'

        set_config_value('platform', config_value)

        self.refresh_builds()