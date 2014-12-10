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
        auth_basic = (settings.QUEUE_AUTH_USER, settings.QUEUE_AUTH_PASS)
        auth_xqueue = (settings.QUEUE_USER, settings.QUEUE_PASS)
        self.manager = XQueuePullManager(
            'https://example.com',
            settings.QUEUE_NAME,
            auth_basic,
            auth_xqueue,
            (settings.CERT_AWS_ID, settings.CERT_AWS_KEY),
        )
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

    # TODO: test on empty queue
    def test_step(self):
        @responses.activate
        def run():
            responses.add(
                responses.GET,
                re.compile(r'https://example\.com/xqueue/get_queuelen/'),
                body=json.dumps({
                    "return_code": 0,
                    "content": 1,
                }),
                content_type='application/json',
                status=200,
            )
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
            self.manager.step(seconds_to_sleep=0, iterations=1)
        run()

    def test_init_fail_auth_basic(self):
        @responses.activate
        def run():
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
            self.manager.auth_basic = (
                settings.QUEUE_AUTH_USER,
                str(settings.QUEUE_AUTH_PASS) + 'asdf',
            )
            with self.assertRaises(ConnectionError):
                len(self.manager)
        run()

    def test_init_fail_auth_xqueue(self):
        pass

    def test_put(self):
        @responses.activate
        def run():
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
            self.manager.put(xqueue_reply)
        run()

    def test_parse_json_fail(self):
        @responses.activate
        def run():
            responses.add(
                responses.GET,
                re.compile(r'https://example\.com/xqueue/get_submission/'),
                body=json.dumps({
                    "return_code": 0,
                    "content": 'not json',
                }),
                content_type='application/json',
                status=200,
            )
            with self.assertRaises(ValueError):
                certdata = self.manager.pop()
        run()

    def test_pop_one(self):
        @responses.activate
        def run():
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
            certdata = self.manager.pop()
        run()

    def test_pop_none(self):
        """
        certdata = self.manager.pop()
        """
        pass

    def test_return_code_invalid(self):
        @responses.activate
        def run():
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
            with self.assertRaises(InvalidReturnCode):
                certdata = self.manager.put({})
        run()

    def test_str(self):
        """
        Test string and unicode representations are equal to the base URL
        """
        string_str = str(self.manager)
        string_unicode = unicode(self.manager)
        self.assertEquals(string_str, string_unicode)

    def test_len_none(self):
        @responses.activate
        def run():
            responses.add(
                responses.GET,
                re.compile(r'https://example\.com/xqueue/get_queuelen/'),
                body=json.dumps({
                    "return_code": 0,
                    "content": 0,
                }),
                content_type='application/json',
                status=200,
            )
            queue_length = len(self.manager)
            self.assertEqual(queue_length, 0)
        run()

    def test_len_one(self):
        @responses.activate
        def run():
            responses.add(
                responses.GET,
                re.compile(r'https://example\.com/xqueue/get_queuelen/'),
                body=json.dumps({
                    "return_code": 0,
                    "content": 1,
                }),
                content_type='application/json',
                status=200,
            )
            queue_length = len(self.manager)
            self.assertEqual(queue_length, 1)
        run()

    def test_len_some(self):
        @responses.activate
        def run():
            responses.add(
                responses.GET,
                re.compile(r'https://example\.com/xqueue/get_queuelen/'),
                body=json.dumps({
                    "return_code": 0,
                    "content": 2,
                }),
                content_type='application/json',
                status=200,
            )
            queue_length = len(self.manager)
            self.assertEqual(queue_length, 2)
        run()

    def test_len_fail(self):
        @responses.activate
        def run():
            responses.add(
                responses.GET,
                re.compile(r'https://example\.com/xqueue/get_queuelen/'),
                body=json.dumps({
                    "return_code": 0,
                    "content": "seven",
                }),
                content_type='application/json',
                status=200,
            )
            queue_length = len(self.manager)
            self.assertEqual(queue_length, 0)
        run()
