# -*- coding: utf-8 -*-
from datetime import datetime
import gnupg
import mock
import os
import shutil
import tempfile
import unittest
import urllib2
from mock import patch
import StringIO

from ddt import ddt, data, unpack
from nose.plugins.skip import SkipTest
from nose.tools import assert_true
from nose.tools import assert_false
from nose.tools import assert_equal
from nose.tools import raises
from reportlab.lib.pagesizes import A4, letter, landscape
from reportlab.pdfgen import canvas

import settings
from gen_cert import CertificateGen
from gen_cert import S3_CERT_PATH, S3_VERIFY_PATH
from gen_cert import get_cert_date
from openedx_certificates.renderers.elements import draw_flair
from test_data import NAMES


CERT_FILENAME = settings.CERT_FILENAME
CERT_FILESIG = settings.CERT_FILENAME + '.sig'
VERIFY_FILES = set(['valid.html', 'verify.html'])
DOWNLOAD_FILES = set([])


def setUp():
    """A gratuitous setUp to document that these bits are added dynamically."""
    VERIFY_FILES.add(CERT_FILESIG)
    DOWNLOAD_FILES.add(CERT_FILENAME)


def test_cert_gen():
    """Do end-to-end generation test (sans s3) for every course.

    For every course:
     * Generates a single dummy certificate
     * Verifies all file artificats are created
     * Verifies the pdf signature against the detached signature
     * Publishes the certificate to a temporary directory
    """

    for course_id in settings.CERT_DATA.keys():
        tmpdir = tempfile.mkdtemp()
        cert = CertificateGen(course_id)
        (download_uuid, verify_uuid, download_url) = cert.create_and_upload(
            'John Smith', upload=False, copy_to_webroot=True,
            cert_web_root=tmpdir, cleanup=True)

        # If the settings we're testing have VERIFY turned off, skip those tests, too
        if settings.CERT_DATA[course_id].get('VERIFY', True) and verify_uuid:
            verify_files = os.listdir(os.path.join(tmpdir, S3_VERIFY_PATH, verify_uuid))
            download_files = os.listdir(os.path.join(tmpdir, S3_CERT_PATH, download_uuid))

            # All the verification files were created correctly
            assert_true(set(verify_files) == VERIFY_FILES)

            # The detached signature is valid
            pdf = os.path.join(tmpdir, S3_CERT_PATH, download_uuid, CERT_FILENAME)
            sig = os.path.join(tmpdir, S3_VERIFY_PATH, verify_uuid, CERT_FILESIG)
            gpg = gnupg.GPG(homedir=settings.CERT_GPG_DIR)
            with open(pdf) as f:
                v = gpg.verify_file(f, sig)
            assert_true(v is not None and v.trust_level >= v.TRUST_FULLY)

            # And of course we have a download file, right?
            assert_true(set(download_files) == DOWNLOAD_FILES)

        # Remove files
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)


@patch('gen_cert.TMP_GEN_DIR', new_callable=tempfile.mkdtemp)
def test_creates_default_dir(gen_dir):
    """Make sure the certificate generator creates the default directory if it doesn't exist."""
    gen = None
    try:
        assert_true(os.path.exists(gen_dir))
        shutil.rmtree(gen_dir)
        assert_false(os.path.exists(gen_dir))
        gen = CertificateGen(settings.CERT_DATA.keys()[0])
        assert_true(os.path.exists(gen.dir_prefix))
    finally:
        if os.path.exists(gen_dir):
            shutil.rmtree(gen_dir)
        if gen:
            # Avoid catastrophy
            assert_true(gen.dir_prefix.startswith(gen_dir))
            shutil.rmtree(gen.dir_prefix)


def test_designation():
    """
    Generate a test certificate with designation text
    """
    designations = ['PharmD', 'MD']
    for course_id in settings.CERT_DATA.keys():
        for designation in designations:
            tmpdir = tempfile.mkdtemp()
            cert = CertificateGen(course_id, designation=designation)
            for name in NAMES:
                (download_uuid, verify_uuid, download_url) = cert.create_and_upload(
                    name,
                    upload=False,
                    copy_to_webroot=True,
                    cert_web_root=tmpdir,
                    cleanup=True,
                )
                if os.path.exists(tmpdir):
                    shutil.rmtree(tmpdir)


def test_cert_names():
    """Generate certificates for all names in NAMES without saving or uploading"""
    # XXX: This is meant to catch unicode rendering problems, but does it?
    course_id = settings.CERT_DATA.keys()[0]
    for name in NAMES:
        cert = CertificateGen(course_id)
        (download_uuid, verify_uuid, download_url) = cert.create_and_upload(name, upload=False)


@raises(ValueError)
def test_uncovered_unicode_cert_name():
    """
    Fail to generate a certificate for a user with unsupported unicode character in name
    """
    course_id = settings.CERT_DATA.keys()[0]
    cert = CertificateGen(course_id)
    (download_uuid, verify_uuid, download_url) = cert.create_and_upload(u"Memphis \u0007", upload=False)


def test_cert_upload():  # pragma: no cover
    """Check here->S3->http round trip."""
    if not settings.CERT_AWS_ID or not settings.CERT_AWS_KEY:
        raise SkipTest
    cert = CertificateGen(settings.CERT_DATA.keys()[0])
    (download_uuid, verify_uuid, download_url) = cert.create_and_upload('John Smith')
    r = urllib2.urlopen(download_url)
    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(r.read())


def test_render_flair_function_false():
    """
    Make sure flair rendering function behaves properly when it is NOT given flair to render
    """
    cert = CertificateGen(settings.CERT_DATA.keys()[0])
    overlay_pdf_buffer = StringIO.StringIO()
    page = canvas.Canvas(overlay_pdf_buffer, pagesize=landscape(A4))
    flair = []
    assert_false(draw_flair(cert, flair, 'top', page, context=None))


def test_render_flair_function_true():
    """
    Make sure flair rendering function behaves properly when it IS given flair to render
    """
    cert = CertificateGen(settings.CERT_DATA.keys()[0])
    overlay_pdf_buffer = StringIO.StringIO()
    page = canvas.Canvas(overlay_pdf_buffer, pagesize=landscape(A4))
    flair = [{'image': {'x': 575, 'y': 325, 'width': 125, 'height': 125, 'file': 'images/test_flair.png'}}]
    assert_true(draw_flair(cert, flair, 'top', page, context=None))


@ddt
class CertificateTestsDates(unittest.TestCase):
    """
    Tests for the certificates date generation function.
    """

    def mock_get_today():
        return datetime.strptime('2016-08-22 04:00:00', '%Y-%m-%d %H:%M:%S')

    @data(
        (None, None, None, 'August 22, 2016'),
        (None, None, 'UTC', 'August 22, 2016'),
        (None, None, 'US/Pacific', 'August 21, 2016'),
        ('2016-08-22', None, 'UTC', 'August 22, 2016'),
        ('2016-08-22', None, 'US/Pacific', 'August 22, 2016'),
        ('2016-08-22', 'en_US', None, 'August 22, 2016'),
        ('2016-08-22', 'es_419', None, '22 de agosto de 2016'),
        ('2016-08-22', 'vi_VN', None, u'Ngày 22 tháng 8 năm 2016'),
    )
    @unpack
    @mock.patch('gen_cert.get_today', mock_get_today)
    def test_cert_date(self, force_date, locale, timezone, expected):
        """
        Assert certs render dates correctly
        """
        cert_date = get_cert_date(force_date, locale, timezone)
        assert_equal(expected, cert_date)
