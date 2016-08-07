import gettext
import logging
import os

from cddagl import globals as globals


def reconfigure_gettext(locale):
    locale_dir = os.path.join(globals.basedir, 'cddagl', 'locale')

    try:
        t = gettext.translation('cddagl', localedir=locale_dir,
                                languages=[locale])
        globals._ = t.gettext
        globals.n_ = t.ngettext
    except FileNotFoundError as e:
        logging.getLogger('cddagl').warning(
            globals._('Could not find translations for {locale} in '
                      '{locale_dir} ({info})'
                      ).format(locale=locale, locale_dir=locale_dir,
                               info=str(e)))

    globals.app_locale = locale
