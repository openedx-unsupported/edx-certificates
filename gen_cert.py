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
from reportlab.lib import utils
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4, letter, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from HTMLParser import HTMLParser
from babel.dates import format_datetime
from babel.dates import get_timezone

import settings
import collections
import itertools
import logging
import reportlab.rl_config
import tempfile
import boto.s3
from boto.s3.key import Key
from bidi.algorithm import get_display

from opaque_keys.edx.keys import CourseKey

from openedx_certificates.renderers.elements import draw_flair
from openedx_certificates.renderers.elements import draw_template_element
from openedx_certificates.renderers.util import apply_style_to_font_list
from openedx_certificates.renderers.util import autoscale_text
from openedx_certificates.renderers.util import font_for_string
from openedx_certificates.renderers.util import WIDTH_LANDSCAPE_PAGE_IN_POINTS

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

# These are small, so let's just load them at import time and keep them around
# so we don't have to keep doing the file I/o
BLANK_PDFS = {
    'landscape-A4': PdfFileReader(file("{0}/blank.pdf".format(TEMPLATE_DIR), "rb")),
    'landscape-letter': PdfFileReader(file("{0}/blank-letter.pdf".format(TEMPLATE_DIR), "rb")),
    'portrait-A4': PdfFileReader(file("{0}/blank-portrait-A4.pdf".format(TEMPLATE_DIR), "rb")),
}


