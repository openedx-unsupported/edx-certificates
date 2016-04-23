# -*- coding: utf-8 -*-

import copy
from glob import glob
import os
import logging
import settings

from reportlab.lib.pagesizes import landscape
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph

logging.config.dictConfig(settings.LOGGING)
log = logging.getLogger('certificates.' + __name__)


TEMPLATE_DIR = settings.TEMPLATE_DIR
WIDTH_LANDSCAPE_PAGE_IN_POINTS, HEIGHT_LANDSCAPE_PAGE_IN_POINTS = landscape(letter)

# Register all fonts in the fonts/ dir; there are likely more fonts here than
# we need, but the performance hit is minimal -- especially since we only do
# this at import time.
#
# While registering fonts, build a table of the Unicode code points in each
# for use in font_for_string().
FONT_CHARACTER_TABLES = {}
for font_file in glob('{0}/fonts/*.ttf'.format(TEMPLATE_DIR)):
    font_name = os.path.basename(os.path.splitext(font_file)[0])
    ttf = TTFont(font_name, font_file)
    FONT_CHARACTER_TABLES[font_name] = ttf.face.charToGlyph.keys()
    pdfmetrics.registerFont(TTFont(font_name, font_file))


def apply_style_to_font_list(fonts_old, style_old):
    """
    Assign a new style to each font
    """
    for font_name, font_file, dummy_0 in fonts_old:
        style_new = copy.copy(style_old)
        style_new.fontName = font_name
        font_new = (font_name, font_file, style_new)
        yield font_new


def autoscale_text(page, string, max_fontsize, max_leading, max_height, max_width, style):
    """Calculate font size and text placement given some base values

    These values passed by reference are modified in this function, and not passed back:
        - style.fontSize
        - style.leading
    """
    width = max_width + 1
    height = max_height + 1
    fontsize = max_fontsize
    leading = max_leading

    # Loop while size of text bigger than max allowed size as passed through
    while width > max_width or height > max_height:
        style.fontSize = fontsize
        style.leading = leading
        paragraph = Paragraph(string, style)
        width, height = paragraph.wrapOn(page, max_width, max_height)
        fontsize -= 1
        leading -= 1

    return paragraph


def font_for_string(fontlist, ustring):
    """Determine the best font to render a string.

    Given a list of fonts in priority order (that is, prettiest-first) and a
    string which may or may not contain Unicode characters, test the string's
    codepoints for glyph entries in the font, failing if any are missing and
    returning the font name if it succeeds.

    Font list a list of tuples where the first two items are the
    human-readable font name, the on-disk filename, and one or more ignored
    fields, e.g.:
      [('font name', 'filename.ttf', 'ignored value', [...]), ...]
    """
    # TODO: There's probably a way to do this by consulting reportlab that
    #       doesn't require re-loading the font files at all
    ustring = unicode(ustring)
    fontlist = list(fontlist)
    if fontlist and not ustring:
        return fontlist[0]
    for fonttuple in fontlist:
        fonttag = fonttuple[0]
        codepoints = FONT_CHARACTER_TABLES.get(fonttag, [])
        if not codepoints:
            warnstring = "Missing or invalid font specification {fonttag} " \
                         "rendering string '{ustring}'.\nFontlist: {fontlist}".format(
                             fonttag=fonttag,
                             ustring=ustring.encode('utf-8'),
                             fontlist=fontlist,
                         )
            log.warning(warnstring)
            continue
        OK = reduce(lambda x, y: x and y, (ord(c) in codepoints for c in ustring))
        if OK:
            return fonttuple
    # No font we tested supports this string, throw an exception.
    # Then a human can and should install better fonts
    raise ValueError("Nothing in fontlist supports string '{0}'. Fontlist: {1}".format(
        ustring.encode('utf-8'),
        repr(fontlist),
    ))
