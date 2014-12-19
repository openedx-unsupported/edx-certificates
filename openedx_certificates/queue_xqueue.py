"""
XQueue management wrapper
"""
import json

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
class XQueuePullManager(object):
    """
    Provide an interface to the XQueue server
    """

    def __init__(self, url, name, auth_basic, auth_xqueue):
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
        """
        self.url = url
        self.name = name
        self.auth_basic = auth_basic
        self.auth_xqueue = auth_xqueue
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

    def peek(self):
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
        content = json.loads(response['content'])
        return _parse_xqueue_response(content)

    def push(self, header, **kwargs):
        self._post(header, **kwargs)

    def pop(self, header, **kwargs):
        self._post(header, **kwargs)

    def _post(self, header, **kwargs):
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
    return (header, body)
