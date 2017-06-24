# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
import unittest

from nose.tools import assert_true
from nose.tools import assert_equal
from nose.tools import assert_in

import settings
from gen_cert import CertificateGen
from test_data import NAMES


class CertificateSubtemplateTests(unittest.TestCase):
    """
    Tests the subtemplates feature for certs

    Subtemplates are currently written to key on designations only
    """
    subtemplate_courses = [
        'Test/Subtemplates/ByDesignations',
    ]

    def test_subtemplate_courses_exist(self):
        """
        Check that the subtemplate courses exist in settings.CERT_DATA
        """
        found_sub_course_ids = [
            course_id
            for course_id, course_data in settings.CERT_DATA.iteritems()
            if 'subtemplates' in course_data
        ]
        assert_equal(
            set(self.subtemplate_courses),
            set(found_sub_course_ids)
        )

    def test_subtemplate_courses_have_proper_data(self):
        """
        Check that subtemplates have proper key-value pairs in 'subtemplates'
        """
        for course_id in self.subtemplate_courses:
            cert_data = settings.CERT_DATA[course_id]
            subtemplates = cert_data['subtemplates']
            assert_true(isinstance(subtemplates, dict))
            for designation, sub_course_id in subtemplates.iteritems():
                assert_in(sub_course_id, settings.CERT_DATA)

    def test_subtemplates(self):
        """
        Generates certficates with subtemplates
        """
        tmpdir = tempfile.mkdtemp()
        for course_id in self.subtemplate_courses:
            cert_data = settings.CERT_DATA[course_id]
            subtemplates = cert_data['subtemplates']
            for designation, course_id in subtemplates.iteritems():
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
