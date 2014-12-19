#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate edX certificates
-------------------------

This script will continuously monitor a queue for certificate
generation, it does the following:

    * Connect to the xqueue server
    * Pull a single certificate request
    * Process the request
    * Post a result back to the xqueue server

A global exception handler will catch any error during the certificate
generation process and post a result back to the LMS indicating there
was a problem.
"""

import sys

from argparse import ArgumentParser, RawTextHelpFormatter

from openedx_certificates.monitor import XQueueMonitor
from openedx_certificates.queue_xqueue import XQueuePullManager
import settings


def parse_args():
    parser = ArgumentParser(
        description=__doc__,
        formatter_class=RawTextHelpFormatter,
    )
    parser.add_argument(
        '--aws-id',
        default=settings.CERT_AWS_ID,
        help='AWS ID for write access to the S3 bucket',
    )
    parser.add_argument(
        '--aws-key',
        default=settings.CERT_AWS_KEY,
        help='AWS KEY for write access to the S3 bucket',
    )
    parser.add_argument(
        '--basic-auth-username',
        default=settings.QUEUE_AUTH_USER,
        help='Username for basic HTTP authentication',
    )
    parser.add_argument(
        '--basic-auth-password',
        default=settings.QUEUE_AUTH_PASS,
        help='Password for basic HTTP authentication',
    )
    parser.add_argument(
        '--xqueue-auth-username',
        default=settings.QUEUE_USER,
        help='Username for XQueue authentication',
    )
    parser.add_argument(
        '--xqueue-auth-password',
        default=settings.QUEUE_PASS,
        help='Password for XQueue authentication',
    )
    parser.add_argument(
        '--xqueue-url',
        default=settings.QUEUE_URL,
        help='URL for XQueue server',
    )
    parser.add_argument(
        '--xqueue-name',
        default=settings.QUEUE_NAME,
        help='Name for XQueue bucket',
    )
    parser.add_argument(
        '--sleep-seconds',
        default=5,
        help='Number of seconds to sleep when XQueue is empty',
    )
    return parser.parse_args()


def main(args):
    manager = XQueuePullManager(
        args.xqueue_url,
        args.xqueue_name,
        (args.basic_auth_username, args.basic_auth_password),
        (args.xqueue_auth_username, args.xqueue_auth_password),
    )
    auth_aws = (args.aws_id, args.aws_key)
    seconds_to_sleep = float(args.sleep_seconds)
    iterations = float('inf')
    monitor = XQueueMonitor(manager, auth_aws)
    monitor.step(seconds_to_sleep, iterations)


if __name__ == '__main__':
    args = parse_args()
    main(args)
