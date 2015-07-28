# -*- coding: utf-8 -*-
import logging

from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT

from openedx_certificates.renderers.util import apply_style_to_font_list
from openedx_certificates.renderers.util import autoscale_text
from openedx_certificates.renderers.util import font_for_string
from openedx_certificates.renderers.util import WIDTH_LANDSCAPE_PAGE_IN_POINTS, HEIGHT_LANDSCAPE_PAGE_IN_POINTS

import settings

logging.config.dictConfig(settings.LOGGING)
log = logging.getLogger('certificates.' + __name__)

DATE_TOP_MARGIN_OFFSET = 13


class CmeRenderer(object):
    def __init__(self, cert_data, page, color, font_list, width_text_in_points, margin_in_points):
        self.cert_data = cert_data
        self.page = page
        self.color = color
        self.font_list = font_list
        self.width_text_in_points = width_text_in_points
        self.margin_in_points = margin_in_points

    def draw_date_on_page(self, date_string):
        style = ParagraphStyle(
            name='date-text',
            fontSize=18,
            leading=24,
            textColor=self.color,
            alignment=TA_RIGHT,
        )
        (dummy_0, dummy_1, style) = font_for_string(
            apply_style_to_font_list(self.font_list, style),
            date_string
        )
        max_width = 200
        max_height = style.fontSize

        paragraph = Paragraph(date_string, style)
        paragraph.wrapOn(self.page, max_width, max_height)
        # positioning paragraph wrapping box from its bottom left corner
        # calculating positioning for top right corner of page
        paragraph.drawOn(
            self.page,
            WIDTH_LANDSCAPE_PAGE_IN_POINTS - self.margin_in_points - max_width,
            HEIGHT_LANDSCAPE_PAGE_IN_POINTS - self.margin_in_points - max_height - DATE_TOP_MARGIN_OFFSET
        )

    def draw_student_name_on_page(self, student_name):
        student_name = u"<b>{0}</b>".format(student_name)
        style = ParagraphStyle(
            name='big-name-text',
            textColor=self.color,
            alignment=TA_LEFT,
        )
        (dummy_0, dummy_1, style) = font_for_string(
            apply_style_to_font_list(self.font_list, style),
            student_name
        )
        max_font_size_in_points = 36
        max_leading = max_font_size_in_points * 1.5
        max_height = max_font_size_in_points * 1.5
        position_bottom_mininum = 370     # distance from bottom of page (in points)
        paragraph = autoscale_text(
            self.page,
            student_name,
            max_font_size_in_points,
            max_leading,
            max_height,
            self.width_text_in_points,
            style
        )
        width, height = paragraph.wrapOn(self.page, self.width_text_in_points, max_height)
        position_bottom = position_bottom_mininum + ((max_height - height) / 2)
        paragraph.drawOn(self.page, self.margin_in_points, position_bottom)

    def draw_course_on_page(self, course_name_string):
        course_title = u"<b>{0}</b>".format(course_name_string)
        style = ParagraphStyle(
            name='big-course-text',
            textColor=self.color,
            alignment=TA_LEFT,
        )
        (dummy_0, dummy_1, style) = font_for_string(
            apply_style_to_font_list(self.font_list, style),
            course_title
        )
        max_font_size_in_points = 32
        max_leading = max_font_size_in_points * 1.3
        max_height = max_font_size_in_points * 3.3
        position_bottom_mininum = 210
        paragraph = autoscale_text(
            self.page,
            course_title,
            max_font_size_in_points,
            max_leading,
            max_height,
            self.width_text_in_points,
            style
        )
        width, height = paragraph.wrapOn(self.page, self.width_text_in_points, max_height)
        position_bottom = (
            position_bottom_mininum +
            ((max_height - height) / 2) +
            (style.fontSize / 5)
        )
        paragraph.drawOn(self.page, self.margin_in_points, position_bottom)

    def draw_credits_on_page(self, gets_md_cert):
        """
        This is pretty fundamentally not internationalizable; like
        the rest of the certificate template renderers we do text
        interpolation that assumes English subject/object
        relationships. If this language needs to be varied, the best
        place to do that is probably a forked rendering method.
        There is some additional information in the documentation.
        """
        credit_info = self.cert_data.get('CREDITS')
        if credit_info:
            if gets_md_cert:
                credits_string = u"and is awarded {credit_info}".format(
                    credit_info=credit_info.decode('utf-8'),
                )
            else:
                credits_string = u"The activity was designated for {credit_info}".format(
                    credit_info=credit_info.decode('utf-8'),
                )
            style = ParagraphStyle(
                name='credits-text',
                fontSize=18,
                leading=24,
                textColor=self.color,
                alignment=TA_LEFT,
            )
            font_list = apply_style_to_font_list(self.font_list, style)
            (dummy_0, dummy_1, style) = font_for_string(
                font_list,
                credits_string
            )
            max_height = style.fontSize
            position_bottom = 175
            paragraph = Paragraph(credits_string, style)
            width, height = paragraph.wrapOn(self.page, self.width_text_in_points, max_height)
            paragraph.drawOn(self.page, self.margin_in_points, position_bottom)

    def draw_tag_on_page(self, gets_md_cert):
        if gets_md_cert:
            tag_string = 'MD/DO'
        else:
            tag_string = 'AHP'
        style_tag_text = ParagraphStyle(
            name='tag-text',
            fontSize=10,
            leading=12,
            textColor=self.color,
            alignment=TA_RIGHT,
        )
        font_list = apply_style_to_font_list(self.font_list, style_tag_text)
        (dummy_0, dummy_1, tag_style) = font_for_string(font_list, tag_string)
        max_width = 50
        max_height = tag_style.fontSize
        position_bottom = 53
        paragraph = Paragraph(tag_string, tag_style)
        width, height = paragraph.wrapOn(self.page, max_width, max_height)
        paragraph.drawOn(self.page, (self.width_text_in_points + self.margin_in_points - max_width), position_bottom)
