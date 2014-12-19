# -*- coding: utf-8 -*-
import os
import sys
import time

import logging

import settings
from gen_cert import CertificateGen
from openedx_certificates import strings

logging.config.dictConfig(settings.LOGGING)
LOG = logging.getLogger(__name__)


class XQueueMonitor(object):
    def __init__(self, xqueue, auth_aws, seconds_to_sleep=5):
        """
        :param seconds_to_sleep: Seconds to sleep between failed requests
        :type seconds_to_sleep: int
        """
        self.xqueue = xqueue
        self.auth_aws = auth_aws
        self.seconds_to_sleep = seconds_to_sleep

    def process(self):
        """
        Process the XQueue
        """
        course_id_previous = None
        certificate_generator = None
        while True:
            for response in self.xqueue:
                header, body = response
                if course_id_previous != body['course_id']:
                    course_id_previous = body['course_id']
                    certificate_generator = self._get_certificate_generator(header, body)
                self._create_and_upload(certificate_generator, header, body)
            time.sleep(self.seconds_to_sleep)

    def _create_and_upload(self, certificate_generator, header, body):
        LOG.info(
            strings.MESSAGE_GENERATE,
            body['username'].encode('utf-8'),
            body['name'].encode('utf-8'),
            body['course_id'].encode('utf-8'),
            body['grade'],
        )
        try:
            (download_uuid, verify_uuid, download_url) = certificate_generator.create_and_upload(
                body['name'].encode('utf-8'),
                grade=body['grade'],
                designation=body['designation'],
            )
        except Exception as error:
            self._handle_exception(error, header, body)
            return None
        return self.xqueue.pop(
            header,
            action=body['action'],
            download_uuid=download_uuid,
            verify_uuid=verify_uuid,
            username=body['username'],
            course_id=body['course_id'],
            url=download_url,
        )

    def _get_certificate_generator(self, header, body):
        try:
            certificate_generator = CertificateGen(
                body['course_id'],
                body['template_pdf'],
                aws_id=self.auth_aws[0],
                aws_key=self.auth_aws[1],
                long_course=body['course_name'],
                issued_date=body['issued_date'],
            )
        except (TypeError, ValueError, KeyError, IOError) as error:
            LOG.critical(strings.ERROR_PARSE, error, (header, body))
            certificate_generator = None
        return certificate_generator

    def _handle_exception(self, error, header, body):
        """
        # if anything goes wrong during the generation of the
        # pdf we will let the LMS know so it can be
        # re-submitted, the LMS will update the state to error
        """
        exc_type, ___, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        error_reason = strings.ERROR_EXCEPTION.format(
            username=body['username'],
            course_id=body['course_id'],
            exception_type=exc_type,
            exception=error,
            file_name=fname,
            line_number=exc_tb.tb_lineno,
        )
        LOG.error(strings.ERROR_GENERATE, error_reason)
        self.xqueue.push(
            header,
            error=strings.ERROR_PARSE.format(
                error=error,
            ),
            username=body['username'],
            course_id=body['course_id'],
            error_reason=error_reason,
        )
