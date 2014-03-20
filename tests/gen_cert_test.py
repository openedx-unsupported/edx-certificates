# -*- coding: utf-8 -*-
from gen_cert import CertificateGen
from gen_cert import S3_CERT_PATH, S3_VERIFY_PATH
from nose.tools import assert_true
from nose.plugins.skip import SkipTest
import settings
import os
import gnupg
import shutil
import urllib2
import tempfile
from test_data import NAMES

VERIFY_FILES = ['valid.html', 'Certificate.pdf.sig', 'verify.html']
DOWNLOAD_FILES = ['Certificate.pdf']

REQUIRED_SETTINGS = ["CERT_AWS_ID", "CERT_AWS_KEY", "CERT_BUCKET", "CERT_KEY_ID"]

def skip_if_not_configured():
    """Tests are skipped unless settings.py has been configured
    with valid credentials"""
    for required in REQUIRED_SETTINGS:
        if not hasattr(settings, required):
            raise SkipTest
        elif getattr(settings, required) is None:
            raise SkipTest
        else:
            pass


def test_cert_gen():
    """
    For every course:
     * Generates a single dummy certificate
     * Verifies all file artificats are created
     * Verifies the pdf signature against the detached signature
    """
    skip_if_not_configured()

    for course_id in settings.CERT_DATA.keys():
        cert = CertificateGen(course_id)
        (download_uuid, verify_uuid, download_url) = cert.create_and_upload(
                        'John Smith', upload=False, cleanup=False)

        verify_files = os.listdir(
                os.path.join(cert.dir_prefix, S3_VERIFY_PATH, verify_uuid))
        download_files = os.listdir(
                os.path.join(cert.dir_prefix, S3_CERT_PATH, download_uuid))


        # Verify that all files are generated
        assert_true(set(verify_files) == set(VERIFY_FILES))
        assert_true(set(download_files) == set(DOWNLOAD_FILES))

        # Verify that the detached signature is valid
        pdf = os.path.join(cert.dir_prefix,
                S3_CERT_PATH, download_uuid, 'Certificate.pdf')
        sig = os.path.join(cert.dir_prefix,
                S3_VERIFY_PATH, verify_uuid, 'Certificate.pdf.sig')
        gpg = gnupg.GPG(gnupghome=settings.CERT_GPG_DIR)

        with open(sig) as f:
            v = gpg.verify_file(f, pdf)
            assert_true(v is not None and v.trust_level >= v.TRUST_FULLY)

        # Remove files
        if os.path.exists(cert.dir_prefix):
            shutil.rmtree(cert.dir_prefix)


def test_cert_upload():
    """
    Ensures that we can upload a certificate
    to S3 and that it can subsequently be
    downloaded via http
    """
    skip_if_not_configured()

    cert = CertificateGen(settings.CERT_DATA.keys()[0], settings.CERT_AWS_ID,
                                settings.CERT_AWS_KEY)
    (download_uuid, verify_uuid, download_url) = cert.create_and_upload(
                      'John Smith')
    r = urllib2.urlopen(download_url)
    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(r.read())


def test_cert_names():
    """
    Generates certificates for all names in NAMES
    Deletes them when finished, doesn't upload to S3
    """
    skip_if_not_configured()

    for course_id in settings.CERT_DATA.keys():
        for name in NAMES:
            cert = CertificateGen(course_id)
            (download_uuid, verify_uuid, download_url) = cert.create_and_upload(
                            name, upload=False)
