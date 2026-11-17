"""Translations: gettext domain `duo-shell`.

Every user-visible string goes through `_()`. The catalogues live in
/usr/local/share/locale/<lang>/LC_MESSAGES/duo-shell.mo (none yet: English
only); po/duo-shell.pot is the template (po/update-pot.py makes it).
"""

import gettext
import os

DOMAIN = "duo-shell"
LOCALEDIR = os.environ.get("DUO_SHELL_LOCALEDIR", "/usr/local/share/locale")

_translation = gettext.translation(DOMAIN, LOCALEDIR, fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext
