# -*- coding: utf-8 -*-
# TODO: clean up this method
import os
import sys
import time

import logging

import settings
from gen_cert import CertificateGen
from openedx_certificates import strings

logging.config.dictConfig(settings.LOGGING)
LOG = logging.getLogger(__name__)


def step(xqueue, seconds_to_sleep=5, iterations=1):
    """
    Process the XQueue

    :param seconds_to_sleep: Seconds to sleep between failed requests
    :type seconds_to_sleep: int
    :param iterations: Number of times to poll the server
    :type iterations: int
    :raises: only in debug mode
    """
    course_id_previous = None
    while iterations > 0:
        iterations -= 1
        queue_length = len(xqueue)
        LOG.debug(strings.MESSAGE_ITERATIONS, iterations)
        if not queue_length:
            time.sleep(seconds_to_sleep)
            continue

        response, header = xqueue.peek()
        LOG.debug(strings.MESSAGE_GET, (response, header))
        if course_id_previous != response['course_id']:
            course_id_previous = response['course_id']
            try:
                certificate_generator = CertificateGen(
                    response['course_id'],
                    response['template_pdf'],
                    aws_id=xqueue.auth_aws[0],
                    aws_key=xqueue.auth_aws[1],
                    long_course=response['course_name'],
                    issued_date=response['issued_date'],
                )
            except (TypeError, ValueError, KeyError, IOError) as error:
                LOG.critical(strings.ERROR_PARSE, error, (response, header))
                if settings.DEBUG:
                    raise
                else:
                    continue

        LOG.info(
            strings.MESSAGE_GENERATE,
            response['username'].encode('utf-8'),
            response['name'].encode('utf-8'),
            response['course_id'].encode('utf-8'),
            response['grade'],
        )
        try:
            (download_uuid, verify_uuid, download_url) = certificate_generator.create_and_upload(
                response['name'].encode('utf-8'),
                grade=response['grade'],
                designation=response['designation'],
            )
        except Exception as error:
            handle_exception(xqueue, error, header, response)
            continue

        xqueue.pop(
            header,
            action=response['action'],
            download_uuid=download_uuid,
            verify_uuid=verify_uuid,
            username=response['username'],
            course_id=response['course_id'],
            url=download_url,
        )


def handle_exception(xqueue, error, header, response):
    """
    # if anything goes wrong during the generation of the
    # pdf we will let the LMS know so it can be
    # re-submitted, the LMS will update the state to error
    """
    exc_type, ___, exc_tb = sys.exc_info()
    fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
    error_reason = strings.ERROR_EXCEPTION.format(
        username=response['username'],
        course_id=response['course_id'],
        exception_type=exc_type,
        exception=error,
        file_name=fname,
        line_number=exc_tb.tb_lineno,
    )

    if settings.DEBUG:
        LOG.critical(strings.ERROR_GENERATE, error_reason)
        raise
    else:
        LOG.error(strings.ERROR_GENERATE, error_reason)

    xqueue.push(
        header,
        error=strings.ERROR_PARSE.format(
            error=error,
        ),
        username=response['username'],
        course_id=response['course_id'],
        error_reason=error_reason,
    )
