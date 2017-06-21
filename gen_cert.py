# -*- coding: utf-8 -*-

import copy
import datetime
import gnupg
import math
import os
import re
import shutil
import StringIO
import uuid

from reportlab.platypus import Paragraph
from PyPDF2 import PdfFileWriter, PdfFileReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4, letter, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from glob import glob
from HTMLParser import HTMLParser

import settings
import collections
import itertools
import logging.config
import reportlab.rl_config
import tempfile
import boto3
from bidi.algorithm import get_display
import arabic_reshaper

from opaque_keys.edx.keys import CourseKey

reportlab.rl_config.warnOnMissingFontGlyphs = 0


RE_ISODATES = re.compile("(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})")
TEMPLATE_DIR = settings.TEMPLATE_DIR
BUCKET = settings.CERT_BUCKET
CERT_KEY_ID = settings.CERT_KEY_ID
logging.config.dictConfig(settings.LOGGING)
log = logging.getLogger('certificates.' + __name__)
S3_CERT_PATH = 'downloads'
S3_VERIFY_PATH = getattr(settings, 'S3_VERIFY_PATH', 'cert')
TARGET_FILENAME = getattr(settings, 'CERT_FILENAME', 'Certificate.pdf')
TMP_GEN_DIR = getattr(settings, 'TMP_GEN_DIR', '/var/tmp/generated_certs')
CERTS_ARE_CALLED = getattr(settings, 'CERTS_ARE_CALLED', 'certificate')
CERTS_ARE_CALLED_PLURAL = getattr(settings, 'CERTS_ARE_CALLED_PLURAL', 'certificates')

# reduce logging level for gnupg
l = logging.getLogger('gnupg')
l.setLevel('WARNING')

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

# These are small, so let's just load them at import time and keep them around
# so we don't have to keep doing the file I/o
BLANK_PDFS = {
    'landscape-A4': PdfFileReader(file("{0}/blank.pdf".format(TEMPLATE_DIR), "rb")),
    'landscape-letter': PdfFileReader(file("{0}/blank-letter.pdf".format(TEMPLATE_DIR), "rb")),
    'portrait-A4': PdfFileReader(file("{0}/blank-portrait-A4.pdf".format(TEMPLATE_DIR), "rb")),
}


def prettify_isodate(isoformat_date):
    """Convert a string like '2012-02-02' to one like 'February 2nd, 2012'"""
    m = RE_ISODATES.match(isoformat_date)
    if not m:
        raise TypeError("prettify_isodate called with incorrect date format: %s" % isoformat_date)
    day_suffixes = {'1': 'st', '2': 'nd', '3': 'rd', '21': 'st', '22': 'nd', '23': 'rd', '31': 'st'}
    months = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
              'August', 'September', 'October', 'November', 'December']
    date = {'year': '', 'month': '', 'day': '', 'suffix': 'th'}
    date['year'] = m.group('year')
    date['month'] = months[int(m.group('month')) - 1]
    date['day'] = m.group('day').lstrip('0')
    date['suffix'] = day_suffixes.get(date['day'], 'th')
    return "%(month)s %(day)s%(suffix)s, %(year)s" % date


def get_cert_date(calling_date_parameter, configured_date_parameter):
    """Get pertinent date for display on cert

    - If cert passes a set date in 'calling_date_parameter', format that
    - If using the "ROLLING" certs feature, use today's date
    - If all else fails use 'configured_date_parameter' for date
    """
    if calling_date_parameter:
        date_value = prettify_isodate(calling_date_parameter)
    elif configured_date_parameter == "ROLLING":
        generate_date = datetime.date.today().isoformat()
        date_value = prettify_isodate(generate_date)
    else:
        date_value = configured_date_parameter

    date_string = u"{0}".format(date_value)

    return date_string


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


