import gettext
import logging
import os
import sys
import traceback
from io import StringIO
from logging.handlers import RotatingFileHandler

import rarfile
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from babel import Locale

from cddagl import globals as g
from cddagl.constants import MAX_LOG_SIZE, MAX_LOG_FILES
from cddagl.globals import _
from cddagl.helpers.gettext import reconfigure_gettext
from cddagl.ui.ExceptionWindow import ExceptionWindow
from cddagl.ui.MainWindow import MainWindow

try:
    from os import scandir
except ImportError:
    from scandir import scandir

if getattr(sys, 'frozen', False):
    # we are running in a bundle
    g.basedir = sys._MEIPASS
else:
    # we are running in a normal Python environment
    g.basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    sys.path.append(g.basedir)

from cddagl.config import init_config, get_config_value, config_true

from cddagl.helpers.win32 import SingleInstance, write_named_pipe, get_ui_locale

from cddagl.__version__ import version


def init_exception_catcher():
    sys.excepthook = handle_exception


def init_single_instance():
    if not config_true(get_config_value('allow_multiple_instances', 'False')):
        single_instance = SingleInstance()

        if single_instance.aleradyrunning():
            write_named_pipe('cddagl_instance', b'dupe')
            sys.exit(0)

        return single_instance

    return None


def init_gettext():
    locale_dir = os.path.join(g.basedir, 'cddagl', 'locale')
    preferred_locales = []

    selected_locale = get_config_value('locale', None)
    if selected_locale == 'None':
        selected_locale = None
    if selected_locale is not None:
        preferred_locales.append(selected_locale)

    system_locale = get_ui_locale()
    if system_locale is not None:
        preferred_locales.append(system_locale)

    if os.path.isdir(locale_dir):
        entries = scandir(locale_dir)
        for entry in entries:
            if entry.is_dir():
                g.available_locales.append(entry.name)

        g.available_locales.sort(key=lambda x: 0 if x == 'en' else 1)

    app_locale = Locale.negotiate(preferred_locales, g.available_locales)
    if app_locale is None:
        app_locale = 'en'
    else:
        app_locale = str(app_locale)

    try:
        t = gettext.translation('cddagl', localedir=locale_dir,
            languages=[app_locale])
        globals._ = t.gettext
    except FileNotFoundError as e:
        pass

    return app_locale


def init_logging():
    logger = logging.getLogger('cddagl')
    logger.setLevel(logging.INFO)

    local_app_data = os.environ.get('LOCALAPPDATA', os.environ.get('APPDATA'))
    if local_app_data is None or not os.path.isdir(local_app_data):
        local_app_data = ''

    logging_dir = os.path.join(local_app_data, 'CDDA Game Launcher')
    if not os.path.isdir(logging_dir):
        os.makedirs(logging_dir)

    logging_file = os.path.join(logging_dir, 'app.log')

    handler = RotatingFileHandler(logging_file, maxBytes=MAX_LOG_SIZE,
                                  backupCount=MAX_LOG_FILES, encoding='utf8')
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    logger.addHandler(handler)

    if not getattr(sys, 'frozen', False):
        handler = logging.StreamHandler()
        logger.addHandler(handler)
    else:
        '''class LoggerWriter:
            def __init__(self, logger, level, imp=None):
                self.logger = logger
                self.level = level
                self.imp = imp

            def __getattr__(self, attr):
                return getattr(self.imp, attr)

            def write(self, message):
                if message != '\n':
                    self.logger.log(self.level, message)


        sys._stdout = sys.stdout
        sys._stderr = sys.stderr

        sys.stdout = LoggerWriter(logger, logging.INFO, sys._stdout)
        sys.stderr = LoggerWriter(logger, logging.ERROR, sys._stderr)'''

    logger.info(_('CDDA Game Launcher started: {version}').format(
        version=version))


def handle_exception(extype, value, tb):
    logger = logging.getLogger('cddagl')

    tb_io = StringIO()
    traceback.print_tb(tb, file=tb_io)

    logger.critical(_('Global error:\nLauncher version: {version}\nType: '
        '{extype}\nValue: {value}\nTraceback:\n{traceback}').format(
            version=version, extype=str(extype), value=str(value),
            traceback=tb_io.getvalue()))

    show_exception_ui(extype, value, tb)


def show_exception_ui(extype, value, tb):
    g.main_app.closeAllWindows()
    ex_win = ExceptionWindow(extype, value, tb)
    ex_win.show()
    g.main_app.ex_win = ex_win


def start_ui(locale, single_instance):
    reconfigure_gettext(locale)

    if getattr(sys, 'frozen', False):
        rarfile.UNRAR_TOOL = os.path.join(g.basedir, 'UnRAR.exe')

    g.main_app = QApplication(sys.argv)

    launcher_icon_path = os.path.join(g.basedir, 'cddagl', 'resources',
        'launcher.ico')
    g.main_app.setWindowIcon(QIcon(launcher_icon_path))

    main_win = MainWindow('CDDA Game Launcher')
    main_win.show()

    g.main_app.main_win = main_win
    g.main_app.single_instance = single_instance
    sys.exit(g.main_app.exec_())


if __name__ == '__main__':
    init_exception_catcher()
    init_config(g.basedir)

    app_locale = init_gettext()
    init_logging()

    single_instance = init_single_instance()

    start_ui(app_locale, single_instance)


