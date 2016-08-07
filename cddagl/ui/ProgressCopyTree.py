import os
import shutil

from cddagl.globals import _, n_

from collections import deque
from datetime import datetime, timedelta
from os import scandir

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtWidgets import QLabel, QProgressBar

from cddagl.constants import READ_BUFFER_SIZE
from cddagl.helpers.file_system import sizeof_fmt


class ProgressCopyTree(QTimer):
    completed = pyqtSignal()
    aborted = pyqtSignal()

    def __init__(self, src, dst, status_bar, name):
        if not os.path.isdir(src):
            raise OSError(_("Source path '%s' is not a directory") % src)
        if os.path.exists(dst):
            raise OSError(_("Destination path '%s' already exists") % dst)

        super(ProgressCopyTree, self).__init__()

        self.src = src
        self.dst = dst

        self.status_bar = status_bar
        self.name = name

        self.started = False
        self.callback = None

        self.status_label = None
        self.copying_speed_label = None
        self.copying_size_label = None
        self.progress_bar = None

        self.source_file = None
        self.destination_file = None

        self.analysing = False
        self.copying = False
        self.copy_completed = False

    def step(self):
        if self.analysing:
            if self.current_scan is None:
                self.current_scan = scandir(self.src)
            else:
                try:
                    entry = next(self.current_scan)
                    self.source_entries.append(entry)
                    if entry.is_dir():
                        self.next_scans.append(entry.path)
                    elif entry.is_file():
                        self.total_files += 1
                        self.total_copy_size += entry.stat().st_size

                        files_text = n_('file', 'files', self.total_files)

                        self.status_label.setText(_('Analysing {name} - Found '
                            '{file_count} {files} ({size})').format(
                                name=self.name,
                                file_count=self.total_files,
                                files=files_text,
                                size=sizeof_fmt(self.total_copy_size)))

                except StopIteration:
                    if len(self.next_scans) > 0:
                        self.current_scan = scandir(self.next_scans.popleft())
                    else:
                        self.analysing = False

                        os.makedirs(self.dst)

                        if len(self.source_entries) > 0:
                            self.copying = True

                            copying_speed_label = QLabel()
                            copying_speed_label.setText(_('{bytes_sec}/s'
                                ).format(bytes_sec=sizeof_fmt(0)))
                            self.status_bar.addWidget(copying_speed_label)
                            self.copying_speed_label = copying_speed_label

                            copying_size_label = QLabel()
                            copying_size_label.setText(
                                _('{bytes_read}/{total_bytes}').format(
                                bytes_read=sizeof_fmt(0),
                                total_bytes=sizeof_fmt(self.total_copy_size)))
                            self.status_bar.addWidget(copying_size_label)
                            self.copying_size_label = copying_size_label

                            progress_bar = QProgressBar()
                            progress_bar.setRange(0, self.total_copy_size)
                            progress_bar.setValue(0)
                            self.status_bar.addWidget(progress_bar)
                            self.progress_bar = progress_bar

                            self.copied_size = 0
                            self.copied_files = 0
                            self.copy_speed_count = 0
                            self.last_copied_bytes = 0
                            self.last_copied = datetime.utcnow()
                            self.current_entry = None
                            self.source_file = None
                            self.destination_file = None
                        else:
                            self.copy_completed = True
                            self.stop()

        elif self.copying:
            if self.current_entry is None:
                if len(self.source_entries) > 0:
                    self.current_entry = self.source_entries.popleft()
                    self.display_entry(self.current_entry)
                else:
                    self.copying = False
                    self.copy_completed = True
                    self.stop()
            elif self.source_file is None and self.destination_file is None:
                relpath = os.path.relpath(self.current_entry.path, self.src)
                dstpath = os.path.join(self.dst, relpath)
                self.dstpath = dstpath

                if self.current_entry.is_dir():
                    os.makedirs(dstpath)
                    self.current_entry = None
                elif self.current_entry.is_file():
                    filedir = os.path.dirname(dstpath)
                    if not os.path.isdir(filedir):
                        os.makedirs(filedir)
                    self.source_file = open(self.current_entry.path, 'rb')
                    self.destination_file = open(dstpath, 'wb')
            else:
                buf = self.source_file.read(READ_BUFFER_SIZE)
                buf_len = len(buf)
                if buf_len == 0:
                    self.source_file.close()
                    self.destination_file.close()
                    shutil.copystat(self.current_entry.path, self.dstpath)
                    self.source_file = None
                    self.destination_file = None
                    self.current_entry = None

                    self.copied_files += 1
                else:
                    self.destination_file.write(buf)

                    self.copied_size += buf_len
                    self.progress_bar.setValue(self.copied_size)

                    self.copy_speed_count += 1

                    if self.copy_speed_count % 10 == 0:
                        self.copying_size_label.setText(
                            _('{bytes_read}/{total_bytes}').format(
                            bytes_read=sizeof_fmt(self.copied_size),
                            total_bytes=sizeof_fmt(self.total_copy_size)))

                        delta_bytes = self.copied_size - self.last_copied_bytes
                        delta_time = datetime.utcnow() - self.last_copied
                        if delta_time.total_seconds() == 0:
                            delta_time = timedelta.resolution

                        bytes_secs = delta_bytes / delta_time.total_seconds()
                        self.copying_speed_label.setText(_('{bytes_sec}/s'
                            ).format(bytes_sec=sizeof_fmt(bytes_secs)))

                        self.last_copied_bytes = self.copied_size
                        self.last_copied = datetime.utcnow()


    def display_entry(self, entry):
        if self.status_label is not None:
            entry_rel_path = os.path.relpath(entry.path, self.src)
            self.status_label.setText(
                _('Copying {name} - {entry}').format(name=self.name,
                    entry=entry_rel_path))

    def start(self):
        self.started = True
        self.status_bar.clearMessage()
        self.status_bar.busy += 1

        self.analysing = True
        status_label = QLabel()
        status_label.setText(_('Analysing {name}').format(name=self.name))
        self.status_bar.addWidget(status_label, 100)
        self.status_label = status_label

        self.total_copy_size = 0
        self.total_files = 0

        self.timeout.connect(self.step)

        self.current_scan = None
        self.next_scans = deque()
        self.source_entries = deque()

        super(ProgressCopyTree, self).start(0)

    def stop(self):
        super(ProgressCopyTree, self).stop()

        if self.started:
            self.status_bar.busy -= 1
            if self.status_label is not None:
                self.status_bar.removeWidget(self.status_label)
            if self.progress_bar is not None:
                self.status_bar.removeWidget(self.progress_bar)
            if self.copying_speed_label is not None:
                self.status_bar.removeWidget(self.copying_speed_label)
            if self.copying_size_label is not None:
                self.status_bar.removeWidget(self.copying_size_label)

            if self.source_file is not None:
                self.source_file.close()
            if self.destination_file is not None:
                self.destination_file.close()

        if self.copy_completed:
            self.completed.emit()
        else:
            self.aborted.emit()