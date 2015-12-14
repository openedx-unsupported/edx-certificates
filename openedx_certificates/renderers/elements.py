# -*- coding: utf-8 -*-

import copy
import logging
import os
import settings

from reportlab.lib.colors import HexColor
import reportlab.lib.enums as reportlab_enums
from reportlab.lib.pagesizes import landscape
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph

from openedx_certificates.renderers.util import apply_style_to_font_list
from openedx_certificates.renderers.util import autoscale_text
from openedx_certificates.renderers.util import font_for_string

logging.config.dictConfig(settings.LOGGING)
log = logging.getLogger('certificates.' + __name__)

TEMPLATE_DIR = settings.TEMPLATE_DIR


def _translate_reportlab_alignment(alignment):
    alignment = 'TA_' + alignment.upper()
    return alignment


def draw_rectangle(_certificateGen, attributes, canvas, _context):
    stroke = 0
    stroke_color = attributes.get('stroke_color')
    stroke_width = attributes.get('stroke_width')
    if stroke_color or stroke_width:
        stroke_color = stroke_color or '#000000'
        stroke_width = stroke_width or 1
        stroke = 1
        canvas.setLineWidth(stroke_width)
        canvas.setStrokeColor(HexColor(stroke_color))

    fill = 0
    fill_color = attributes.get('fill_color')
    if fill_color:
        canvas.setFillColor(HexColor(fill_color))
        fill = 1

    if stroke or fill:
        canvas.rect(
            attributes['x'],
            attributes['y'],
            attributes['width'],
            attributes['height'],
            stroke=stroke,
            fill=fill,
        )


def draw_line(_certificateGen, attributes, canvas, _context):
    stroke = 0
    stroke_color = attributes.get('stroke_color')
    stroke_width = attributes.get('stroke_width')
    if stroke_color or stroke_width:
        stroke_color = stroke_color or '#000000'
        stroke_width = stroke_width or 1
        canvas.setLineWidth(stroke_width)
        canvas.setStrokeColor(HexColor(stroke_color))

        canvas.line(
            attributes['x_start'],
            attributes['y_start'],
            attributes['x_end'],
            attributes['y_end'],
        )


def draw_image(_certificateGen, attributes, canvas, _context):
    image_file = os.path.join(TEMPLATE_DIR, attributes['file'])
    canvas.drawImage(
        image_file,
        attributes['x'],
        attributes['y'],
        attributes['width'],
        attributes['height'],
        mask='auto',
    )


def draw_text(certificateGen, attributes, canvas, context):
    string = unicode(attributes['string'])

    if context:
        string = string.format(**context)

    fontSize = attributes.get('font_size', 12)
    leading = attributes.get('leading', 12)
    textColor = attributes.get('text_color', '#000000')
    height = attributes['height']
    width = attributes['width']
    x_position = attributes['x']
    y_position = attributes['y']
    alignment = attributes.get('alignment', 'left')
    alignment = _translate_reportlab_alignment(alignment)
    auto_scale = attributes.get('auto_scale')

    style_for_text = ParagraphStyle(
        name='text',
        fontSize=fontSize,
        leading=leading,
        textColor=HexColor(textColor),
        alignment=getattr(reportlab_enums, alignment),
    )

    (fonttag, fontfile, text_style) = font_for_string(
        apply_style_to_font_list(certificateGen.fontlist, style_for_text),
        string
    )

    max_height = height
    if auto_scale:
        paragraph = autoscale_text(canvas, string, fontSize, leading, height, width, text_style)
        y_position = y_position + ((height - paragraph.height) / 2) + (text_style.fontSize / 5)
    else:
        paragraph = Paragraph(string, text_style)
        width, height = paragraph.wrapOn(canvas, width, height)
        y_position = y_position + (max_height - paragraph.height)

    paragraph.drawOn(canvas, x_position, y_position)


ELEMENT_OPTIONS = {
    'rectangle': draw_rectangle,
    'line': draw_line,
    'image': draw_image,
    'text': draw_text,
}


def draw_template_element(certificateGen, element, attributes, canvas, context=None):
    ELEMENT_OPTIONS[element](certificateGen, attributes, canvas, context)
