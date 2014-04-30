# -*- coding: utf-8 -*-

import os
import StringIO
import uuid
import gnupg
import shutil

from reportlab.platypus import Paragraph
from PyPDF2 import PdfFileWriter, PdfFileReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.fonts import addMapping
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from glob import glob
from HTMLParser import HTMLParser

import settings
import logging.config
import reportlab.rl_config
import tempfile
import boto.s3
from boto.s3.key import Key
from bidi.algorithm import get_display
import arabic_reshaper

reportlab.rl_config.warnOnMissingFontGlyphs = 0

logging.config.dictConfig(settings.LOGGING)
log = logging.getLogger('certificates.' + __name__)
# name of the S3 bucket
# paths to the S3 are for downloading and verifying certs
S3_CERT_PATH = 'downloads'
S3_VERIFY_PATH = 'cert'
BUCKET = settings.CERT_BUCKET
# reduce logging level for gnupg
l = logging.getLogger('gnupg')
l.setLevel('WARNING')


class CertificateGen(object):
    """
    Manages the pdf, signatures, and S3 bucket
    for course certificates.
    Also generates the letterhead for 188x

    """

    def __init__(self, course_id, template_pdf=None, aws_id=None, aws_key=None,
                 dir_prefix=None, long_org=None,
                 long_course=None, issued_date=None, template_dir=None):

        """
        Loads a school pdf template, after initalization
        multiple certificates can be generated and uploaded
        for a single course

        course_id - Full course_id (ex: MITx/6.00x/2012_Fall)
        dir_prefix - Temporary directory for file generation
                     ceritificates and signatures are copied
                     here temporarily before they are uploaded
                     to S3
        template_pdf - Template (filename.pdf) to use
                        for the certificate generation.

        aws_id and aws_key default to None and are necessary
        for S3 uploads

        course_id is needed to look up extra data that needs
        to be present in settings.CERT_DATA, this includes:
          * LONG_ORG - (long name for the organization)
          * LONG_COURSE - (long name for the course)
          * ISSUED_DATA - (month, year that corresponds to the
                           run of the course)

        """
        if not template_dir:
            self.template_dir = settings.TEMPLATE_DIR
        else:
            self.template_dir = template_dir

        if dir_prefix is None:
            default_dir = '/var/tmp/generated_certs/'
            if not os.path.exists(default_dir):
                os.makedirs(default_dir)
            dir_prefix = tempfile.mkdtemp(prefix='/var/tmp/generated_certs/')
        else:
            if not os.path.exists(dir_prefix):
                os.makedirs(dir_prefix)

        self.dir_prefix = dir_prefix
        if not os.path.exists(self.dir_prefix):
            os.makedirs(self.dir_prefix)

        # lookup long names from the course_id
        try:
            if long_org is None:
                self.long_org = settings.CERT_DATA[course_id]['LONG_ORG']
            else:
                self.long_org = long_org
            if long_course is None:
                self.long_course = settings.CERT_DATA[course_id]['LONG_COURSE']
            else:
                self.long_course = long_course
            if issued_date is None:
                self.issued_date = settings.CERT_DATA[course_id]['ISSUED_DATE']
            else:
                self.issued_date = issued_date

            # lookup the version, default to version 1
            if 'VERSION' in settings.CERT_DATA[course_id]:
                self.version = settings.CERT_DATA[course_id]['VERSION']
            else:
                self.version = 1

        except KeyError:
            log.critical("Unable to lookup long names for course {0}".format(
                course_id))
            raise

        # split the org and course from the course_id
        # if COURSE or ORG is set in the configuration
        # dictionary, use that instead

        if 'COURSE' in settings.CERT_DATA[course_id]:
            self.course = settings.CERT_DATA[course_id]['COURSE']
        else:
            self.course = course_id.split('/')[1]

        if 'ORG' in settings.CERT_DATA[course_id]:
            self.org = settings.CERT_DATA[course_id]['ORG']
        else:
            self.org = course_id.split('/')[0]

        # set versioning and type defaults, since we're going to need to
        # generate pdfs differently for the different templates
        self.template_version = 1
        self.template_type = 'honor'

        # get the template version based on the course settings in the
        # certificates repo
        self.template_version = settings.CERT_DATA[course_id].get('VERSION', 1)
        # search for certain keywords in the file name, we'll probably want to
        # be better at parsing this later
        if template_pdf and 'verified' in template_pdf:
            self.template_type = 'verified'

        self.course_association_text = settings.CERT_DATA[course_id].get(
            'COURSE_ASSOCIATION_TEXT', 'a course of study')

        template_path = os.path.join(self.template_dir,
                                     "v{}-cert-templates".format(self.version))
        if template_pdf:
            # Open and load the template pdf for the org
            self.template_pdf = PdfFileReader(
                open(os.path.join(template_path, template_pdf), 'rb'))
        else:
            # For backwards compatibility and standalone testing
            # when the template file is not available use the
            # raw course ID
            self.template_pdf = PdfFileReader(file(
                os.path.join(
                    template_path,
                    "certificate-template-{}-{}.pdf".format(
                    course_id.split('/')[0], course_id.split('/')[1])), "rb"))

        # Open the 188 letterhead pdf
        # if it exists
        letterhead_path = "{0}/letterhead-template-BerkeleyX-CS188.1x.pdf".format(self.template_dir)

        if os.path.exists(letterhead_path):
            self.letterhead_pdf = PdfFileReader(file(letterhead_path, "rb"))
        else:
            self.letterhead_pdf = None

        self.aws_id = aws_id
        self.aws_key = aws_key

    def delete_certificate(self, delete_download_uuid, delete_verify_uuid):
        # TODO remove/archive an existing certificate
        raise NotImplementedError

    def create_and_upload(self, name, upload=settings.S3_UPLOAD, cleanup=True,
                          copy_to_webroot=settings.COPY_TO_WEB_ROOT,
                          cert_web_root=settings.CERT_WEB_ROOT, letterhead=False):
        """
        name - Full name that will be on the certificate
        upload - Upload to S3 (defaults to True)
        letterhead - Set to True to generate a letterhead instead
                     of a certificate.  Letterheads are not signed
                     so there will be no verification pages.

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

        certificates_path = os.path.join(self.dir_prefix, S3_CERT_PATH)
        verify_path = os.path.join(self.dir_prefix, S3_VERIFY_PATH)

        if letterhead:
            (download_uuid, download_url) = self._generate_letterhead(
                student_name=name,
                download_dir=certificates_path)
        else:
            (download_uuid,
                verify_uuid,
                download_url) = self._generate_certificate(
                    student_name=name,
                    download_dir=certificates_path,
                    verify_dir=verify_path)

        # upload generated certificate and verification files to S3
        for dirpath, dirnames, filenames in os.walk(self.dir_prefix):
            for filename in filenames:
                local_path = os.path.join(dirpath, filename)
                dest_path = os.path.relpath(
                    os.path.join(dirpath, filename),
                    start=self.dir_prefix
                )
                if upload:
                    s3_conn = boto.connect_s3()
                    bucket = s3_conn.get_bucket(BUCKET)
                    key = Key(bucket, name=dest_path)
                    log.info('uploading to {0} from {1} to {2}'.format(
                        settings.CERT_URL, local_path, dest_path))
                    key.set_contents_from_filename(local_path, policy='public-read')

                if copy_to_webroot:
                    publish_dest = os.path.join(cert_web_root, dest_path)
                    log.info('publishing to {0} from {1} to {2}'.format(
                        settings.CERT_URL, local_path, publish_dest))
                    if not os.path.exists(os.path.dirname(publish_dest)):
                        os.makedirs(os.path.dirname(publish_dest))
                    shutil.copy(local_path, publish_dest)

        if cleanup:
            if os.path.exists(self.dir_prefix):
                shutil.rmtree(self.dir_prefix)

        return (download_uuid, verify_uuid, download_url)

    def _generate_letterhead(self, student_name, download_dir,
                             filename='distinction-letter.pdf'):

        """

        Generate a PDF letterhead for 188x

        return (download_uuid, download_url)

        """

        # A4 page size is 210mm x 297mm

        download_uuid = uuid.uuid4().hex
        download_url = "{base_url}/{cert}/{uuid}/{file}".format(
            base_url=settings.CERT_DOWNLOAD_URL,
            cert=S3_CERT_PATH, uuid=download_uuid, file=filename
        )

        filename = os.path.join(download_dir, download_uuid, filename)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        c = canvas.Canvas(overlay_pdf_buffer)
        c.setPageSize((297 * mm, 210 * mm))

        # register all fonts in the fonts/ dir,
        # there are more fonts in here than we need
        # but the performance hit seems minimal
        # the open-source repo does not include
        # a font that has full unicode support.
        for font_file in glob('{0}/fonts/*.ttf'.format(self.template_dir)):
            font_name = os.path.basename(os.path.splitext(font_file)[0])
            pdfmetrics.registerFont(TTFont(font_name, font_file))

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

        styleArial = ParagraphStyle(
            name="arial", leading=10,
            fontName='Arial Unicode'
        )
        styleOpenSans = ParagraphStyle(
            name="opensans-regular",
            leading=10, fontName='OpenSans-Regular'
        )
        styleOpenSansLight = ParagraphStyle(
            name="opensans-light",
            leading=10, fontName='OpenSans-Light'
        )

        # Text is overlayed top to bottom
        #   * Student's name
        #   * comma

        WIDTH = 210  # width in mm (A4)
        HEIGHT = 297  # height in mm (A4)

        LEFT_INDENT = 36  # mm from the left side to write the text

        #######  Student name

        # default is to use the DejaVu font for the name,
        # will fall back to Arial if there are
        # unusual characters
        style = styleOpenSans
        width = stringWidth(
            student_name.decode('utf-8'),
            'OpenSans-Bold', 16) / mm
        paragraph_string = "<b>{0}</b>".format(student_name)

        if self._use_unicode_font(student_name):
            style = styleArial
            width = stringWidth(student_name.decode('utf-8'),
                                'Arial Unicode', 16) / mm
            # There is no bold styling for Arial :(
            paragraph_string = "{0}".format(student_name)

        style.fontSize = 16
        style.textColor = colors.Color(
            0, 0.624, 0.886)
        style.alignment = TA_LEFT

        paragraph = Paragraph(paragraph_string, style)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 217.7 * mm)

        ########## Comma
        style = styleOpenSansLight
        style.fontSize = 14
        style.textColor = colors.Color(
            0.302, 0.306, 0.318)
        # Place the comma after the student's name
        paragraph = Paragraph(",", style)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, (LEFT_INDENT + width) * mm, 216.8 * mm)

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
            file("{0}/blank-portrait.pdf".format(self.template_dir), "rb"))

        final_certificate = blank_pdf.getPage(0)
        final_certificate.mergePage(self.letterhead_pdf.getPage(0))
        final_certificate.mergePage(overlay.getPage(0))

        output.addPage(final_certificate)

        self._ensure_dir(filename)

        outputStream = file(filename, "wb")
        output.write(outputStream)
        outputStream.close()

        return (download_uuid, download_url)

    def _generate_certificate(self, student_name, download_dir,
                              verify_dir, filename='Certificate.pdf'):
        """
        Generate a PDF certificate, signature and static html
        files used for validation.

        return (download_uuid, verify_uuid, download_url)

        """
        if self.template_version == 1:
            return self._generate_v1_certificate(student_name, download_dir, verify_dir, filename)

        elif self.template_version == 2:
            return self._generate_v2_certificate(student_name, download_dir, verify_dir, filename)

        elif self.template_version == 'MIT_PE':
            return self._generate_mit_pe_certificate(student_name, download_dir, verify_dir, filename)

    def _generate_v1_certificate(self, student_name, download_dir, verify_dir, filename='Certificate.pdf'):
        # A4 page size is 297mm x 210mm

        verify_uuid = uuid.uuid4().hex
        download_uuid = uuid.uuid4().hex
        download_url = "{base_url}/{cert}/{uuid}/{file}".format(
            base_url=settings.CERT_DOWNLOAD_URL,
            cert=S3_CERT_PATH, uuid=download_uuid, file=filename
        )
        filename = os.path.join(download_dir, download_uuid, filename)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        c = canvas.Canvas(overlay_pdf_buffer)
        c.setPageSize((297 * mm, 210 * mm))

        # register all fonts in the fonts/ dir,
        # there are more fonts in here than we need
        # but the performance hit seems minimal

        for font_file in glob('{0}/fonts/*.ttf'.format(self.template_dir)):
            font_name = os.path.basename(os.path.splitext(font_file)[0])
            pdfmetrics.registerFont(TTFont(font_name, font_file))

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

        styleArial = ParagraphStyle(
            name="arial", leading=10,
            fontName='Arial Unicode'
        )
        styleOpenSans = ParagraphStyle(
            name="opensans-regular", leading=10,
            fontName='OpenSans-Regular'
        )
        styleOpenSansLight = ParagraphStyle(
            name="opensans-light", leading=10,
            fontName='OpenSans-Light'
        )

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

        ####### CERTIFICATE

        styleOpenSansLight.fontSize = 19
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
            0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "CERTIFICATE"

        # Right justified so we compute the width
        width = stringWidth(
            paragraph_string,
            'OpenSans-Light', 19) / mm
        paragraph = Paragraph("{0}".format(
            paragraph_string), styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, (WIDTH - RIGHT_INDENT - width) * mm, 163 * mm)

        ####### Issued ..

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
            0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "Issued {0}".format(self.issued_date)

        # Right justified so we compute the width
        width = stringWidth(
            paragraph_string,
            'OpenSans-LightItalic', 12) / mm
        paragraph = Paragraph("<i>{0}</i>".format(
            paragraph_string), styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, (WIDTH - RIGHT_INDENT - width) * mm, 155 * mm)

        ####### This is to certify..

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
            0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "This is to certify that"
        paragraph = Paragraph(paragraph_string, styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 132.5 * mm)

        #######  Student name

        # default is to use the DejaVu font for the name,
        # will fall back to Arial if there are
        # unusual characters
        style = styleOpenSans
        style.leading = 10
        width = stringWidth(
            student_name.decode('utf-8'),
            'OpenSans-Bold', 34) / mm
        paragraph_string = "<b>{0}</b>".format(student_name)

        if self._use_unicode_font(student_name):
            style = styleArial
            width = stringWidth(student_name.decode('utf-8'),
                                'Arial Unicode', 34) / mm
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

        ####### Successfully completed

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

        ###### Course name

        #styleOpenSans.fontName = 'OpenSans-BoldItalic'
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

        paragraph_string = "<b><i>{0}: {1}</i></b>".format(
            self.course, self.long_course)
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

        ###### A course of study..

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.textColor = colors.Color(
            0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "a course of study offered by <b>{0}</b>" \
                           ", an online learning<br /><br />initiative of " \
                           "<b>{1}</b> through <b>edX</b>.".format(
                               self.org, self.long_org)

        paragraph = Paragraph(paragraph_string, styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 78 * mm)

        ###### Honor code

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
            verify_uuid=verify_uuid
        )
        paragraph = Paragraph(paragraph_string, styleOpenSansLight)

        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, 0 * mm, 28 * mm)

        ########

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
            file("{0}/blank.pdf".format(self.template_dir), "rb")
        )

        final_certificate = blank_pdf.getPage(0)
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

    def _generate_v2_certificate(self, student_name, download_dir,
                                 verify_dir, filename='Certificate.pdf'):
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
        c = canvas.Canvas(overlay_pdf_buffer)
        c.setPageSize((WIDTH * mm, HEIGHT * mm))

        # register all fonts in the fonts/ dir,
        # there are more fonts in here than we need
        # but the performance hit seems minimal

        for font_file in glob('{0}/fonts/*.ttf'.format(self.template_dir)):
            font_name = os.path.basename(os.path.splitext(font_file)[0])
            pdfmetrics.registerFont(TTFont(font_name, font_file))

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

        ########
        #
        # New things below
        #
        ########

        #### STYLE: typeface assets
        addMapping('AvenirNext-Regular', 0, 0, 'AvenirNext-Regular')
        addMapping('AvenirNext-DemiBold', 1, 0, 'AvenirNext-DemiBold')

        #### STYLE: grid/layout
        LEFT_INDENT = 23  # mm from the left side to write the text
        MAX_WIDTH = 150  # maximum width on the content in the cert, used for wrapping

        #### STYLE: template-wide typography settings
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

        #### STYLE: template-wide color settings
        style_color_metadata = colors.Color(0.541176, 0.509804, 0.560784)
        style_color_name = colors.Color(0.000000, 0.000000, 0.000000)

        #### STYLE: positioning
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

        #### STYLE: verified settings
        v_style_color_course = colors.Color(0.701961, 0.231373, 0.400000)

        #### HTML Parser ####
        # Since the final string is HTML in a PDF we need to un-escape the html
        # when calculating the string width.
        html = HTMLParser()

        #### ================== ####

        #### ELEM: Metacopy
        styleAvenirNext = ParagraphStyle(name="avenirnext-regular", fontName='AvenirNext-Regular')

        styleAvenirNext.alignment = TA_LEFT
        styleAvenirNext.fontSize = style_type_metacopy_size
        styleAvenirNext.leading = style_type_metacopy_leading
        styleAvenirNext.textColor = style_color_metadata

        #### ELEM: Metacopy - Title: This is to certify that
        if self.template_type == 'verified':
            y_offset = pos_metacopy_title_y

            paragraph_string = 'This is to certify that'

            paragraph = Paragraph(paragraph_string, styleAvenirNext)
            paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
            paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        #### ================== ####

        ####### ELEM: Student Name
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

        #TODO: get all strings working reshaped and handling bi-directional strings
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

        #### ================== ####

        ##### ELEM: Metacopy - Achievement: successfully completed and received a passing grade in
        y_offset = pos_metacopy_achivement_y

        paragraph_string = 'successfully completed and received a passing grade in'

        paragraph = Paragraph("{0}".format(paragraph_string), styleAvenirNext)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        #### ================== ####

        ##### ELEM: Course Name
        y_offset_larger = pos_course_y
        y_offset_smaller = pos_course_small_y

        styleAvenirCourseName = ParagraphStyle(name="avenirnext-demi", fontName='AvenirNext-DemiBold')
        styleAvenirCourseName.textColor = style_color_name
        if self.template_type == 'verified':
            styleAvenirCourseName.textColor = v_style_color_course

        paragraph_string = "{0}: {1}".format(self.course, self.long_course)
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

        #### ================== ####

        #### ELEM: Metacopy - Org: a course of study...
        y_offset = pos_metacopy_org_y
        paragraph_string = "{2} offered by {0}" \
                           ", an online learning<br /><br />initiative of " \
                           "{1} through edX.".format(
                               self.org, self.long_org, self.course_association_text)

        paragraph = Paragraph(paragraph_string, styleAvenirNext)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        #### ================== ####

        ##### ELEM: Footer
        styleAvenirFooter = ParagraphStyle(name="avenirnext-demi", fontName='AvenirNext-DemiBold')

        styleAvenirFooter.alignment = TA_LEFT
        styleAvenirFooter.fontSize = style_type_footer_size

        ##### ELEM: Footer - Issued on Date
        x_offset = pos_footer_date_x
        y_offset = pos_footer_date_y
        paragraph_string = "Issued {0}".format(self.issued_date)
        # Right justified so we compute the width
        paragraph = Paragraph("{0}".format(
            paragraph_string), styleAvenirFooter)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, y_offset * mm)

        ########

        #### ================== ####

        ##### ELEM: Footer - Verify Authenticity URL
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

        blank_pdf = PdfFileReader(
            file("{0}/blank-letter.pdf".format(self.template_dir), "rb")
        )

        final_certificate = blank_pdf.getPage(0)
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

    def _generate_mit_pe_certificate(self, student_name, download_dir, verify_dir, filename='Certificate.pdf'):
        """
        Generate the BigDataX certs
        """
        # 8.5x11 page size 279.4mm x 215.9mm
        WIDTH = 279  # width in mm (8.5x11)
        HEIGHT = 216  # height in mm (8.5x11)

        download_uuid = uuid.uuid4().hex
        verify_uuid = uuid.uuid4().hex
        download_url = "https://s3.amazonaws.com/{0}/" \
                       "{1}/{2}/{3}".format(
                           BUCKET, S3_CERT_PATH,
                           download_uuid, filename)

        filename = os.path.join(download_dir, download_uuid, filename)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        c = canvas.Canvas(overlay_pdf_buffer)
        c.setPageSize((WIDTH * mm, HEIGHT * mm))

        # register all fonts in the fonts/ dir,
        # there are more fonts in here than we need
        # but the performance hit seems minimal

        for font_file in glob('{0}/fonts/*.ttf'.format(self.template_dir)):
            font_name = os.path.basename(os.path.splitext(font_file)[0])
            pdfmetrics.registerFont(TTFont(font_name, font_file))

        #### STYLE: grid/layout
        LEFT_INDENT = 10  # mm from the left side to write the text
        MAX_WIDTH = 260  # maximum width on the content in the cert, used for wrapping

        #### STYLE: template-wide typography settings
        style_type_name_size = 36
        style_type_name_leading = 53
        style_type_name_med_size = 22
        style_type_name_med_leading = 27
        style_type_name_small_size = 18
        style_type_name_small_leading = 21

        #### STYLE: template-wide color settings
        style_color_name = colors.Color(0.000000, 0.000000, 0.000000)

        #### STYLE: positioning
        pos_name_y = 137
        pos_name_med_y = 142
        pos_name_small_y = 140
        pos_name_no_wrap_offset_y = 2

        #### HTML Parser ####
        # Since the final string is HTML in a PDF we need to un-escape the html
        # when calculating the string width.
        html = HTMLParser()

        ####### ELEM: Student Name
        # default is to use Garamond for the name,
        # will fall back to Arial if there are
        # unusual characters
        y_offset_name = pos_name_y
        y_offset_name_med = pos_name_med_y
        y_offset_name_small = pos_name_small_y

        styleUnicode = ParagraphStyle(name="arial", leading=10,
                                      fontName='Arial Unicode')
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
        # if we can't use it, use Gentium
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

        ## Generate the final PDF
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
            file("{0}/blank-letter.pdf".format(self.template_dir), "rb")
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

    def _generate_verification_page(self,
                                    name,
                                    filename,
                                    output_dir,
                                    verify_uuid,
                                    download_url):
        """
        This generates the gpg signature and the
        verification files including
        the static html files that will be seen when
        the user clicks the verification link.

            name - full name of the student
            filename - path on the local filesystem to the certificate pdf
            output_dir - where to write the verification files
            verify_uuid - UUID for the verification files
            download_url - link to the pdf download (for the verifcation page)

        """

        # Do not do anything if there isn't any KEY to sign with

        if not settings.CERT_KEY_ID:
            return

        # generate signature
        signature_filename = os.path.basename(filename) + ".sig"
        signature_filename = os.path.join(
            output_dir, verify_uuid, signature_filename)
        gpg = gnupg.GPG(gnupghome=settings.CERT_GPG_DIR)
        gpg.encoding = 'utf-8'
        with open(filename) as f:
            if settings.CERT_KEY_ID:
                signed_data = gpg.sign_file(
                    f, detach=True,
                    keyid=settings.CERT_KEY_ID).data
            else:
                signed_data = gpg.sign_file(f, detach=True).data

        self._ensure_dir(signature_filename)
        with open(signature_filename, 'w') as f:
            f.write(signed_data.encode('utf-8'))

        valid_template = 'valid.html'
        verify_template = 'verify.html'

        if self.template_version == 2:
            valid_template = 'v2/valid.html'
            verify_template = 'v2/verify.html'

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

        type_map['verified']['explanation'] = "An ID verified certificate signifies that an edX user has agreed to abide by edX's honor code and completed all of the required tasks of this course under its guidelines, as well as having their photo ID checked to verify their identity."
        type_map['verified']['img'] = '''
            <div class="wrapper--img">
                <img class="img--idverified" src="/v2/static/images/logo-idverified.png" alt="ID Verified Certificate Logo" />
            </div>
        '''
        type_map['honor']['explanation'] = "An honor code certificate signifies that an edX user has agreed to abide by edX's honor code and completed all of the required tasks of this course under its guidelines."
        type_map['honor']['img'] = ""

        with open("{0}/{1}".format(self.template_dir, valid_template)) as f:
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
            EXPLANATION=type_map[self.template_type]['explanation'],
        )

        with open(os.path.join(
                output_dir, verify_uuid, "valid.html"), 'w') as f:
            f.write(valid_page.encode('utf-8'))

        with open("{0}/{1}".format(self.template_dir, verify_template)) as f:
            verify_page = f.read().decode('utf-8').format(
                NAME=name.decode('utf-8'),
                SIG_URL=signature_download_url,
                SIG_FILE=os.path.basename(signature_download_url),
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
        """
        for character in string.decode('utf-8'):
            #I believe chinese characters are 0x4e00 to 0x9fff
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
        # This function should return true for any
        # string that that opensans/baskerville can't render.
        # I don't know how to query the font, so I assume that
        # any high codepoint is unsupported.
        # This can be improved dramatically

        #I believe chinese characters are 0x4e00 to 0x9fff
        # Japanese kanji seem to be >= 0x3000
        return self._contains_characters_above(string, 0x0500)
