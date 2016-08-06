import gettext
import os
import random
import shutil
import subprocess
import sys
from datetime import datetime

_ = gettext.gettext

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt5.QtWidgets import QDialog, QGridLayout, QLabel, QProgressBar, \
    QLineEdit, QPushButton

from cddagl.helpers.file_system import retry_rmtree, sizeof_fmt


class LauncherUpdateDialog(QDialog):
    def __init__(self, url, version, parent=0, f=0):
        super(LauncherUpdateDialog, self).__init__(parent, f)

        self.updated = False
        self.url = url

        layout = QGridLayout()

        self.shown = False
        self.qnam = QNetworkAccessManager()
        self.http_reply = None

        progress_label = QLabel()
        progress_label.setText(_('Progress:'))
        layout.addWidget(progress_label, 0, 0, Qt.AlignRight)
        self.progress_label = progress_label

        progress_bar = QProgressBar()
        layout.addWidget(progress_bar, 0, 1)
        self.progress_bar = progress_bar

        url_label = QLabel()
        url_label.setText(_('Url:'))
        layout.addWidget(url_label, 1, 0, Qt.AlignRight)
        self.url_label = url_label

        url_lineedit = QLineEdit()
        url_lineedit.setText(url)
        url_lineedit.setReadOnly(True)
        layout.addWidget(url_lineedit, 1, 1)
        self.url_lineedit = url_lineedit

        size_label = QLabel()
        size_label.setText(_('Size:'))
        layout.addWidget(size_label, 2, 0, Qt.AlignRight)
        self.size_label = size_label

        size_value_label = QLabel()
        layout.addWidget(size_value_label, 2, 1)
        self.size_value_label = size_value_label

        speed_label = QLabel()
        speed_label.setText(_('Speed:'))
        layout.addWidget(speed_label, 3, 0, Qt.AlignRight)
        self.speed_label = speed_label

        speed_value_label = QLabel()
        layout.addWidget(speed_value_label, 3, 1)
        self.speed_value_label = speed_value_label

        cancel_button = QPushButton()
        cancel_button.setText(_('Cancel update'))
        cancel_button.setStyleSheet('font-size: 15px;')
        cancel_button.clicked.connect(self.cancel_update)
        layout.addWidget(cancel_button, 4, 0, 1, 2)
        self.cancel_button = cancel_button

        layout.setColumnStretch(1, 100)

        self.setLayout(layout)
        self.setMinimumSize(300, 0)
        self.setWindowTitle(_('CDDA Game Launcher self-update'))

    def showEvent(self, event):
        if not self.shown:
            temp_dir = os.path.join(os.environ['TEMP'], 'CDDA Game Launcher')
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir)

            temp_dl_dir = os.path.join(temp_dir, 'launcher-update')
            while os.path.exists(temp_dl_dir):
                temp_dl_dir = os.path.join(temp_dir,
                    'launcher-update-{0}'.format(
                    '%08x' % random.randrange(16**8)))
            os.makedirs(temp_dl_dir)

            exe_name = os.path.basename(sys.executable)

            self.downloaded_file = os.path.join(temp_dl_dir, exe_name)
            self.downloading_file = open(self.downloaded_file, 'wb')

            self.download_last_read = datetime.utcnow()
            self.download_last_bytes_read = 0
            self.download_speed_count = 0
            self.download_aborted = False

            self.http_reply = self.qnam.get(QNetworkRequest(QUrl(self.url)))
            self.http_reply.finished.connect(self.http_finished)
            self.http_reply.readyRead.connect(self.http_ready_read)
            self.http_reply.downloadProgress.connect(self.dl_progress)

        self.shown = True

    def closeEvent(self, event):
        self.cancel_update(True)

    def http_finished(self):
        self.downloading_file.close()

        if self.download_aborted:
            download_dir = os.path.dirname(self.downloaded_file)
            retry_rmtree(download_dir)
        else:
            redirect = self.http_reply.attribute(
                QNetworkRequest.RedirectionTargetAttribute)
            if redirect is not None:
                download_dir = os.path.dirname(self.downloaded_file)
                retry_rmtree(download_dir)
                os.makedirs(download_dir)

                self.downloading_file = open(self.downloaded_file, 'wb')

                self.download_last_read = datetime.utcnow()
                self.download_last_bytes_read = 0
                self.download_speed_count = 0
                self.download_aborted = False

                self.progress_bar.setValue(0)

                self.http_reply = self.qnam.get(QNetworkRequest(redirect))
                self.http_reply.finished.connect(self.http_finished)
                self.http_reply.readyRead.connect(self.http_ready_read)
                self.http_reply.downloadProgress.connect(self.dl_progress)
            else:
                # Download completed
                if getattr(sys, 'frozen', False):
                    launcher_exe = os.path.abspath(sys.executable)
                    launcher_dir = os.path.dirname(launcher_exe)
                    download_dir = os.path.dirname(self.downloaded_file)
                    pid = os.getpid()

                    batch_path = os.path.join(sys._MEIPASS, 'updated.bat')
                    copied_batch_path = os.path.join(download_dir,
                        'updated.bat')
                    shutil.copy2(batch_path, copied_batch_path)

                    command = ('start "Update Process" call "{batch}" "{pid}" '
                        '"{update_path}" "{update_dir}" "{exe_path}" '
                        '"{exe_dir}"'
                        ).format(batch=copied_batch_path, pid=pid,
                        update_path=self.downloaded_file,
                        update_dir=download_dir,
                        exe_path=launcher_exe,
                        exe_dir=launcher_dir)
                    subprocess.call(command, shell=True)

                    self.updated = True
                    self.done(0)

    def http_ready_read(self):
        self.downloading_file.write(self.http_reply.readAll())

    def dl_progress(self, bytes_read, total_bytes):
        self.progress_bar.setMaximum(total_bytes)
        self.progress_bar.setValue(bytes_read)

        self.download_speed_count += 1

        self.size_value_label.setText(_('{bytes_read}/{total_bytes}').format(
            bytes_read=sizeof_fmt(bytes_read),
            total_bytes=sizeof_fmt(total_bytes)))

        if self.download_speed_count % 5 == 0:
            delta_bytes = bytes_read - self.download_last_bytes_read
            delta_time = datetime.utcnow() - self.download_last_read

            bytes_secs = delta_bytes / delta_time.total_seconds()
            self.speed_value_label.setText(_('{bytes_sec}/s').format(
                bytes_sec=sizeof_fmt(bytes_secs)))

            self.download_last_bytes_read = bytes_read
            self.download_last_read = datetime.utcnow()

    def cancel_update(self, from_close=False):
        if self.http_reply.isRunning():
            self.download_aborted = True
            self.http_reply.abort()

        if not from_close:
            self.close()