def get_cert_date(
        calling_date_parameter,
        configured_date_parameter,
        locale=settings.DEFAULT_LOCALE,
        timezone=settings.TIMEZONE,
):
    """Get pertinent date for display on cert

    - If cert passes a set date in 'calling_date_parameter', format that
    - If using the "ROLLING" certs feature, use today's date
    - If all else fails use 'configured_date_parameter' for date
    """

    if calling_date_parameter:
        date_value = format_datetime(calling_date_parameter, 'MMMM d, y', tzinfo=timezone, locale=locale)
    elif configured_date_parameter == "ROLLING":
        date_value = format_datetime(datetime.datetime.today(), 'MMMM d, y', tzinfo=timezone, locale=locale)
    else:
        date_value = format_datetime(configured_date_parameter, 'MMMM d, y', tzinfo=timezone, locale=locale)

    date_string = u"{0}".format(date_value)

    return date_string


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
            if not os.path.exists(TMP_GEN_DIR):
                os.makedirs(TMP_GEN_DIR)
            dir_prefix = tempfile.mkdtemp(prefix=TMP_GEN_DIR)
        self._ensure_dir(dir_prefix)
        self.dir_prefix = dir_prefix

        self.aws_id = str(aws_id)
        self.aws_key = str(aws_key)

        cert_data = settings.CERT_DATA.get(course_id, {})
        self.cert_data = cert_data

        def interstitial_factory():
            """ Generate default values for interstitial_texts defaultdict """
            return itertools.repeat(cert_data.get('interstitial', {}).get('Pass', u'').encode('utf-8')).next

        # lookup long names from the course_id
        try:
            self.long_org = long_org or cert_data.get('LONG_ORG', '').encode('utf-8') or settings.DEFAULT_ORG
            self.long_course = cert_data.get('LONG_COURSE', '').encode('utf-8') or long_course or ''
            self.issued_date = issued_date or cert_data.get('ISSUED_DATE', '').encode('utf-8') or 'ROLLING'
            self.interstitial_texts = collections.defaultdict(interstitial_factory())
            interstitial_dict = {
                key.encode('utf8'): value.encode('utf8')
                for key, value in cert_data.get('interstitial', {}).items()
            }
            self.interstitial_texts.update(interstitial_dict)
            self.timezone = cert_data.get('timezone', settings.TIMEZONE)
            self.locale = cert_data.get('locale', settings.DEFAULT_LOCALE).encode('utf-8')
            self.course_translations = cert_data.get('translations', {})
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
        self.template_version = cert_data.get('VERSION', '4_programmatic')
        self.template_type = 'honor'
        # Check for font definition in course yaml, default to 'OpenSans' and 'Light'
        self.template_font = cert_data.get('font', {})
        self.template_font_name = self.template_font.get('name', 'OpenSans')
        self.template_font_type = self.template_font.get('type', 'Light')
        # search for certain keywords in the file name, we'll probably want to
        # be better at parsing this later
        # If TEMPLATEFILE is set in cert-data.yml, this value has top priority.
        # Else if a value is passed in to the constructor (eg, from xqueue), it is used,
        # Else, the filename is calculated from the version and course_id.
        template_pdf = cert_data.get('TEMPLATEFILE', template_pdf)

        # template_pdf is allowed to be None and '' so we key exactly on False here
        if template_pdf is not False:
            template_prefix = "{template_dir}/v{template_version}-cert-templates".format(
                template_dir=TEMPLATE_DIR,
                template_version=self.template_version,
            )
            template_pdf_filename = "{template_prefix}/certificate-template-{org}-{course}.pdf".format(
                template_prefix=template_prefix,
                org=self.org,
                course=self.course,
            )
            if template_pdf:
                template_pdf_filename = "{template_prefix}/{template_pdf}".format(
                    template_prefix=template_prefix,
                    template_pdf=template_pdf,
                )
                if 'verified' in template_pdf:
                    self.template_type = 'verified'
            try:
                self.template_pdf = PdfFileReader(file(template_pdf_filename, 'rb'))
            except IOError as e:
                log.critical("I/O error (%s): %s opening %s", e.errno, e.strerror, template_pdf_filename)
                raise

        self.cert_label_singular = cert_data.get('CERTS_ARE_CALLED', CERTS_ARE_CALLED)
        self.cert_label_plural = cert_data.get('CERTS_ARE_CALLED_PLURAL', CERTS_ARE_CALLED_PLURAL)
        self.course_association_text = cert_data.get('COURSE_ASSOCIATION_TEXT', 'a course of study')

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
        if upload:
            s3_conn = boto.connect_s3(settings.CERT_AWS_ID, settings.CERT_AWS_KEY)
            bucket = s3_conn.get_bucket(BUCKET)
        if upload or copy_to_webroot:
            for subtree in (my_certs_path, my_verify_path):
                for dirpath, dirnames, filenames in os.walk(subtree):
                    for filename in filenames:
                        local_path = os.path.join(dirpath, filename)
                        dest_path = os.path.relpath(local_path, start=self.dir_prefix)
                        publish_dest = os.path.join(cert_web_root, dest_path)

                        if upload:
                            key = Key(bucket, name=dest_path)
                            key.set_contents_from_filename(local_path, policy='public-read')
                            log.info("uploaded {local} to {s3path}".format(local=local_path, s3path=dest_path))

                        if copy_to_webroot:
                            dirname = os.path.dirname(publish_dest)
                            if not os.path.exists(dirname):
                                os.makedirs(dirname)
                            shutil.copy(local_path, publish_dest)
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
            'stanford': self._generate_stanford_SOA,
            '4_programmatic': self._generate_v4_certificate,
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

        valid_template = 'valid.html'
        verify_template = 'verify.html'

        # generate signature
        signature_filename = os.path.basename(filename) + ".sig"
        signature_filename = os.path.join(output_dir, verify_uuid, signature_filename)
        self._ensure_dir(signature_filename)
        gpg = gnupg.GPG(homedir=settings.CERT_GPG_DIR)
        gpg.encoding = 'utf-8'
        with open(filename) as f:
            signed_data = gpg.sign(data=f, default_key=CERT_KEY_ID, clearsign=False, detach=True).data
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

        verification_paragraph = self.cert_data.get('VERIFY', True)
        verify_uuid = uuid.uuid4().hex if verification_paragraph else ''
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

        paragraph_string = get_cert_date(generate_date, self.issued_date, self.locale, self.timezone)

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
        if verification_paragraph:
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

        if verification_paragraph:
            self._generate_verification_page(
                student_name,
                filename,
                verify_dir,
                verify_uuid,
                download_url
            )

        return (download_uuid, verify_uuid, download_url)

    def _generate_v4_certificate(
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

        verification_paragraph = self.cert_data.get('VERIFY', True)
        verify_uuid = uuid.uuid4().hex if verification_paragraph else ''
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
        page = canvas.Canvas(overlay_pdf_buffer, pagesize=landscape(A4))
        # page width: 841.88976378 pts
        # page height: 595.275590551 pts

        # --- Fonts --- #
        # 0 0 - normal
        # 0 1 - italic
        # 1 0 - bold
        # 1 1 - italic and bold
        font_string = self.template_font_name + '-' + self.template_font_type
        font_file = font_string + '.ttf'
        addMapping(font_string, 0, 0, font_string)
        addMapping(font_string, 0, 1, self.template_font_name + '-Italic')
        addMapping(font_string, 1, 0, self.template_font_name + '-Bold')
        addMapping(font_string, 1, 1, self.template_font_name + '-BoldItalic')

        # These are ordered by preference; cf. font_for_string() above
        self.fontlist = [
            (font_string, font_file, None),
            ('Arial Unicode', 'Arial Unicode.ttf', None),
        ]
        fontlist = self.fontlist

        # Process Translations
        default_translation = settings.DEFAULT_TRANSLATIONS.get(settings.DEFAULT_LOCALE, {})
        successfully_completed = default_translation.get('success_text', '')
        grade_interstitial = default_translation.get('grade_interstitial', '')
        disclaimer_text = default_translation.get('disclaimer_text', '')
        verify_text = default_translation.get('verify_text', '')

        if self.locale in self.course_translations:
            successfully_completed = self.course_translations[self.locale].get('success_text', successfully_completed)
            grade_interstitial = self.course_translations[self.locale].get('grade_interstitial', grade_interstitial)
            disclaimer_text = self.course_translations[self.locale].get('disclaimer_text', disclaimer_text)
            verify_text = self.course_translations[self.locale].get('verify_text', verify_text)

        # calculate interstitial text
        achievements_string = ""
        achievements_description_string = self.interstitial_texts[grade].decode('utf-8')
        if grade and grade.lower() != 'pass':
            grade_html = u"<b>{grade}</b>".format(grade=grade.decode('utf-8'))
            achievements_string = grade_interstitial.decode('utf-8').format(grade=grade_html) + '<br /><br />'
        achievements_paragraph = u"{0}{1}".format(achievements_string, achievements_description_string)

        # Overide achievements/interstitial strings with yaml designations info based on designation
        designation_tag = ''
        for key, value in self.cert_data.get('designations', {}).iteritems():
            if designation in value['titles']:
                # Add student name designation if not Other or None
                if designation not in ['Other', 'None']:
                    student_name = u"{name}, {designation}".format(
                        name=student_name,
                        designation=designation.decode('utf-8'),
                    )
                achievements_string = value['credits']
                achievements_description_string = self.cert_data['CREDITS']
                designation_tag = key
                break

        # print disclaimer text if required
        print_disclaimer = not self.cert_data.get('HAS_DISCLAIMER', False)
        if not print_disclaimer:
            disclaimer_text = ""

        # print verify text if required
        formatted_verify_text = ""
        if verification_paragraph:
            verify_link = (
                u"<a href='{verify_url}/{verify_path}/{verify_uuid}'>"
                u"<b>{verify_url}/{verify_path}/{verify_uuid}</b>"
                u"</a>"
            ).encode('utf-8').format(
                verify_url=settings.CERT_VERIFY_URL,
                verify_path=S3_VERIFY_PATH,
                verify_uuid=verify_uuid,
            )
            formatted_verify_text = verify_text.format(
                verify_link=verify_link,
            )

        # Course Context
        context = {
            'date_string': get_cert_date(generate_date, self.issued_date, self.locale, self.timezone),
            'student_name': student_name.decode('utf-8'),
            'successfully_completed': successfully_completed,
            'course_title': self.long_course.decode('utf-8'),
            'achievements_string': achievements_string,
            'achievements_description_string': achievements_description_string,
            'designation_tag': designation_tag,
            'disclaimer_text': disclaimer_text,
            'verify_text': formatted_verify_text,
        }

        # Render Certificate Theme
        certificate_theme = self.cert_data.get('certificate_theme', [])
        for step in certificate_theme:
            for element, attributes in step.iteritems():
                draw_template_element(self, element, attributes, page)

        # Render any flair below Course Information & Instructor Signatures
        flair = self.cert_data.get('flair', [])
        draw_flair(self, flair, 'bottom', page, context)

        # Render Instructor Signature Blocks
        instructors = self.cert_data.get('instructors', [])
        for _instructor in instructors:
            for __, instructor in _instructor.iteritems():
                x_position = instructor['x']
                y_position = instructor['y']
                for step in instructor['template']:
                    for element, _attributes in step.iteritems():
                        attributes = copy.deepcopy(_attributes)
                        if element == 'text':
                            value = attributes.get('string')
                            if not value:
                                key = attributes['key']
                                if key not in instructor:
                                    continue
                                value = instructor[key]
                            attributes['string'] = value
                            attributes['x'] = x_position + attributes.get('x', 0)
                            attributes['y'] = y_position + attributes.get('y', 0)
                            y_position += attributes['height']
                        elif element == 'line':
                            attributes['x_start'] += x_position
                            attributes['y_start'] += y_position
                            attributes['x_end'] += x_position
                            attributes['y_end'] += y_position
                        elif element == 'image':
                            value = attributes.get('file')
                            y_offset = 0
                            if not value:
                                key = attributes['key']
                                value = instructor[key]
                                if key == 'signature_file':
                                    y_offset = instructor.get('signature_y_offset', 0)
                            attributes['file'] = value

                            filepath = os.path.join(TEMPLATE_DIR, attributes['file'])
                            image = utils.ImageReader(filepath)
                            image_width, image_height = image.getSize()

                            aspect_ratio = image_height / float(image_width)
                            image_width = attributes.get('width', image_width)
                            image_height = int(image_width * aspect_ratio)

                            attributes['x'] = attributes.get('x', 0) + x_position
                            attributes['y'] = attributes.get('y', 0) + y_position + y_offset
                            attributes['height'] = image_height
                            attributes['width'] = image_width
                            y_position = y_position + y_offset + image_height
                        else:
                            continue
                        draw_template_element(self, element, attributes, page)

        # Render Dynamic Course Information
        course_information = self.cert_data.get('course_information', [])
        for step in course_information:
            for element, attributes in step.iteritems():
                draw_template_element(self, element, attributes, page, context=context)

        # Render any flair above Course Information & Instructor Signatures
        draw_flair(self, flair, 'top', page, context)

        # Render Page
        page.showPage()
        page.save()

        # Merge the overlay with the template, then write it to file
        output = PdfFileWriter()
        overlay = PdfFileReader(overlay_pdf_buffer)

        # We render the final certificate by merging several rendered pages.
        # It is fastest if the bottom layer is blank and loaded from memory
        final_certificate = copy.copy(BLANK_PDFS['landscape-A4']).getPage(0)
        final_certificate.mergePage(overlay.getPage(0))

        output.addPage(final_certificate)

        self._ensure_dir(filename)

        outputStream = file(filename, 'wb')
        output.write(outputStream)
        outputStream.close()

        # have to create the verification page seperately from the above
        # conditional because filename must have already been written.
        if verification_paragraph:
            self._generate_verification_page(
                student_name,
                filename,
                verify_dir,
                verify_uuid,
                download_url,
            )

        return (download_uuid, verify_uuid, download_url)
