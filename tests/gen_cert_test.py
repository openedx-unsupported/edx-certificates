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

def test_cert_gen():
    """
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

        verify_files = os.listdir(
                os.path.join(tmpdir, S3_VERIFY_PATH, verify_uuid))
        download_files = os.listdir(
                os.path.join(tmpdir, S3_CERT_PATH, download_uuid))


        # Verify that all files are generated
        assert_true(set(verify_files) == set(VERIFY_FILES))
        assert_true(set(download_files) == set(DOWNLOAD_FILES))

        # Verify that the detached signature is valid
        pdf = os.path.join(tmpdir,
                S3_CERT_PATH, download_uuid, 'Certificate.pdf')
        sig = os.path.join(,
                S3_VERIFY_PATH, verify_uuid, 'Certificate.pdf.sig')
        gpg = gnupg.GPG(gnupghome=settings.CERT_GPG_DIR)

        with open(sig) as f:
            v = gpg.verify_file(f, pdf)
            assert_true(v is not None and v.trust_level >= v.TRUST_FULLY)

        # Remove files
        if os.path.exists(tmpdir):
            shutil.rmtree(tmpdir)


def test_cert_upload():
    """
    Ensures that we can upload a certificate
    to S3 and that it can subsequently be
    downloaded via http
    """

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

    for course_id in settings.CERT_DATA.keys():
        for name in NAMES:
            cert = CertificateGen(course_id)
            (download_uuid, verify_uuid, download_url) = cert.create_and_upload(
                            name, upload=False)
