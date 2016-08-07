import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QGridLayout, QTextBrowser, QPushButton

import cddagl.globals as globals
from cddagl.__version__ import version
from cddagl.globals import _


class AboutDialog(QDialog):
    def __init__(self, parent=0, f=0):
        super(AboutDialog, self).__init__(parent, f)

        layout = QGridLayout()

        text_content = QTextBrowser()
        text_content.setReadOnly(True)
        text_content.setOpenExternalLinks(True)

        text_content.setSearchPaths([os.path.join(globals.basedir, 'cddagl',
                                                  'resources')])
        layout.addWidget(text_content, 0, 0)
        self.text_content = text_content

        ok_button = QPushButton()
        ok_button.clicked.connect(self.done)
        layout.addWidget(ok_button, 1, 0, Qt.AlignRight)
        self.ok_button = ok_button

        layout.setRowStretch(0, 100)

        self.setMinimumSize(500, 400)

        self.setLayout(layout)
        self.set_text()

    def set_text(self):
        self.setWindowTitle(_('About CDDA Game Launcher'))
        self.ok_button.setText(_('OK'))
        self.text_content.setHtml(_('''
<p>CDDA Game Launcher version {version}</p>

<p>Get the latest release <a
href="https://github.com/remyroy/CDDA-Game-Launcher/releases">on GitHub</a>.</p>

<p>Please report any issue <a
href="https://github.com/remyroy/CDDA-Game-Launcher/issues/new">on GitHub</a>.
</p>

<p>If you like the CDDA Game Launcher, you can buy me a beer by donating
bitcoins to <a href="bitcoin:15SxanjS9CELTqVRCeEKgzFKYCCvSDLdsZ">
15SxanjS9CELTqVRCeEKgzFKYCCvSDLdsZ</a> <img src="btc-qr.png">.</p>

<p>Thanks to the following people for their efforts in translating the CDDA Game
Launcher</p>
<ul>
<li>Russian: Daniel from <a href="http://cataclysmdda.ru/">cataclysmdda.ru</a>
and Night_Pryanik</li>
<li>Italian: Rettiliano Verace from <a
href="http://emigrantebestemmiante.blogspot.com">Emigrante Bestemmiante</a></li>
<li>French: Rémy Roy</li>
</ul>

<p>Thanks to <a href="http://mattahan.deviantart.com/">Paul Davey aka
Mattahan</a> for the permission to use his artwork for the launcher icon.</p>

<p>Copyright (c) 2015 Rémy Roy</p>

<p>Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:</p>

<p>The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.</p>

<p>THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.</p>

''').format(version=version))
