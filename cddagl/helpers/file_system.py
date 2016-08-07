import html
import os
import shutil
import stat

from PyQt5.QtWidgets import QMessageBox

from cddagl import globals as globals
from cddagl.helpers.win32 import find_process_with_file_handle

try:
    from os import scandir
except ImportError:
    from scandir import scandir

from cddagl.globals import _

def clean_qt_path(path):
    return path.replace('/', '\\')


def safe_filename(filename):
    keepcharacters = (' ', '.', '_', '-')
    return ''.join(c for c in filename if c.isalnum() or c in keepcharacters
        ).strip()


def retry_rmtree(path):
    while os.path.isdir(path):
        try:
            shutil.rmtree(path, onerror=remove_readonly)
        except OSError as e:
            retry_msgbox = QMessageBox()
            retry_msgbox.setWindowTitle(_('Cannot remove directory'))

            process = None
            if e.filename is not None:
                process = find_process_with_file_handle(e.filename)

            text = _('''
<p>The launcher failed to remove the following directory: {directory}</p>
<p>When trying to remove or access {filename}, the launcher raised the
following error: {error}</p>
''').format(
    directory=html.escape(path),
    filename=html.escape(e.filename),
    error=html.escape(e.strerror))

            if process is None:
                text = text + _('''
<p>No process seems to be using that file or directory.</p>
''')
            else:
                text = text + _('''
<p>The process <strong>{image_file_name} ({pid})</strong> is currently using
that file or directory. You might need to end it if you want to retry.</p>
''').format(image_file_name=process['image_file_name'], pid=process['pid'])

            retry_msgbox.setText(text)
            retry_msgbox.setInformativeText(_('Do you want to retry removing '
                'this directory?'))
            retry_msgbox.addButton(_('Retry removing the directory'),
                QMessageBox.YesRole)
            retry_msgbox.addButton(_('Cancel the operation'),
                QMessageBox.NoRole)
            retry_msgbox.setIcon(QMessageBox.Critical)

            if retry_msgbox.exec() == 1:
                return False

    return True


def retry_delfile(path):
    while os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as e:
            retry_msgbox = QMessageBox()
            retry_msgbox.setWindowTitle(_('Cannot delete file'))

            process = None
            if e.filename is not None:
                process = find_process_with_file_handle(e.filename)

            text = _('''
<p>The launcher failed to delete the following file: {path}</p>
<p>When trying to remove or access {filename}, the launcher raised the
following error: {error}</p>
''').format(
    path=html.escape(path),
    filename=html.escape(e.filename),
    error=html.escape(e.strerror))

            if process is None:
                text = text + _('''
<p>No process seems to be using that file.</p>
''')
            else:
                text = text + _('''
<p>The process <strong>{image_file_name} ({pid})</strong> is currently using
that file. You might need to end it if you want to retry.</p>
''').format(image_file_name=process['image_file_name'], pid=process['pid'])

            retry_msgbox.setText(text)
            retry_msgbox.setInformativeText(_('Do you want to retry deleting '
                'this file?'))
            retry_msgbox.addButton(_('Retry deleting the file'),
                QMessageBox.YesRole)
            retry_msgbox.addButton(_('Cancel the operation'),
                QMessageBox.NoRole)
            retry_msgbox.setIcon(QMessageBox.Critical)

            if retry_msgbox.exec() == 1:
                return False

    return True


def retry_rename(src, dst):
    while os.path.exists(src):
        try:
            os.rename(src, dst)
        except OSError as e:
            retry_msgbox = QMessageBox()
            retry_msgbox.setWindowTitle(_('Cannot rename file'))

            process = None
            if e.filename is not None:
                process = find_process_with_file_handle(e.filename)

            text = _('''
<p>The launcher failed to rename the following file: {src} to {dst}</p>
<p>When trying to rename or access {filename}, the launcher raised the
following error: {error}</p>
''').format(
    src=html.escape(src),
    dst=html.escape(dst),
    filename=html.escape(e.filename),
    error=html.escape(e.strerror))

            if process is None:
                text = text + _('''
<p>No process seems to be using that file.</p>
''')
            else:
                text = text + _('''
<p>The process <strong>{image_file_name} ({pid})</strong> is currently using
that file. You might need to end it if you want to retry.</p>
''').format(image_file_name=process['image_file_name'], pid=process['pid'])

            retry_msgbox.setText(text)
            retry_msgbox.setInformativeText(_('Do you want to retry renaming '
                'this file?'))
            retry_msgbox.addButton(_('Retry renaming the file'),
                QMessageBox.YesRole)
            retry_msgbox.addButton(_('Cancel the operation'),
                QMessageBox.NoRole)
            retry_msgbox.setIcon(QMessageBox.Critical)

            if retry_msgbox.exec() == 1:
                return False

    return True


def remove_readonly(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def sizeof_fmt(num, suffix=None):
    if suffix is None:
        suffix = _('B')
    for unit in ['', _('Ki'), _('Mi'), _('Gi'), _('Ti'), _('Pi'), _('Ei'),
        _('Zi')]:
        if abs(num) < 1024.0:
            return _("%3.1f %s%s") % (num, unit, suffix)
        num /= 1024.0
    return _("%.1f %s%s") % (num, _('Yi'), suffix)


def get_data_path():
    return os.path.join(globals.basedir, 'data')