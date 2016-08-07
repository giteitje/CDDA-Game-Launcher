import html
import traceback
from io import StringIO
from urllib.parse import urlencode

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QGridLayout, QLabel, QTextBrowser, \
    QPushButton

from cddagl.__version__ import version
from cddagl.constants import NEW_ISSUE_URL
from cddagl.globals import _


class ExceptionWindow(QWidget):
    def __init__(self, extype, value, tb):
        super(ExceptionWindow, self).__init__()

        layout = QGridLayout()

        information_label = QLabel()
        information_label.setText(_('The CDDA Game Launcher just crashed. An '
                                    'unhandled exception was raised. Here are the details.'))
        layout.addWidget(information_label, 0, 0)
        self.information_label = information_label

        tb_io = StringIO()
        traceback.print_tb(tb, file=tb_io)
        traceback_content = html.escape(tb_io.getvalue()).replace('\n', '<br>')

        text_content = QTextBrowser()
        text_content.setReadOnly(True)
        text_content.setOpenExternalLinks(True)
        text_content.setHtml(_('''
<p>CDDA Game Launcher version: {version}</p>
<p>Type: {extype}</p>
<p>Value: {value}</p>
<p>Traceback:</p>
<code>{traceback}</code>
''').format(version=html.escape(version), extype=html.escape(str(extype)),
            value=html.escape(str(value)),
            traceback=traceback_content))

        layout.addWidget(text_content, 1, 0)
        self.text_content = text_content

        report_url = NEW_ISSUE_URL + '?' + urlencode({
            'title': _('Unhandled exception: [Enter a title]'),
            'body': _('''* Description: [Enter what you did and what happened]
* Version: {version}
* Type: `{extype}`
* Value: {value}
* Traceback:
```
{traceback}
```
''').format(version=version, extype=str(extype), value=str(value),
            traceback=tb_io.getvalue())
        })

        report_label = QLabel()
        report_label.setOpenExternalLinks(True)
        report_label.setText(_('Please help us make a better launcher '
                               '<a href="{url}">by reporting this issue on GitHub</a>.').format(
            url=html.escape(report_url)))
        layout.addWidget(report_label, 2, 0)
        self.report_label = report_label

        exit_button = QPushButton()
        exit_button.setText(_('Exit'))
        exit_button.clicked.connect(self.close)
        layout.addWidget(exit_button, 3, 0, Qt.AlignRight)
        self.exit_button = exit_button

        self.setLayout(layout)
        self.setWindowTitle(_('Something went wrong'))
        self.setMinimumSize(350, 0)
