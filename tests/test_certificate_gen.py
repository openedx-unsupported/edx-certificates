# -*- coding: utf-8 -*-
import unittest

from ddt import ddt, data, unpack
from nose.tools import assert_equal

from gen_cert import CertificateGen


@ddt
class CertificateGenTests(unittest.TestCase):
    """
    Tests CertificateGen class
    """
    @data(
        # designation, new_course_id, new_designation, result
        (None, None, None, False),
        (None, 'edX/DemoX_v4/Demo_Course_v4', None, True),
        (None, 'edX/DemoX_v4/Demo_Course_v4', 'new_designation', False),
        ('designation', None, None, False),
        ('designation', 'edX/DemoX_v4/Demo_Course_v4', None, False),
        ('designation', 'edX/DemoX_v4/Demo_Course_v4', 'new_designation', False),
        ('designation', 'edX/DemoX_v4/Demo_Course_v4', 'designation', True),
        (None, 'course-v1:edX+DemoX_v4+Custom_Instructor_Block_v4', None, False),
        (None, 'course-v1:edX+DemoX_v4+Custom_Instructor_Block_v4', 'new_designation', False),
        ('new_designation', 'course-v1:edX+DemoX_v4+Custom_Instructor_Block_v4', 'new_designation', False),
    )
    @unpack
    def test_is_reusable(
        self,
        designation,
        new_course_id,
        new_designation,
        result
    ):
        """
        Test is_reusable return boolean based on course_id and designation
        """
        cert = CertificateGen(
            'edX/DemoX_v4/Demo_Course_v4',
            designation=designation,
        )
        assert_equal(cert.is_reusable(new_course_id, new_designation), result)