class CertificateGen(object):
    """Manages the pdf, signatures, and S3 bucket for course certificates."""

    def __init__(self, course_id, template_pdf=None, aws_id=None, aws_key=None,
                 dir_prefix=None, long_org=None, long_course=None, issued_date=None):
        """Load a pdf template and initialize

        Multiple certificates can be generated and uploaded for a single course.

        course_id    - Full course_id (ex: course-v1:MITx+6.00x+1T2015)
        course_name  - Human readable course title (ex: Introduction to Curling)
        dir_prefix   - Temporary directory for file generation. Ceritificates
                       and signatures are copied here temporarily before they
                       are uploaded to S3
        template_pdf - (optional) Template (filename.pdf) to use for the
                       certificate generation.
        aws_id       - necessary for S3 uploads
        aws_key      - necessary for S3 uploads

        course_id is used to look up extra data from settings.CERT_DATA,
        including (but not necessarily limited to):
          * LONG_ORG     - long name for the organization
          * ISSUED_DATE  - month, year that corresponds to the
                           run of the course
          * TEMPLATEFILE - the template pdf filename to use, equivalent to
                           template_pdf parameter
        """
        if dir_prefix is None:
            self._ensure_dir(TMP_GEN_DIR)
            dir_prefix = tempfile.mkdtemp(prefix=TMP_GEN_DIR)
        self._ensure_dir(dir_prefix)
        self.dir_prefix = dir_prefix

        self.aws_id = str(aws_id)
        self.aws_key = str(aws_key)

        cert_data = settings.CERT_DATA.get(course_id, {})
        self.cert_data = cert_data

        def interstitial_factory():
            """ Generate default values for interstitial_texts defaultdict """
            return itertools.repeat(cert_data.get('interstitial', {}).get('Pass', '')).next

        # lookup long names from the course_id
        try:
            self.long_org = long_org or cert_data.get('LONG_ORG', '').encode('utf-8') or settings.DEFAULT_ORG
            self.long_course = long_course or cert_data.get('LONG_COURSE', '').encode('utf-8')
            self.issued_date = issued_date or cert_data.get('ISSUED_DATE', '').encode('utf-8') or 'ROLLING'
            self.interstitial_texts = collections.defaultdict(interstitial_factory())
            self.interstitial_texts.update(cert_data.get('interstitial', {}))
        except KeyError:
            log.critical("Unable to lookup long names for course {0}".format(course_id))
            raise

        # if COURSE or ORG is set in the configuration attempt to parse.
        # This supports both new and old style course keys.
        course_key = CourseKey.from_string(course_id)
        self.course = cert_data.get('COURSE', course_key.course)
        self.org = cert_data.get('ORG', course_key.org)

        # get the template version based on the course settings in the
        # certificates repo, with sensible defaults so that we can generate
        # pdfs differently for the different templates
        self.template_version = cert_data.get('VERSION', 1)
        self.template_type = 'honor'
        # search for certain keywords in the file name, we'll probably want to
        # be better at parsing this later
        # If TEMPLATEFILE is set in cert-data.yml, this value has top priority.
        # Else if a value is passed in to the constructor (eg, from xqueue), it is used,
        # Else, the filename is calculated from the version and course_id.
        template_pdf = cert_data.get('TEMPLATEFILE', template_pdf)
        template_prefix = '{0}/v{1}-cert-templates'.format(TEMPLATE_DIR, self.template_version)
        template_pdf_filename = "{0}/certificate-template-{1}-{2}.pdf".format(template_prefix, self.org, self.course)
        if template_pdf:
            template_pdf_filename = "{0}/{1}".format(template_prefix, template_pdf)
            if 'verified' in template_pdf:
                self.template_type = 'verified'
        try:
            self.template_pdf = PdfFileReader(file(template_pdf_filename, "rb"))
        except IOError as e:
            log.critical("I/O error ({0}): {1} opening {2}".format(e.errno, e.strerror, template_pdf_filename))
            raise

        self.cert_label_singular = cert_data.get('CERTS_ARE_CALLED', CERTS_ARE_CALLED)
        self.cert_label_plural = cert_data.get('CERTS_ARE_CALLED_PLURAL', CERTS_ARE_CALLED_PLURAL)
        self.course_association_text = cert_data.get('COURSE_ASSOCIATION_TEXT', 'a course of study')

    def delete_certificate(self, delete_download_uuid, delete_verify_uuid):
        # TODO remove/archive an existing certificate
        raise NotImplementedError

    def create_and_upload(
        self,
        name,
        upload=settings.S3_UPLOAD,
        cleanup=True,
        copy_to_webroot=settings.COPY_TO_WEB_ROOT,
        cert_web_root=settings.CERT_WEB_ROOT,
        grade=None,
        designation=None,
    ):
        """
        name - Full name that will be on the certificate
        upload - Upload to S3 (defaults to True)

        set upload to False if you do not want to upload to S3,
        this will also keep temporary files that are created.

        returns a tuple containing the UUIDs for download, verify and
        the full download URL.  If upload is set to False
        download_url in the return will be None

        return (download_uuid, verify_uuid, download_url)

        verify_uuid will be None if there is no verification signature

        """
        download_uuid = None
        verify_uuid = None
        download_url = None
        s3_conn = None
        bucket = None

        certificates_path = os.path.join(self.dir_prefix, S3_CERT_PATH)
        verify_path = os.path.join(self.dir_prefix, S3_VERIFY_PATH)

        (download_uuid, verify_uuid, download_url) = self._generate_certificate(student_name=name,
                                                                                download_dir=certificates_path,
                                                                                verify_dir=verify_path,
                                                                                grade=grade,
                                                                                designation=designation,)

        # upload generated certificate and verification files to S3,
        # or copy them to the web root. Or both.
        my_certs_path = os.path.join(certificates_path, download_uuid)
        my_verify_path = os.path.join(verify_path, verify_uuid)

        if upload or copy_to_webroot:
            for subtree in (my_certs_path, my_verify_path):
                for dirpath, dirnames, filenames in os.walk(subtree):
                    for filename in filenames:
                        local_path = os.path.join(dirpath, filename)
                        dest_path = os.path.relpath(local_path, start=self.dir_prefix)
                        publish_dest = os.path.join(cert_web_root, dest_path)

                        if upload:
                            try:
                                s3 = boto3.resource('s3')
                                s3.Bucket(BUCKET).put_object(Key=dest_path, Body=open(local_path, 'rb'), ACL='public-read')                                
                            except:
                                raise
                            else:
                                log.info("uploaded {local} to {s3path}".format(local=local_path, s3path=dest_path))

                        if copy_to_webroot:
                            try:
                                dirname = os.path.dirname(publish_dest)
                                if not os.path.exists(dirname):
                                    os.makedirs(dirname)
                                shutil.copy(local_path, publish_dest)
                            except:
                                raise
                            else:
                                log.info("published {local} to {web}".format(local=local_path, web=publish_dest))

        if cleanup:
            for working_dir in (certificates_path, verify_path):
                if os.path.exists(working_dir):
                    shutil.rmtree(working_dir)

        return (download_uuid, verify_uuid, download_url)

    def _generate_certificate(
        self,
        student_name,
        download_dir,
        verify_dir,
        filename=TARGET_FILENAME,
        grade=None,
        designation=None,
    ):
        """Generate a certificate PDF, signature and validation html files.

        return (download_uuid, verify_uuid, download_url)
        """
        versionmap = {
            1: self._generate_v1_certificate,
            2: self._generate_v2_certificate,
            'MIT_PE': self._generate_mit_pe_certificate,
            'stanford': self._generate_stanford_SOA,
            '3_dynamic': self._generate_v3_dynamic_certificate,
            'stanford_cme': self._generate_stanford_cme_certificate,
        }
        # TODO: we should be taking args, kwargs, and passing those on to our callees
        return versionmap[self.template_version](
            student_name,
            download_dir,
            verify_dir,
            filename,
            grade,
            designation,
        )

    def _generate_v1_certificate(
        self,
        student_name,
        download_dir,
        verify_dir,
        filename=TARGET_FILENAME,
        grade=None,
        designation=None,
    ):
        # A4 page size is 297mm x 210mm

        verify_uuid = uuid.uuid4().hex
        download_uuid = uuid.uuid4().hex
        download_url = "{base_url}/{cert}/{uuid}/{file}".format(
            base_url=settings.CERT_DOWNLOAD_URL,
            cert=S3_CERT_PATH, uuid=download_uuid, file=filename)
        filename = os.path.join(download_dir, download_uuid, filename)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        c = canvas.Canvas(overlay_pdf_buffer, pagesize=landscape(A4))

        # 0 0 - normal
        # 0 1 - italic
        # 1 0 - bold
        # 1 1 - italic and bold
        addMapping('OpenSans-Light', 0, 0, 'OpenSans-Light')
        addMapping('OpenSans-Light', 0, 1, 'OpenSans-LightItalic')
        addMapping('OpenSans-Light', 1, 0, 'OpenSans-Bold')

        addMapping('OpenSans-Regular', 0, 0, 'OpenSans-Regular')
        addMapping('OpenSans-Regular', 0, 1, 'OpenSans-Italic')
        addMapping('OpenSans-Regular', 1, 0, 'OpenSans-Bold')
        addMapping('OpenSans-Regular', 1, 1, 'OpenSans-BoldItalic')

        styleArial = ParagraphStyle(name="arial", leading=10, fontName='Arial Unicode')
        styleOpenSans = ParagraphStyle(name="opensans-regular", leading=10, fontName='OpenSans-Regular')
        styleOpenSansLight = ParagraphStyle(name="opensans-light", leading=10, fontName='OpenSans-Light')

        # Text is overlayed top to bottom
        #   * Issued date (top right corner)
        #   * "This is to certify that"
        #   * Student's name
        #   * "successfully completed"
        #   * Course name
        #   * "a course of study.."
        #   * honor code url at the bottom
        WIDTH = 297  # width in mm (A4)
        HEIGHT = 210  # hight in mm (A4)

        LEFT_INDENT = 49  # mm from the left side to write the text
        RIGHT_INDENT = 49  # mm from the right side for the CERTIFICATE

        # CERTIFICATE

        styleOpenSansLight.fontSize = 19
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "CERTIFICATE"

        # Right justified so we compute the width
        width = stringWidth(
            paragraph_string,
            'OpenSans-Light',
            19,
        ) / mm
        paragraph = Paragraph("{0}".format(
            paragraph_string), styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, (WIDTH - RIGHT_INDENT - width) * mm, 163 * mm)

        # Issued ..

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
            0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "Issued {0}".format(self.issued_date)

        # Right justified so we compute the width
        width = stringWidth(
            paragraph_string,
            'OpenSans-LightItalic',
            12,
        ) / mm
        paragraph = Paragraph("<i>{0}</i>".format(
            paragraph_string), styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, (WIDTH - RIGHT_INDENT - width) * mm, 155 * mm)

        # This is to certify..

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
            0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "This is to certify that"
        paragraph = Paragraph(paragraph_string, styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 132.5 * mm)

        #  Student name

        # default is to use the DejaVu font for the name,
        # will fall back to Arial if there are
        # unusual characters
        style = styleOpenSans
        style.leading = 10
        width = stringWidth(student_name.decode('utf-8'), 'OpenSans-Bold', 34) / mm
        paragraph_string = "<b>{0}</b>".format(student_name)

        if self._use_unicode_font(student_name):
            style = styleArial
            width = stringWidth(student_name.decode('utf-8'), 'Arial Unicode', 34) / mm
            # There is no bold styling for Arial :(
            paragraph_string = "{0}".format(student_name)

        # We will wrap at 200mm in, so if we reach the end (200-47)
        # decrease the font size
        if width > 153:
            style.fontSize = 18
            nameYOffset = 121.5
        else:
            style.fontSize = 34
            nameYOffset = 124.5

        style.textColor = colors.Color(
            0, 0.624, 0.886)
        style.alignment = TA_LEFT

        paragraph = Paragraph(paragraph_string, style)
        paragraph.wrapOn(c, 200 * mm, 214 * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, nameYOffset * mm)

        # Successfully completed

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
            0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "successfully completed"
        if '7.00x' in self.course:
            paragraph_string = "successfully completed the inaugural offering of"
        else:
            paragraph_string = "successfully completed"

        paragraph = Paragraph(paragraph_string, styleOpenSansLight)

        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 108 * mm)

        # Course name

        # styleOpenSans.fontName = 'OpenSans-BoldItalic'
        if 'PH207x' in self.course:
            styleOpenSans.fontSize = 18
            styleOpenSans.leading = 21
        elif '4.01x' in self.course:
            styleOpenSans.fontSize = 20
            styleOpenSans.leading = 10
        elif 'Stat2.1x' in self.course:
            styleOpenSans.fontSize = 20
            styleOpenSans.leading = 10
        elif 'CS191x' in self.course:
            styleOpenSans.fontSize = 20
            styleOpenSans.leading = 10
        elif '6.00x' in self.course:
            styleOpenSans.fontSize = 20
            styleOpenSans.leading = 21
        elif 'PH278x' in self.course:
            styleOpenSans.fontSize = 20
            styleOpenSans.leading = 10
        else:
            styleOpenSans.fontSize = 24
            styleOpenSans.leading = 10
        styleOpenSans.textColor = colors.Color(
            0, 0.624, 0.886)
        styleOpenSans.alignment = TA_LEFT

        paragraph_string = u"<b><i>{0}: {1}</i></b>".format(
            self.course, self.long_course.decode('utf-8'))
        paragraph = Paragraph(paragraph_string, styleOpenSans)
        # paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        if 'PH207x' in self.course:
            paragraph.wrapOn(c, 180 * mm, HEIGHT * mm)
            paragraph.drawOn(c, LEFT_INDENT * mm, 91 * mm)
        elif '6.00x' in self.course:
            paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
            paragraph.drawOn(c, LEFT_INDENT * mm, 95 * mm)
        else:
            paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
            paragraph.drawOn(c, LEFT_INDENT * mm, 99 * mm)

        # A course of study..

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.textColor = colors.Color(
            0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "a course of study offered by <b>{0}</b>" \
                           ", an online learning<br /><br />initiative of " \
                           "<b>{1}</b> through <b>edX</b>.".format(
                               self.org, self.long_org.decode('utf-8'))

        paragraph = Paragraph(paragraph_string, styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 78 * mm)

        # Honor code

        styleOpenSansLight.fontSize = 7
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
            0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_CENTER

        paragraph_string = "HONOR CODE CERTIFICATE<br/>" \
            "*Authenticity of this certificate can be verified at " \
            "<a href='{verify_url}/{verify_path}/{verify_uuid}'>" \
            "{verify_url}/{verify_path}/{verify_uuid}</a>"

        paragraph_string = paragraph_string.format(
            verify_url=settings.CERT_VERIFY_URL,
            verify_path=S3_VERIFY_PATH,
            verify_uuid=verify_uuid)
        paragraph = Paragraph(paragraph_string, styleOpenSansLight)

        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, 0 * mm, 28 * mm)

        c.showPage()
        c.save()

        # Merge the overlay with the template, then write it to file
        output = PdfFileWriter()
        overlay = PdfFileReader(overlay_pdf_buffer)

        # We need a page to overlay on.
        # So that we don't have to open the template
        # several times, we open a blank pdf several times instead
        # (much faster)
        final_certificate = copy.copy(BLANK_PDFS['landscape-A4']).getPage(0)
        final_certificate.mergePage(self.template_pdf.getPage(0))
        final_certificate.mergePage(overlay.getPage(0))

        output.addPage(final_certificate)

        self._ensure_dir(filename)

        outputStream = file(filename, "wb")
        output.write(outputStream)
        outputStream.close()

        self._generate_verification_page(
            student_name,
            filename,
            verify_dir,
            verify_uuid,
            download_url
        )

        return (download_uuid, verify_uuid, download_url)

    def _generate_v2_certificate(
        self,
        student_name,
        download_dir,
        verify_dir,
        filename=TARGET_FILENAME,
        grade=None,
        designation=None,
    ):
        """
        We have a new set of certificates that we want to generate which means brand new generation of certs
        """

        # 8.5x11 page size 279.4mm x 215.9mm
        WIDTH = 279  # width in mm (8.5x11)
        HEIGHT = 216  # height in mm (8.5x11)

        verify_uuid = uuid.uuid4().hex
        download_uuid = uuid.uuid4().hex
        download_url = "{base_url}/{cert}/{uuid}/{file}".format(
            base_url=settings.CERT_DOWNLOAD_URL,
            cert=S3_CERT_PATH, uuid=download_uuid, file=filename
        )
        filename = os.path.join(download_dir, download_uuid, filename)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        c = canvas.Canvas(overlay_pdf_buffer, pagesize=landscape(letter))

        styleOpenSans = ParagraphStyle(name="opensans-regular", leading=10,
                                       fontName='OpenSans-Regular')
        styleArial = ParagraphStyle(name="arial", leading=10,
                                    fontName='Arial Unicode')

        # Text is overlayed top to bottom
        #   * Issued date (top right corner)
        #   * "This is to certify that"
        #   * Student's name
        #   * "successfully completed"
        #   * Course name
        #   * "a course of study.."
        #   * honor code url at the bottom

        # New things below

        # STYLE: typeface assets
        addMapping('AvenirNext-Regular', 0, 0, 'AvenirNext-Regular')
        addMapping('AvenirNext-DemiBold', 1, 0, 'AvenirNext-DemiBold')

        # STYLE: grid/layout
        LEFT_INDENT = 23  # mm from the left side to write the text
        MAX_WIDTH = 150  # maximum width on the content in the cert, used for wrapping

        # STYLE: template-wide typography settings
        style_type_metacopy_size = 13
        style_type_metacopy_leading = 10

        style_type_footer_size = 8

        style_type_name_size = 36
        style_type_name_leading = 53
        style_type_name_med_size = 28
        style_type_name_med_leading = 41
        style_type_name_small_size = 22
        style_type_name_small_leading = 27

        style_type_course_size = 24
        style_type_course_leading = 28
        style_type_course_small_size = 16
        style_type_course_small_leading = 20

        # STYLE: template-wide color settings
        style_color_metadata = colors.Color(0.541176, 0.509804, 0.560784)
        style_color_name = colors.Color(0.000000, 0.000000, 0.000000)

        # STYLE: positioning
        pos_metacopy_title_y = 120
        pos_metacopy_achivement_y = 88
        pos_metacopy_org_y = 50

        pos_name_y = 94
        pos_name_med_y = 95
        pos_name_small_y = 95
        pos_name_no_wrap_offset_y = 2

        pos_course_y = 68
        pos_course_small_y = 66
        pos_course_no_wrap_offset_y = 5

        pos_footer_url_x = 83
        pos_footer_url_y = 20
        pos_footer_date_x = LEFT_INDENT
        pos_footer_date_y = 20

        # STYLE: verified settings
        v_style_color_course = colors.Color(0.701961, 0.231373, 0.400000)

        # HTML Parser ####
        # Since the final string is HTML in a PDF we need to un-escape the html
        # when calculating the string width.
        html = HTMLParser()

        # ELEM: Metacopy
        styleAvenirNext = ParagraphStyle(name="avenirnext-regular", fontName='AvenirNext-Regular')

        styleAvenirNext.alignment = TA_LEFT
        styleAvenirNext.fontSize = style_type_metacopy_size
        styleAvenirNext.leading = style_type_metacopy_leading
        styleAvenirNext.textColor = style_color_metadata

        # ELEM: Metacopy - Title: This is to certify that
        if self.template_type == 'verified':
            y_offset = pos_metacopy_title_y

            paragraph_string = 'This is to certify that'

            paragraph = Paragraph(paragraph_string, styleAvenirNext)
            paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
            paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        # ELEM: Student Name
        # default is to use Avenir for the name,
        # will fall back to Arial if there are
        # unusual characters
        y_offset_name = pos_name_y
        y_offset_name_med = pos_name_med_y
        y_offset_name_small = pos_name_small_y

        styleAvenirStudentName = ParagraphStyle(name="avenirnext-demi", fontName='AvenirNext-DemiBold')
        styleAvenirStudentName.leading = style_type_name_small_size

        style = styleAvenirStudentName

        html_student_name = html.unescape(student_name)
        larger_width = stringWidth(html_student_name.decode('utf-8'),
                                   'AvenirNext-DemiBold', style_type_name_size) / mm
        smaller_width = stringWidth(
            html_student_name.decode('utf-8'),
            'AvenirNext-DemiBold', style_type_name_small_size) / mm

        # TODO: get all strings working reshaped and handling bi-directional strings
        paragraph_string = arabic_reshaper.reshape(student_name.decode('utf-8'))
        paragraph_string = get_display(paragraph_string)

        # Avenir only supports Latin-1
        # Switch to using OpenSans if we can
        if self._use_non_latin(student_name):
            style = styleOpenSans
            larger_width = stringWidth(html_student_name.decode('utf-8'),
                                       'OpenSans-Regular', style_type_name_size) / mm

        # if we can't use OpenSans, use Arial
        if self._use_unicode_font(student_name):
            style = styleArial
            larger_width = stringWidth(html_student_name.decode('utf-8'),
                                       'Arial Unicode', style_type_name_size) / mm

        # if the name is too long, shrink the font size
        if larger_width < MAX_WIDTH:
            style.fontSize = style_type_name_size
            style.leading = style_type_name_leading
            y_offset = y_offset_name
        elif smaller_width < MAX_WIDTH:
            y_offset = y_offset_name_med + pos_name_no_wrap_offset_y
            style.fontSize = style_type_name_med_size
            style.leading = style_type_name_med_leading
        else:
            y_offset = y_offset_name_small
            style.fontSize = style_type_name_small_size
            style.leading = style_type_name_small_leading
        style.textColor = style_color_name
        style.alignment = TA_LEFT

        paragraph = Paragraph(paragraph_string, style)
        paragraph.wrapOn(c, MAX_WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        # ELEM: Metacopy - Achievement: successfully completed and received a passing grade in
        y_offset = pos_metacopy_achivement_y

        paragraph_string = 'successfully completed and received a passing grade in'

        paragraph = Paragraph("{0}".format(paragraph_string), styleAvenirNext)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        # ELEM: Course Name
        y_offset_larger = pos_course_y
        y_offset_smaller = pos_course_small_y

        styleAvenirCourseName = ParagraphStyle(name="avenirnext-demi", fontName='AvenirNext-DemiBold')
        styleAvenirCourseName.textColor = style_color_name
        if self.template_type == 'verified':
            styleAvenirCourseName.textColor = v_style_color_course

        paragraph_string = u"{0}: {1}".format(self.course, self.long_course)
        html_paragraph_string = html.unescape(paragraph_string)
        larger_width = stringWidth(html_paragraph_string.decode('utf-8'),
                                   'AvenirNext-DemiBold', style_type_course_size) / mm
        smaller_width = stringWidth(html_paragraph_string.decode('utf-8'),
                                    'AvenirNext-DemiBold', style_type_course_small_size) / mm

        if larger_width < MAX_WIDTH:
            styleAvenirCourseName.fontSize = style_type_course_size
            styleAvenirCourseName.leading = style_type_course_leading
            y_offset = y_offset_larger
        elif smaller_width < MAX_WIDTH:
            styleAvenirCourseName.fontSize = style_type_course_small_size
            styleAvenirCourseName.leading = style_type_course_small_leading
            y_offset = y_offset_smaller + pos_course_no_wrap_offset_y
        else:
            styleAvenirCourseName.fontSize = style_type_course_small_size
            styleAvenirCourseName.leading = style_type_course_small_leading
            y_offset = y_offset_smaller

        styleAvenirCourseName.alignment = TA_LEFT

        paragraph = Paragraph(paragraph_string, styleAvenirCourseName)

        paragraph.wrapOn(c, MAX_WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        # ELEM: Metacopy - Org: a course of study...
        y_offset = pos_metacopy_org_y
        paragraph_string = "{2} offered by {0}" \
                           ", an online learning<br /><br />initiative of " \
                           "{1} through edX.".format(
                               self.org, self.long_org.decode('utf-8'), self.course_association_text)

        paragraph = Paragraph(paragraph_string, styleAvenirNext)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        # ELEM: Footer
        styleAvenirFooter = ParagraphStyle(name="avenirnext-demi", fontName='AvenirNext-DemiBold')

        styleAvenirFooter.alignment = TA_LEFT
        styleAvenirFooter.fontSize = style_type_footer_size

        # ELEM: Footer - Issued on Date
        x_offset = pos_footer_date_x
        y_offset = pos_footer_date_y
        paragraph_string = "Issued {0}".format(self.issued_date)
        # Right justified so we compute the width
        paragraph = Paragraph("{0}".format(
            paragraph_string), styleAvenirFooter)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        # ELEM: Footer - Verify Authenticity URL
        y_offset = pos_footer_url_y
        x_offset = pos_footer_url_x
        paragraph_string = "<a href='https://{bucket}/{verify_path}/{verify_uuid}'>" \
                           "https://{bucket}/{verify_path}/{verify_uuid}</a>"
        paragraph_string = paragraph_string.format(bucket=BUCKET,
                                                   verify_path=S3_VERIFY_PATH,
                                                   verify_uuid=verify_uuid)

        paragraph = Paragraph(paragraph_string, styleAvenirFooter)

        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, x_offset * mm, y_offset * mm)

        c.showPage()
        c.save()

        # Merge the overlay with the template, then write it to file
        output = PdfFileWriter()
        overlay = PdfFileReader(overlay_pdf_buffer)

        # We need a page to overlay on.
        # So that we don't have to open the template
        # several times, we open a blank pdf several times instead
        # (much faster)

        final_certificate = copy.copy(BLANK_PDFS['landscape-letter']).getPage(0)
        final_certificate.mergePage(self.template_pdf.getPage(0))
        final_certificate.mergePage(overlay.getPage(0))

        output.addPage(final_certificate)

        self._ensure_dir(filename)

        outputStream = file(filename, "wb")
        output.write(outputStream)
        outputStream.close()

        self._generate_verification_page(
            student_name,
            filename,
            verify_dir,
            verify_uuid,
            download_url
        )

        return (download_uuid, verify_uuid, download_url)

    def _generate_mit_pe_certificate(
        self,
        student_name,
        download_dir,
        verify_dir,
        filename=TARGET_FILENAME,
        grade=None,
        designation=None,
    ):
        """
        Generate the BigDataX certs
        """
        # 8.5x11 page size 279.4mm x 215.9mm
        WIDTH = 279  # width in mm (8.5x11)
        HEIGHT = 216  # height in mm (8.5x11)

        download_uuid = uuid.uuid4().hex
        verify_uuid = uuid.uuid4().hex
        download_url = "{base_url}/{cert}/{uuid}/{file}".format(
            base_url=settings.CERT_DOWNLOAD_URL,
            cert=S3_CERT_PATH, uuid=download_uuid, file=filename
        )

        filename = os.path.join(download_dir, download_uuid, filename)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        c = canvas.Canvas(overlay_pdf_buffer)
        c.setPageSize((WIDTH * mm, HEIGHT * mm))

        # STYLE: grid/layout
        LEFT_INDENT = 10  # mm from the left side to write the text
        MAX_WIDTH = 260  # maximum width on the content in the cert, used for wrapping

        # STYLE: template-wide typography settings
        style_type_name_size = 36
        style_type_name_leading = 53
        style_type_name_med_size = 22
        style_type_name_med_leading = 27
        style_type_name_small_size = 18
        style_type_name_small_leading = 21

        # STYLE: template-wide color settings
        style_color_name = colors.Color(0.000000, 0.000000, 0.000000)

        # STYLE: positioning
        pos_name_y = 137
        pos_name_med_y = 142
        pos_name_small_y = 140
        pos_name_no_wrap_offset_y = 2

        # HTML Parser
        # Since the final string is HTML in a PDF we need to un-escape the html
        # when calculating the string width.
        html = HTMLParser()

        # ELEM: Student Name
        # default is to use Garamond for the name,
        # will fall back to Arial if there are
        # unusual characters
        y_offset_name = pos_name_y
        y_offset_name_med = pos_name_med_y
        y_offset_name_small = pos_name_small_y

        styleUnicode = ParagraphStyle(name="arial", leading=10, fontName='Arial Unicode')
        styleGaramondStudentName = ParagraphStyle(name="garamond", fontName='Garamond-Bold')
        styleGaramondStudentName.leading = style_type_name_small_size

        style = styleGaramondStudentName

        html_student_name = html.unescape(student_name)
        larger_width = stringWidth(html_student_name.decode('utf-8'),
                                   'Garamond-Bold', style_type_name_size) / mm
        smaller_width = stringWidth(html_student_name.decode('utf-8'),
                                    'Garamond-Bold', style_type_name_small_size) / mm

        paragraph_string = arabic_reshaper.reshape(student_name.decode('utf-8'))
        paragraph_string = get_display(paragraph_string)

        # Garamond only supports Latin-1
        # if we can't use it, use Arial
        if self._use_unicode_font(student_name):
            style = styleUnicode
            larger_width = stringWidth(html_student_name.decode('utf-8'),
                                       'Arial Unicode', style_type_name_size) / mm

        # if the name is too long, shrink the font size
        if larger_width < MAX_WIDTH:
            style.fontSize = style_type_name_size
            style.leading = style_type_name_leading
            y_offset = y_offset_name
        elif smaller_width < MAX_WIDTH:
            y_offset = y_offset_name_med + pos_name_no_wrap_offset_y
            style.fontSize = style_type_name_med_size
            style.leading = style_type_name_med_leading
        else:
            y_offset = y_offset_name_small
            style.fontSize = style_type_name_small_size
            style.leading = style_type_name_small_leading
        style.textColor = style_color_name
        style.alignment = TA_CENTER

        paragraph = Paragraph(paragraph_string, style)
        paragraph.wrapOn(c, MAX_WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        # Generate the final PDF
        c.showPage()
        c.save()

        # Merge the overlay with the template, then write it to file
        output = PdfFileWriter()
        overlay = PdfFileReader(overlay_pdf_buffer)

        # We need a page to overlay on.
        # So that we don't have to open the template
        # several times, we open a blank pdf several times instead
        # (much faster)

        blank_pdf = PdfFileReader(
            file("{0}/blank-letter.pdf".format(TEMPLATE_DIR), "rb")
        )

        final_certificate = blank_pdf.getPage(0)
        final_certificate.mergePage(self.template_pdf.getPage(0))
        final_certificate.mergePage(overlay.getPage(0))

        output.addPage(final_certificate)

        self._ensure_dir(filename)

        outputStream = file(filename, "wb")
        output.write(outputStream)
        outputStream.close()
        return (download_uuid, verify_uuid, download_url)

    def _generate_verification_page(self, name, filename, output_dir, verify_uuid, download_url):
        """
        This generates the gpg signature and the
        verification files including
        the static html files that will be seen when
        the user clicks the verification link.

        name - full name of the student
        filename - path on the local filesystem to the certificate pdf
        output_dir - where to write the verification files
        verify_uuid - UUID for the verification files
        download_url - link to the pdf download (for the verifcation page)"""

        # Do not do anything if there isn't any GPG Key to sign with
        if not CERT_KEY_ID:
            return

        prefix = ''
        if self.template_version == 2:
            prefix = 'v2/'
        valid_template = prefix + 'valid.html'
        verify_template = prefix + 'verify.html'

        # generate signature
        signature_filename = os.path.basename(filename) + ".sig"
        signature_filename = os.path.join(output_dir, verify_uuid, signature_filename)
        self._ensure_dir(signature_filename)
        gpg = gnupg.GPG(homedir=settings.CERT_GPG_DIR)
        gpg.encoding = 'utf-8'
        with open(filename) as f:
            signed_data = gpg.sign(data=f, default_key=CERT_KEY_ID, clearsign=False, detach=True, passphrase=Settings.CERT_KEY_PASSPHRASE).data
        with open(signature_filename, 'w') as f:
            f.write(signed_data.encode('utf-8'))

        # create the validation page
        signature_download_url = "{verify_url}/{verify_path}/{verify_uuid}/{verify_filename}".format(
            verify_url=settings.CERT_VERIFY_URL,
            verify_path=S3_VERIFY_PATH,
            verify_uuid=verify_uuid,
            verify_filename=os.path.basename(signature_filename))

        verify_page_url = "{verify_url}/{verify_path}/{verify_uuid}/verify.html".format(
            verify_url=settings.CERT_VERIFY_URL,
            verify_path=S3_VERIFY_PATH,
            verify_uuid=verify_uuid)

        type_map = {
            'verified': {'type': 'idverified', 'type_name': 'Verified'},
            'honor': {'type': 'honorcode', 'type_name': 'Honor Code'},
        }

        type_map['verified']['explanation'] = (
            "An ID verified certificate signifies that an edX user has "
            "agreed to abide by edX's honor code and completed all of the "
            "required tasks of this course under its guidelines, as well "
            "as having their photo ID checked to verify their identity."
        )
        type_map['verified']['img'] = '''
            <div class="wrapper--img">
                <img
                    class="img--idverified"
                    src="/v2/static/images/logo-idverified.png"
                    alt="ID Verified Certificate Logo"
                />
            </div>
        '''
        type_map['honor']['explanation'] = (
            "An honor code certificate signifies that an edX user has "
            "agreed to abide by edX's honor code and completed all of the "
            "required tasks of this course under its guidelines."
        )
        type_map['honor']['img'] = ""

        with open("{0}/{1}".format(TEMPLATE_DIR, valid_template)) as f:
            valid_page = f.read().decode('utf-8')
        valid_page = valid_page.format(
            COURSE=self.course.decode('utf-8'),
            COURSE_LONG=self.long_course.decode('utf-8'),
            ORG=self.org.decode('utf-8'),
            ORG_LONG=self.long_org.decode('utf-8'),
            NAME=name.decode('utf-8'),
            CERTIFICATE_ID=verify_uuid,
            SIGNATURE=signed_data,
            SIG_URL=signature_download_url,
            VERIFY_URL=verify_page_url,
            TYPE=type_map[self.template_type]['type'],
            TYPE_NAME=type_map[self.template_type]['type_name'],
            ISSUE_DATE=self.issued_date,
            IMG=type_map[self.template_type]['img'],
            CERTS_ARE_CALLED=CERTS_ARE_CALLED.title(),
            CERTS_ARE_CALLED_PLURAL=CERTS_ARE_CALLED_PLURAL.title(),
            EXPLANATION=type_map[self.template_type]['explanation'],
        )

        with open(os.path.join(
                output_dir, verify_uuid, "valid.html"), 'w') as f:
            f.write(valid_page.encode('utf-8'))

        with open("{0}/{1}".format(TEMPLATE_DIR, verify_template)) as f:
            verify_page = f.read().decode('utf-8').format(
                NAME=name.decode('utf-8'),
                SIG_URL=signature_download_url,
                SIG_FILE=os.path.basename(signature_download_url),
                CERT_KEY_ID=CERT_KEY_ID,
                CERTS_ARE_CALLED=CERTS_ARE_CALLED.title(),
                CERTS_ARE_CALLED_PLURAL=CERTS_ARE_CALLED_PLURAL.title(),
                VERIFY_URL=verify_page_url,
                PDF_FILE=os.path.basename(download_url)
            )

        with open(os.path.join(
                output_dir, verify_uuid, "verify.html"), 'w') as f:
            f.write(verify_page.encode('utf-8'))

    def _ensure_dir(self, f):
        d = os.path.dirname(f)
        if not os.path.exists(d):
            os.makedirs(d)

    def _contains_characters_above(self, string, value):
        """
        Crude method for determining whether or not a string contains
        characters we can't render nicely in particular fonts

        FIXME: methods using this should consider using font_for_string()
        instead.
        """
        for character in string.decode('utf-8'):
            # I believe chinese characters are 0x4e00 to 0x9fff
            # Japanese kanji seem to be >= 0x3000
            if ord(character) >= value:
                return True
        return False

    def _use_non_latin(self, string):
        """
        Use this to detect when we are dealing with characters that
        do not fit into Latin-1
        """
        return self._contains_characters_above(string, 0x0100)

    def _use_unicode_font(self, string):
        """
        FIXME: methods using this should consider using font_for_string()
        instead.
        """
        # This function should return true for any
        # string that that opensans/baskerville can't render.
        # I don't know how to query the font, so I assume that
        # any high codepoint is unsupported.
        # This can be improved dramatically
        # I believe chinese characters are 0x4e00 to 0x9fff
        # Japanese kanji seem to be >= 0x3000
        return self._contains_characters_above(string, 0x0500)

    def _generate_stanford_SOA(
        self,
        student_name,
        download_dir,
        verify_dir,
        filename=TARGET_FILENAME,
        grade=None,
        designation=None,
        generate_date=None,
    ):
        """Generate a PDF certificate, signature and html files for validation.

        REQUIRED PARAMETERS:
        student_name  - specifies student name as it must appear on the cert.
        download_dir  -
        verify_dir    -

        OPTIONAL PARAMETERS:
        filename      - the filename to write out, i.e., 'Certificate.pdf'.
                        Defaults to settings.TARGET_FILENAME
        grade         - the grade received by the student. Defaults to 'Pass'
        generate_date - specifies an ISO formatted date (i.e., '2012-02-02')
                        with which to stamp the cert. Defaults to CERT_DATA's
                        ISSUED_DATE, or today's date for ROLLING.

        CONFIGURATION PARAMETERS:
            The following items are brought in from the cert-data.yml stanza for the
        current course:
        LONG_COURSE  - (optional) The course title to be printed on the cert;
                       unset means to use the value passed in as part of the
                       certificate request.
        ISSUED_DATE  - (optional) If given, the date string which should be
                       stamped onto each and every certificate. The value
                       ROLLING is equivalent to leaving ISSUED_DATE unset, which
                       stamps the certificates with the current date.
        TEMPLATEFILE - (optional) If given, the filename referred to by
                       TEMPLATEFILE will be used as the template over which
                       to render.

        RETURNS (download_uuid, verify_uuid, download_url)
        """

        verify_me_p = self.cert_data.get('VERIFY', True)
        verify_uuid = uuid.uuid4().hex if verify_me_p else ''
        download_uuid = uuid.uuid4().hex
        download_url = "{base_url}/{cert}/{uuid}/{file}".format(
            base_url=settings.CERT_DOWNLOAD_URL,
            cert=S3_CERT_PATH, uuid=download_uuid, file=filename)

        filename = os.path.join(download_dir, download_uuid, filename)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        c = canvas.Canvas(overlay_pdf_buffer, pagesize=landscape(A4))

        # 0 0 - normal
        # 0 1 - italic
        # 1 0 - bold
        # 1 1 - italic and bold
        addMapping('OpenSans-Light', 0, 0, 'OpenSans-Light')
        addMapping('OpenSans-Regular', 1, 0, 'OpenSans-Bold')
        addMapping('SourceSansPro-Light', 0, 0, 'SourceSansPro-Light')
        addMapping('SourceSansPro-Light', 1, 1, 'SourceSansPro-SemiboldItalic')
        addMapping('SourceSansPro-Regular', 0, 0, 'SourceSansPro-Regular')

        styleArial = ParagraphStyle(
            name="arial",
            leading=10,
            fontName='Arial Unicode',
        )
        styleOpenSansLight = ParagraphStyle(
            name="opensans-light",
            leading=10,
            fontName='OpenSans-Light',
        )
        styleSourceSansPro = ParagraphStyle(
            name="sourcesans-regular",
            leading=10,
            fontName='SourceSansPro-Regular',
        )
        styleSourceSansProLight = ParagraphStyle(
            name="sourcesans-light",
            leading=10,
            fontName='SourceSansPro-Light',
        )

        # Text is overlayed top to bottom
        #   * Issued date (top right corner)
        #   * "This is to certify that"
        #   * Student's name
        #   * "successfully completed"
        #   * Course name
        #   * "a course of study.."
        #   * honor code url at the bottom
        WIDTH, HEIGHT = landscape(A4)
        standardgray = colors.Color(0.302, 0.306, 0.318)

        LEFT_INDENT = 55  # mm from the left side
        DATE_INDENT = 45  # mm from the right side for Date

        # Issued ..
        style = styleSourceSansProLight
        style.fontSize = 12
        style.textColor = standardgray
        style.alignment = TA_LEFT

        paragraph_string = get_cert_date(generate_date, self.issued_date)

        # Right justified so we compute the width
        width = stringWidth(paragraph_string, 'SourceSansPro-SemiboldItalic', style.fontSize) / mm
        paragraph = Paragraph("<i><b>{0}</b></i>".format(paragraph_string), style)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, (WIDTH - DATE_INDENT - width) * mm, 159 * mm)

        # Certify That
        styleSourceSansPro.fontSize = 14
        styleSourceSansPro.textColor = standardgray
        styleSourceSansPro.alignment = TA_LEFT

        paragraph_string = "This is to certify that,"

        paragraph = Paragraph(paragraph_string, styleSourceSansPro)

        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 135 * mm)

        #  Student name
        # default is to use the DejaVu font for the name, will fall back
        # to Arial if there are unusual characters
        style = styleOpenSansLight
        style.fontSize = 34
        width = stringWidth(student_name.decode('utf-8'), 'OpenSans-Bold', style.fontSize) / mm
        paragraph_string = "<b>{0}</b>".format(student_name)

        if self._use_unicode_font(student_name):
            style = styleArial
            width = stringWidth(student_name.decode('utf-8'), 'Arial Unicode', style.fontSize) / mm
            # There is no bold styling for Arial :(
            paragraph_string = "{0}".format(student_name)

        # We will wrap at 200mm in, so if we reach the end (200-47)
        # decrease the font size
        if width > 153:
            style.fontSize = 18
            nameYOffset = 121.5
        else:
            style.fontSize = 34
            nameYOffset = 124.5

        style.textColor = standardgray
        style.alignment = TA_LEFT

        paragraph = Paragraph(paragraph_string, style)
        paragraph.wrapOn(c, 200 * mm, 214 * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, nameYOffset * mm)

        # Successfully completed
        paragraph_string_interstitial = ' '
        successfully_completed = "has successfully completed{0}a free online offering of"

        # Add distinction here
        if grade:
            tmp = self.interstitial_texts.get(grade, paragraph_string_interstitial)
            if tmp != paragraph_string_interstitial:
                tmp = ' <b>' + tmp + '</b> '
            paragraph_string_interstitial = tmp
        paragraph_string = successfully_completed.format(paragraph_string_interstitial)

        paragraph = Paragraph(paragraph_string, styleSourceSansPro)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 104.5 * mm)

        # Honor code
        if verify_me_p:
            styleSourceSansPro.fontSize = 9
            styleSourceSansPro.alignment = TA_CENTER
            paragraph_string = (
                "Authenticity of this {cert_label} can be verified at "
                "<a href='{verify_url}/{verify_path}/{verify_uuid}'>"
                "<b>{verify_url}/{verify_path}/{verify_uuid}</b></a>"
            )
            paragraph_string = paragraph_string.format(
                cert_label=self.cert_label_singular,
                verify_url=settings.CERT_VERIFY_URL,
                verify_path=S3_VERIFY_PATH,
                verify_uuid=verify_uuid,
            )
            paragraph = Paragraph(paragraph_string, styleSourceSansPro)
            paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
            # paragraph.drawOn(c, 0 * mm, 31 * mm)
            paragraph.drawOn(c, -275 * mm, 31 * mm)

        c.showPage()
        c.save()

        # Merge the overlay with the template, then write it to file
        output = PdfFileWriter()
        overlay = PdfFileReader(overlay_pdf_buffer)

        # We render the final certificate by merging several rendered pages.
        # It is fastest if the bottom layer is blank and loaded from memory
        final_certificate = copy.copy(BLANK_PDFS['landscape-A4']).getPage(0)
        final_certificate.mergePage(self.template_pdf.getPage(0))
        final_certificate.mergePage(overlay.getPage(0))

        output.addPage(final_certificate)

        self._ensure_dir(filename)

        outputStream = file(filename, "wb")
        output.write(outputStream)
        outputStream.close()

        if verify_me_p:
            self._generate_verification_page(
                student_name,
                filename,
                verify_dir,
                verify_uuid,
                download_url
            )

        return (download_uuid, verify_uuid, download_url)

    def _generate_stanford_cme_certificate(
        self,
        student_name,
        download_dir,
        verify_dir,
        filename=TARGET_FILENAME,
        grade=None,
        designation=None,
        generate_date=None,
    ):
        """Generate a PDF certificate, signature and html files for validation.

        REQUIRED PARAMETERS:
        student_name  - specifies student name as it must appear on the cert.
        download_dir  -
        verify_dir    -

        OPTIONAL PARAMETERS:
        filename      - the filename to write out, i.e., 'Certificate.pdf'.
                        Defaults to settings.TARGET_FILENAME
        grade         - the grade received by the student. Defaults to 'Pass'
        generate_date - specifies an ISO formatted date (i.e., '2012-02-02')
                        with which to stamp the cert. Defaults to CERT_DATA's
                        ISSUED_DATE, or today's date for ROLLING.

        CONFIGURATION PARAMETERS:
        The following items are brought in from the cert-data.yml stanza for the
        current course:
        MD_CERTS     - A list of all of the student titles which qualify to get the
                       MD/DO certificate and receive CME credit
        NO_TITLE     - A list of student titles which should be treated as
                       equivalent to having no title at all.
        CREDITS      - A string describing what accreditation this CME
                       certificate is good for, e.g., "## Blabbity Blab Credits".
        LONG_COURSE  - (optional) The course title to be printed on the cert;
                       unset means to use the value passed in as part of the
                       certificate request.
        ISSUED_DATE  - (optional) If given, the date string which should be
                       stamped onto each and every certificate. The value
                       ROLLING is equivalent to leaving ISSUED_DATE unset, which
                       stamps the certificates with the current date.
        TEMPLATEFILE - (optional) If given, the filename referred to by
                       TEMPLATEFILE will be used as the template over which
                       to render.

        RETURNS (download_uuid, verify_uuid, download_url)

        Note that CME certificates never generate verification URLs; the
        underlying template is expected to embed contact information for
        the relevant medical school.
        """

        # Landscape Letter page size is 279mm x 216 mm
        # All unexplained constants below were selected because they look good
        WIDTH, HEIGHT = landscape(letter)   # values in points, multiply by mm

        download_uuid = uuid.uuid4().hex
        download_url = "{base_url}/{cert}/{uuid}/{file}".format(
            base_url=settings.CERT_DOWNLOAD_URL,
            cert=S3_CERT_PATH,
            uuid=download_uuid,
            file=filename,
        )
        filename = os.path.join(download_dir, download_uuid, filename)
        self._ensure_dir(filename)

        # Manipulate student titles
        gets_md_cert = False
        gets_md_cert_list = self.cert_data.get('MD_CERTS', [])
        gets_no_title = self.cert_data.get('NO_TITLE', [])
        student_name = u"{}".format(student_name.decode('utf-8'))  # Ensure consistent handling
        if designation and designation not in gets_no_title:
            student_name = u"{}, {}".format(student_name, designation.decode('utf-8'))
        gets_md_cert = designation in gets_md_cert_list

        #                            0 0 - normal
        #                            0 1 - italic
        #                            1 0 - bold
        #                            1 1 - italic and bold
        addMapping('OpenSans-Light', 0, 0, 'OpenSans-Light')
        addMapping('OpenSans-Light', 0, 1, 'OpenSans-LightItalic')
        addMapping('OpenSans-Light', 1, 0, 'OpenSans-Bold')
        addMapping('DroidSerif', 0, 0, 'DroidSerif')
        addMapping('DroidSerif', 0, 1, 'DroidSerif-Italic')
        addMapping('DroidSerif', 1, 0, 'DroidSerif-Bold')
        addMapping('DroidSerif', 1, 1, 'DroidSerif-BoldItalic')

        styleArial = ParagraphStyle(name="arial", leading=10, fontName='Arial Unicode', allowWidows=0)
        styleOpenSansLight = ParagraphStyle(name="opensans-light", leading=10, fontName='OpenSans-Light', allowWidows=0)
        styleDroidSerif = ParagraphStyle(name="droidserif", leading=10, fontName='DroidSerif', allowWidows=0)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        c = canvas.Canvas(overlay_pdf_buffer, pagesize=landscape(letter))

        def draw_centered_text(text, style, height):
            """Draw text in style, centered at height mm above origin"""
            paragraph = Paragraph(text, style)
            # wrap sets the Flowable bounding box. Necessary voodoo.
            paragraph.wrap(WIDTH, HEIGHT)
            paragraph.drawOn(c, 0, height * mm)

        # Text is then overlayed onto it. From top to bottom:
        #   * Student's name
        #   * Course name
        #   * Issued date (top right corner)
        #   * "is awarded/was designated.."
        #   * MD/DO;AHP corner marker

        # Student name

        # These are ordered by preference; cf. font_for_string() above
        fontlist = [
            ('DroidSerif', 'DroidSerif.ttf', styleDroidSerif),
            ('OpenSans-Light', 'OpenSans-Light.ttf', styleOpenSansLight),
            ('Arial Unicode', 'Ariel Unicode.ttf', styleArial),
        ]

        (fonttag, fontfile, style) = font_for_string(fontlist, student_name)
        style.alignment = TA_CENTER
        width = 9999             # Fencepost width is way too wide
        nameYOffset = 146        # by eye, looks good for 34 pt font
        fontsize = 36            # good default giant text size: 1/2"
        indent = 0               # initialize while loop
        max_width = 0.8 * WIDTH  # Keep scaling until <= 80% of page

        while width > max_width:
            style.fontSize = fontsize
            width = stringWidth(student_name, fonttag, fontsize)
            if nameYOffset > 140:
                nameYOffset = nameYOffset - math.floor((36 - fontsize) / 12)
            fontsize -= 1

        draw_centered_text(u"<b>{0}</b>".format(student_name), style, nameYOffset)

        # Enduring material titled
        style = styleDroidSerif
        style.alignment = TA_CENTER
        style.fontSize = 28
        draw_centered_text(u"<b>{0}</b>".format(self.long_course.decode('utf-8')), style, 119)

        # Issued on date...
        style.fontSize = 26
        paragraph_string = get_cert_date(generate_date, self.issued_date)
        draw_centered_text(u"<b>{0}</b>".format(paragraph_string), style, 95)

        # Credits statement
        # This is pretty fundamentally not internationalizable; like the rest of the certificate template renderers
        # we do text interpolation that assumes English subject/object relationships. If this language needs to be
        # varied, the best place to do that is probably a forked rendering method. There is some additional
        # information in the documentation.
        style.fontSize = 18
        credit_info = self.cert_data.get('CREDITS', '')
        if credit_info:
            if gets_md_cert:
                paragraph_string = u"and is awarded {credit_info}".format(
                    credit_info=credit_info.decode('utf-8'),
                )
            else:
                paragraph_string = u"The activity was designated for {credit_info}".format(
                    credit_info=credit_info.decode('utf-8'),
                )
            draw_centered_text(paragraph_string, style, 80)

        # MD/DO vs AHP tags
        style.fontSize = 8
        style.alignment = TA_LEFT
        if gets_md_cert:
            paragraph_string = "MD/DO"
        else:
            paragraph_string = "AHP"
        indent = WIDTH - 72         # One inch in from right edge
        paragraph = Paragraph(paragraph_string, style)
        paragraph.wrap(WIDTH, HEIGHT)
        paragraph.drawOn(c, indent, 14.9 * mm)

        c.showPage()
        c.save()

        # Merge the overlay with the template, then write it to file
        overlay = PdfFileReader(overlay_pdf_buffer)

        # We render the final certificate by merging several rendered pages.
        # It's fastest if the bottom layer is a blank page loaded from RAM
        final_certificate = copy.copy(BLANK_PDFS['landscape-letter']).getPage(0)
        final_certificate.mergePage(self.template_pdf.getPage(0))
        final_certificate.mergePage(overlay.getPage(0))

        output = PdfFileWriter()
        output.addPage(final_certificate)
        with file(filename, "wb") as ostream:
            output.write(ostream)

        return (download_uuid, 'No Verification', download_url)

    def _generate_v3_dynamic_certificate(
        self,
        student_name,
        download_dir,
        verify_dir,
        filename=TARGET_FILENAME,
        grade=None,
        designation=None,
        generate_date=None,
    ):
        """Generate a PDF certificate, signature and html files for validation.

        REQUIRED PARAMETERS:
        student_name  - specifies student name as it must appear on the cert.
        download_dir  -
        verify_dir    -

        OPTIONAL PARAMETERS:
        filename      - the filename to write out, e.g., 'Statement.pdf'.
                        Defaults to settings.TARGET_FILENAME.
        grade         - the grade received by the student. Defaults to 'Pass'
        generate_date - specifies an ISO formatted date (i.e., '2012-02-02')
                        with which to stamp the cert. Defaults to CERT_DATA's
                        ISSUED_DATE, or today's date for ROLLING.

        CONFIGURATION PARAMETERS:
            The following items are brought in from the cert-data.yml stanza for the
        current course:
        LONG_COURSE    - (optional) The course title to be printed on the cert;
                         unset means to use the value passed in as part of the
                         certificate request.
        ISSUED_DATE    - (optional) If given, the date string which should be
                         stamped onto each and every certificate. The value
                         ROLLING is equivalent to leaving ISSUED_DATE unset, which
                         stamps the certificates with the current date.
        HAS_DISCLAIMER - (optional) If given, the programmatic disclaimer that
                         is usually rendered at the bottom of the page, is not.
        TEMPLATEFILE   - (optional) If given, the filename referred to by
                         TEMPLATEFILE will be used as the template over which
                         to render.

        RETURNS (download_uuid, verify_uuid, download_url)
        """

        verify_me_p = self.cert_data.get('VERIFY', True)
        verify_uuid = uuid.uuid4().hex if verify_me_p else ''
        download_uuid = uuid.uuid4().hex
        download_url = "{base_url}/{cert}/{uuid}/{file}".format(
            base_url=settings.CERT_DOWNLOAD_URL,
            cert=S3_CERT_PATH,
            uuid=download_uuid,
            file=filename,
        )

        filename = os.path.join(download_dir, download_uuid, filename)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        PAGE = canvas.Canvas(overlay_pdf_buffer, pagesize=landscape(A4))

        WIDTH, HEIGHT = landscape(A4)  # Width and Height of landscape canvas (in points)
        MAX_GEN_WIDTH = WIDTH * .5  # Width to which to constrain text block
        MAX_FULL_WIDTH = WIDTH * .72  # Width to which to constrian full page text blocks
        GUTTER_WIDTH = 120  # Space from the left and right sides (in points)
        GUTTER_WIDTH = 120  # Space from the right side for Date (in points)
        DATE_INDENT_TOP = 112  # Space from top for Date (in points)
        STANDARD_GRAY = colors.Color(0.13, 0.14, 0.22)  # Main dark gray text color
        CARDINAL_RED = colors.Color(.55, .08, .08)  # Special red color for course title

        # 0 0 - normal
        # 0 1 - italic
        # 1 0 - bold
        # 1 1 - italic and bold
        addMapping('OpenSans-Light', 0, 0, 'OpenSans-Light')
        addMapping('OpenSans-Light', 1, 0, 'OpenSans-Bold')
        addMapping('SourceSansPro-Regular', 0, 0, 'SourceSansPro-Regular')
        addMapping('SourceSansPro-Regular', 1, 0, 'SourceSansPro-Bold')
        addMapping('SourceSansPro-Regular', 1, 1, 'SourceSansPro-BoldItalic')

        styleArial = ParagraphStyle(name="arial", fontName='Arial Unicode')
        styleOpenSansLight = ParagraphStyle(name="opensans-light", fontName='OpenSans-Light')
        styleSourceSansPro = ParagraphStyle(name="sourcesans-regular", fontName='SourceSansPro-Regular')

        style_date_text = ParagraphStyle(
            name="date-text",
            fontSize=12,
            leading=14,
            textColor=STANDARD_GRAY,
            alignment=TA_RIGHT,
        )
        style_big_name_text = ParagraphStyle(
            name="big-name-text",
            textColor=STANDARD_GRAY,
            alignment=TA_LEFT,
        )
        style_standard_text = ParagraphStyle(
            name="standard-text",
            fontSize=14,
            leading=18,
            textColor=STANDARD_GRAY,
            alignment=TA_LEFT,
        )
        style_big_course_text = ParagraphStyle(
            name="big-course-text",
            textColor=CARDINAL_RED,
            alignment=TA_LEFT,
        )
        style_small_text = ParagraphStyle(
            name="small-text",
            fontSize=7.5,
            leading=10,
            textColor=STANDARD_GRAY,
            alignment=TA_LEFT,
        )

        # These are ordered by preference; cf. font_for_string() above
        fontlist = [
            ('SourceSansPro-Regular', 'SourceSansPro-Regular.ttf', None),
            ('OpenSans-Light', 'OpenSans-Light.ttf', None),
            ('Arial Unicode', 'Arial Unicode.ttf', None),
        ]

        def fontlist_with_style(a_style):
            """ assign 'a_style' to each font in 'fontlist' """
            new_fontlist = []
            for styletag, a_file, dummy_0 in fontlist:
                new_style = copy.copy(a_style)
                new_style.fontName = styletag
                new_fontlist.append((styletag, a_file, new_style))
            return new_fontlist

        # Text is overlayed top to bottom with one exception
        #   * Issued date (top right)
        #   * Student's name (scaled to fit and centered vertically)
        #   * "has successfully completed an online offering of"
        #   * Course Title (scaled to fit and centered vertically)
        #   * optional "with *Distinction*." or some other level with optional description
        #   * honor code url at the bottom

        # SECTION: Issued Date
        date_string = u"{0}".format(get_cert_date(generate_date, self.issued_date))

        (fonttag, fontfile, date_style) = font_for_string(fontlist_with_style(style_date_text), date_string)
        max_width = 125
        max_height = date_style.fontSize

        paragraph = Paragraph(date_string, date_style)
        width, height = paragraph.wrapOn(PAGE, max_width, max_height)

        # positioning paragraph wrapping box from its bottom left corner
        # calculating positioning for top right corner of page
        paragraph.drawOn(PAGE, (WIDTH - GUTTER_WIDTH - max_width), (HEIGHT - DATE_INDENT_TOP))

        # SECTION: Student name
        student_name_string = u"<b>{0}</b>".format(student_name.decode('utf-8'))
        (fonttag, fontfile, name_style) = font_for_string(fontlist_with_style(style_big_name_text), student_name_string)

        maxFontSize = 42      # good default name text size (in points)
        max_leading = maxFontSize * 1.2
        max_height = maxFontSize * 1.2
        max_width = MAX_GEN_WIDTH
        minYOffset = 415     # distance from bottom of page (in points)

        paragraph = autoscale_text(
            PAGE,
            student_name_string,
            maxFontSize,
            max_leading,
            max_height,
            max_width,
            name_style
        )
        width, height = paragraph.wrapOn(PAGE, max_width, max_height)

        yOffset = minYOffset + ((max_height - height) / 2)
        paragraph.drawOn(PAGE, GUTTER_WIDTH - (name_style.fontSize / 12), yOffset)

        # SECTION: Successfully completed
        successfully_completed = u"has successfully completed a free online offering of"
        (fonttag, fontfile, completed_style) = font_for_string(
            fontlist_with_style(style_standard_text),
            successfully_completed,
        )

        max_height = completed_style.leading
        max_width = MAX_GEN_WIDTH
        yOffset = 390     # distance from bottom of page (in points)

        paragraph = Paragraph(successfully_completed, completed_style)
        width, height = paragraph.wrapOn(PAGE, max_width, max_height)

        paragraph.drawOn(PAGE, GUTTER_WIDTH, yOffset)

        # SECTION: Course Title
        course_name_string = self.long_course.decode('utf-8')
        course_title = u"<b>{0}</b>".format(course_name_string)

        (fonttag, fontfile, course_style) = font_for_string(fontlist_with_style(style_big_course_text), course_title)

        maxFontSize = 36      # good default name text size (in points)
        max_leading = maxFontSize * 1.1
        max_height = maxFontSize * 2.1
        max_width = MAX_GEN_WIDTH
        minYOffset = 305     # distance from bottom of page (in points)

        paragraph = autoscale_text(PAGE, course_title, maxFontSize, max_leading, max_height, max_width, course_style)
        width, height = paragraph.wrapOn(PAGE, max_width, max_height)

        yOffset = minYOffset + ((max_height - height) / 2) + (course_style.fontSize / 5)

        paragraph.drawOn(PAGE, GUTTER_WIDTH, yOffset)

        # SECTION: Extra achievements
        achievements_string = ""
        achievements_description_string = self.interstitial_texts[grade]
        if grade and grade.lower() != 'pass':
            achievements_string = "with <b>{0}</b>.<br /><br />".format(grade)
        achievements_paragraph = u"{0}{1}".format(achievements_string, achievements_description_string)

        (fonttag, fontfile, achievements_style) = font_for_string(
            fontlist_with_style(style_standard_text),
            achievements_paragraph,
        )

        max_height = achievements_style.leading * 9  # allow for up to 9 lines of text
        max_width = MAX_GEN_WIDTH
        minYOffset = 135  # distance from bottom of page (in points)

        paragraph = Paragraph(achievements_paragraph, achievements_style)
        width, height = paragraph.wrapOn(PAGE, max_width, max_height)

        yOffset = minYOffset + (max_height - height)

        paragraph.drawOn(PAGE, GUTTER_WIDTH, yOffset)

        # SECTION: disclaimer text
        print_disclaimer = not self.cert_data.get('HAS_DISCLAIMER', False)
        disclaimer_text = getattr(settings, 'CERTS_SITE_DISCLAIMER_TEXT', '')
        if print_disclaimer and disclaimer_text:
            (fonttag, fontfile, disclaimer_style) = font_for_string(
                fontlist_with_style(style_small_text),
                disclaimer_text,
            )

            max_height = disclaimer_style.leading * 3  # allow for up to 9 lines of text
            max_width = MAX_FULL_WIDTH
            yOffset = 89  # distance from bottom of page (in points)

            paragraph = Paragraph(disclaimer_text, disclaimer_style)
            width, height = paragraph.wrapOn(PAGE, max_width, max_height)

            paragraph.drawOn(PAGE, GUTTER_WIDTH, yOffset)

        # SECTION: Honor code
        if verify_me_p:
            paragraph_string = u"Authenticity of this {cert_label} can be verified at " \
                u"<a href='{verify_url}/{verify_path}/{verify_uuid}'>" \
                u"<b>{verify_url}/{verify_path}/{verify_uuid}</b></a>"

            paragraph_string = paragraph_string.format(
                cert_label=self.cert_label_singular,
                verify_url=settings.CERT_VERIFY_URL,
                verify_path=S3_VERIFY_PATH,
                verify_uuid=verify_uuid,
            )

            (fonttag, fontfile, honor_style) = font_for_string(
                fontlist_with_style(style_small_text),
                achievements_paragraph,
            )

            max_height = 10
            max_width = MAX_FULL_WIDTH

            paragraph = Paragraph(paragraph_string, honor_style)
            paragraph.wrapOn(PAGE, max_width, max_height)
            paragraph.drawOn(PAGE, GUTTER_WIDTH, 70)

        # Render Page
        PAGE.showPage()
        PAGE.save()

        # Merge the overlay with the template, then write it to file
        output = PdfFileWriter()
        overlay = PdfFileReader(overlay_pdf_buffer)

        # We render the final certificate by merging several rendered pages.
        # It is fastest if the bottom layer is blank and loaded from memory
        final_certificate = copy.copy(BLANK_PDFS['landscape-A4']).getPage(0)
        final_certificate.mergePage(self.template_pdf.getPage(0))
        final_certificate.mergePage(overlay.getPage(0))

        output.addPage(final_certificate)

        self._ensure_dir(filename)

        outputStream = file(filename, "wb")
        output.write(outputStream)
        outputStream.close()

        # have to create the verification page seperately from the above
        # conditional because filename must have already been written.
        if verify_me_p:
            self._generate_verification_page(
                student_name,
                filename,
                verify_dir,
                verify_uuid,
                download_url,
            )

        return (download_uuid, verify_uuid, download_url)
