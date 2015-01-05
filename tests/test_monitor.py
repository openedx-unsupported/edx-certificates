# -*- coding: utf-8 -*-
import json
import responses
import re
import unittest

import settings
from openedx_certificates.monitor import QueueMonitor


# TODO: move duplicate/mock methods to helper class/module
class MonitorTest(unittest.TestCase):
    def setUp(self):
        args = {
            'auth_aws_id': None,
            'auth_aws_key': None,
            'sleep_seconds': 0,
            'xqueue_url': 'https://example.com',
            'xqueue_name': settings.QUEUE_NAME,
            'basic_auth_username': settings.QUEUE_AUTH_USER,
            'basic_auth_password': settings.QUEUE_AUTH_PASS,
            'xqueue_auth_username': settings.QUEUE_USER,
            'xqueue_auth_password': settings.QUEUE_PASS,
        }
        self.monitor = QueueMonitor(args)
        self._mock_login_good()

    @responses.activate
    def test_process_none(self):
        self.monitor.process(0)

    @responses.activate
    def test_process_one(self):
        self._mock_len(1)
        self._mock_pop_good()
        self.monitor.process(1)

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
