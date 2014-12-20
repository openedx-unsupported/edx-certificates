# -*- coding: utf-8 -*-
import json
import requests
import responses
import re
import unittest

from requests.exceptions import ConnectionError

import settings
from mock import patch
from openedx_certificates.queue_xqueue import InvalidReturnCode
from openedx_certificates.queue_xqueue import XQueuePullManager


class QueueTest(unittest.TestCase):

    def setUp(self):
        self.manager = XQueuePullManager(
            'https://example.com',
            settings.QUEUE_NAME,
            (settings.QUEUE_AUTH_USER, settings.QUEUE_AUTH_PASS),
            (settings.QUEUE_USER, settings.QUEUE_PASS),
        )
        self._mock_login_good()

    @responses.activate
    def test_login_xqueue_fail(self):
        self._mock_xqueue_login_fail()

    @responses.activate
    def test_iter_one(self):
        self._mock_len(1)
        self._mock_pop_good()
        for response in self.manager:
            self.assertIsNotNone(response)
            break

    @responses.activate
    def test_init_fail_auth_basic(self):
        self._mock_login_bad()
        self.manager.auth_basic = (
            settings.QUEUE_AUTH_USER,
            str(settings.QUEUE_AUTH_PASS) + 'asdf',
        )
        response = self.manager.push(None)
        self.assertIsNone(response)

    @responses.activate
    def test_push(self):
        self._mock_push_good()
        xqueue_reply = {
            'xqueue_header': json.dumps({
                "key": "value",
            }),
            'xqueue_body': json.dumps({
                'action': "action",
                'download_uuid': "download_uuid",
                'verify_uuid': "verify_uuid",
                'username': "username",
                'course_id': "course_id",
                'url': "download_url",
            }),
        }
        self.manager.push(xqueue_reply)

    @responses.activate
    def test_parse_json_fail(self):
        responses.add(
            responses.GET,
            re.compile(r'https://example\.com/xqueue/put_result/'),
            body=json.dumps({
                "return_code": 0,
                "content": 'not json',
            }),
            content_type='application/json',
            status=200,
        )
        certdata = self.manager.pop(None)
        self.assertIsNone(certdata)

    @responses.activate
    def test_pop_one(self):
        self._mock_pop_good()
        certdata = self.manager.pop(
            header=None,
            action='action',
            download_uuid='download_uuid',
            verify_uuid='verify_uuid',
            username='username',
            course_id='course_id',
            url='download_url',
        )

    @responses.activate
    def test_return_code_invalid(self):
        responses.add(
            responses.POST,
            re.compile(r'https://example\.com/xqueue/put_result/'),
            body=json.dumps({
                "return_code": 1,
                "content": "not json",
            }),
            content_type='application/json',
            status=200,
        )
        certdata = self.manager.push({})
        self.assertIsNone(certdata)

    def test_str(self):
        """
        Test string and unicode representations are equal to the base URL
        """
        string_str = str(self.manager)
        string_unicode = unicode(self.manager)
        self.assertEquals(string_str, string_unicode)

    @responses.activate
    def test_len_none(self):
        length = 0
        self._mock_len(length)
        queue_length = len(self.manager)
        self.assertEqual(queue_length, length)

    @responses.activate
    def test_len_one(self):
        length = 1
        self._mock_len(length)
        queue_length = len(self.manager)
        self.assertEqual(queue_length, length)

    @responses.activate
    def test_len_some(self):
        length = 2
        self._mock_len(length)
        queue_length = len(self.manager)
        self.assertEqual(queue_length, length)

    @responses.activate
    def test_len_fail(self):
        self._mock_len()
        queue_length = len(self.manager)
        self.assertEqual(queue_length, 0)

    def _mock_len(self, length=''):
        responses.add(
            responses.GET,
            re.compile(r'https://example\.com/xqueue/get_queuelen/'),
            body=json.dumps({
                "return_code": 0,
                "content": length,
            }),
            content_type='application/json',
            status=200,
        )

    def _mock_push_good(self):
        responses.add(
            responses.POST,
            re.compile(r'https://example\.com/xqueue/put_result/'),
            body=json.dumps({
                "return_code": 0,
                "content": json.dumps({
                    "key": "value",
                }),
            }),
            content_type='application/json',
            status=200,
        )

    def _mock_xqueue_login_fail(self):
        responses.add(
            responses.POST,
            re.compile(r'https://example\.com/xqueue/login/'),
            body=json.dumps({
                "return_code": 1,
                "content": "value"
            }),
            content_type='application/json',
            status=200,
        )

    def _mock_login_bad(self):
        responses.add(
            responses.POST,
            re.compile(r'https://example\.com/xqueue/login/'),
            body=json.dumps({
                "return_code": 0,
                "content": "value"
            }),
            content_type='application/json',
            status=401,
        )

    def _mock_login_good(self):
        responses.add(
            responses.POST,
            re.compile(r'https://example\.com/xqueue/login/'),
            body=json.dumps({
                "return_code": 0,
                "content": "value",
            }),
            content_type='application/json',
            status=200,
        )

    def _mock_pop_good(self):
        responses.add(
            responses.GET,
            re.compile(r'https://example\.com/xqueue/get_submission/'),
            body=json.dumps({
                "return_code": 0,
                "content": json.dumps({
                    "xqueue_body": json.dumps({
                        "action": "pop",
                        "username": "jrbl",
                        "course_id": "blah/blah/blah",
                        "course_name": "ClassX",
                        "name": "Joe",
                    }),
                    "xqueue_header": json.dumps({
                    }),
                }),
            }),
            content_type='application/json',
            status=200,
        )
