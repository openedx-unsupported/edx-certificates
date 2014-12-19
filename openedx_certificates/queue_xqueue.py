"""
XQueue management wrapper
"""
import json
import os
import sys
import time

import logging
import requests
from requests.exceptions import ConnectionError, Timeout

import settings
from gen_cert import CertificateGen
from openedx_certificates.exceptions import InvalidReturnCode
from openedx_certificates import strings

logging.config.dictConfig(settings.LOGGING)
LOG = logging.getLogger(__name__)


# TODO: rename class?
# TODO: step should be the only public method
class XQueuePullManager(object):
    """
    Provide an interface to the XQueue server
    """

    def __init__(self, url, name, auth_basic, auth_xqueue, auth_aws):
        """
        Initialize a new XQueuePullManager

        :param url: The base URL of the XQueue server
        :type url: str
        :param name: The name of the XQueue server
        :type name: str
        :param auth_basic: A tuple of (username, password)
        :type auth_basic: tuple
        :param auth_xqueue: A tuple of (username, password)
        :type auth_xqueue: tuple
        :param auth_aws: A tuple of (aws_id, aws_key)
        :type auth_aws: tuple
        """
        self.url = url
        self.name = name
        self.auth_basic = auth_basic
        self.auth_xqueue = auth_xqueue
        self.auth_aws = auth_aws
        self.session = None

    def __len__(self):
        """
        Return the length of the XQueue

        :raises: ConnectionError, Timeout, InvalidReturnCode
        :returns: int -- the XQueue length
        """
        self._try_login()
        response = _request(
            self.session.get,
            self._get_method_url('get_queuelen'),
            params={
                'queue_name': self.name,
            },
        )
        try:
            length = int(response.get('content', 0))
        except ValueError as error:
            length = 0
            LOG.error(strings.ERROR_LEN, error)
        LOG.debug(strings.MESSAGE_LENGTH, length, self)
        return length

    def __str__(self):
        """
        Stringify self as the URL
        """
        return self.url

    def pop(self):
        """
        Get submission from the XQueue server

        :raises: ConnectionError, Timeout, ValueError, KeyError, InvalidReturnCode
        :returns: dict -- a single submission
        """
        self._try_login()
        response = _request(
            self.session.get,
            self._get_method_url('get_submission'),
            params={
                'queue_name': self.name,
            },
        )
        LOG.info(strings.MESSAGE_RESPONSE, response)
        return json.loads(response['content'])

    def put(self, data):
        """
        Post data back to LMS

        :param data: The payload to be posted to the server
        :type data: dict
        :raises: ConnectionError, Timeout, ValueError, InvalidReturnCode
        :returns: dict -- A dictionary of the JSON response
        """
        LOG.info(strings.MESSAGE_POST, data)
        self._try_login()
        response = _request(
            self.session.post,
            self._get_method_url('put_result'),
            data=data,
        )
        LOG.info(strings.MESSAGE_RESPONSE, response)
        return response

    def _get_method_url(self, method):
        """
        Build an XQueue request URL

        :param method: The method to be called on the XQueue server
        :type method: str
        :returns: str -- the method's XQueue URL
        """
        return "{url_base}/xqueue/{method}/".format(
            url_base=self.url,
            method=method,
        )

    def _try_login(self):
        """
        Login to the XQueue server, if not already

        :param auth_basic: A tuple of (username, password)
        :type auth_basic: tuple
        :param auth_xqueue: A tuple of (username, password)
        :type auth_xqueue: tuple
        :raises: ConnectionError, Timeout, ValueError, InvalidReturnCode
        """
        if not self.session:
            self.session = requests.Session()
            self.session.auth = self.auth_basic
            _request(
                self.session.post,
                self._get_method_url('login'),
                data={
                    'username': self.auth_xqueue[0],
                    'password': self.auth_xqueue[1],
                },
            )
        return True

    def _reply(self, header, **kwargs):
        """
        Reply to LMS with creation result

        :param header: A dictionary of request headers
        :type header: dict
        :param kwargs: The request body
        :type header: dict
        """
        data = {
            'xqueue_header': json.dumps(header),
            'xqueue_body': json.dumps(kwargs),
        }
        return self.put(data)

    # TODO: clean up this method
    def step(self, seconds_to_sleep=5, iterations=1):
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
            queue_length = len(self)
            LOG.debug(strings.MESSAGE_ITERATIONS, iterations)
            if not queue_length:
                time.sleep(seconds_to_sleep)
                continue

            certificate_data = self.pop()
            LOG.debug(strings.MESSAGE_GET, certificate_data)
            response, header = _parse_xqueue_response(certificate_data)
            if course_id_previous != response['course_id']:
                course_id_previous = response['course_id']
                try:
                    certificate_generator = CertificateGen(
                        response['course_id'],
                        response['template_pdf'],
                        aws_id=self.auth_aws[0],
                        aws_key=self.auth_aws[1],
                        long_course=response['course_name'],
                        issued_date=response['issued_date'],
                    )
                except (TypeError, ValueError, KeyError, IOError) as error:
                    LOG.critical(strings.ERROR_PARSE, error, certificate_data)
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
                self.handle_exception(error, header, response)
                continue

            self._reply(
                header,
                action=response['action'],
                download_uuid=download_uuid,
                verify_uuid=verify_uuid,
                username=response['username'],
                course_id=response['course_id'],
                url=download_url,
            )

    def handle_exception(self, error, header, response):
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

        self._reply(
            header,
            error=strings.ERROR_PARSE.format(
                error=error,
            ),
            username=response['username'],
            course_id=response['course_id'],
            error_reason=error_reason,
        )


def _validate(response):
    """
    Check for a valid return code in XQueue response

    :param response: The server response
    :type response: dict
    :raises: InvalidReturnCode
    :returns: bool - Whether or not the response is valid
    """
    return_code = response.get('return_code')
    if return_code != 0:
        raise InvalidReturnCode(strings.ERROR_VALIDATE.format(
            return_code,
            response,
        ))
    return True


# TODO: this should handle errors
# and return None when SHTF
def _request(method, url, **kwargs):
    """
    Make a request to the XQueue server

    :param method: The method to be executed by the server
    :type method: str
    :param url: The server URL
    :type url: str
    :raises: ConnectionError, Timeout, ValueError, InvalidReturnCode
    :returns: dict -- A dictionary of the JSON response
    """
    request = method(url, **kwargs)
    response = request.json()
    _validate(response)
    return response


def _parse_xqueue_response(certificate_data):
    header = json.loads(certificate_data.get('xqueue_header', '{}'))
    response = json.loads(certificate_data.get('xqueue_body', '{}'))
    body = {
        key: response.get(key, None)
        for key in [
            'action',
            'username',
            'course_id',
            'course_name',
            'name',
            'template_pdf',
            'grade',
            'issued_date',
            'designation',
            'delete_download_uuid',
            'delete_verify_uuid',
        ]
    }
    return (body, header)
