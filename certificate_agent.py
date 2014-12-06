from argparse import ArgumentParser, RawTextHelpFormatter
import logging.config
import json
import sys
import os
import time
import settings
from queue import XQueuePullManager
from gen_cert import CertificateGen

logging.config.dictConfig(settings.LOGGING)
log = logging.getLogger('certificates: ' + __name__)


# how long to wait in seconds after an xqueue poll
SLEEP_TIME = 5


def parse_args(args=sys.argv[1:]):
    parser = ArgumentParser(description="""

    Generate edX certificates
    -------------------------

    This script will continuously monitor a queue
    for certificate generation, it does the following:

        * Connect to the xqueue server
        * Pull a single certificate request
        * Process the request
        * Post a result back to the xqueue server

    A global exception handler will catch any error
    during the certificate generation process and
    post a result back to the LMS indicating there
    was a problem.

    """, formatter_class=RawTextHelpFormatter)

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
    return parser.parse_args()


def main():

    manager = XQueuePullManager(settings.QUEUE_URL, settings.QUEUE_NAME,
                                settings.QUEUE_AUTH_USER,
                                settings.QUEUE_AUTH_PASS,
                                settings.QUEUE_USER, settings.QUEUE_PASS)
    last_course = None  # The last course_id we generated for
    cert = None  # A CertificateGen instance for a particular course

    while True:

        if manager.get_length() == 0:
            log.debug("{0} has no jobs".format(str(manager)))
            time.sleep(SLEEP_TIME)
            continue
        else:
            log.debug('queue length: {0}'.format(manager.get_length()))

        xqueue_body = {}
        xqueue_header = ''
        action = ''
        username = ''
        grade = None
        course_id = ''
        course_name = ''
        template_pdf = None
        name = ''

        certdata = manager.get_submission()
        log.debug('xqueue response: {0}'.format(certdata))
        try:
            xqueue_body = json.loads(certdata['xqueue_body'])
            xqueue_header = json.loads(certdata['xqueue_header'])
            action = xqueue_body['action']
            username = xqueue_body['username']
            course_id = xqueue_body['course_id']
            course_name = xqueue_body['course_name']
            name = xqueue_body['name']
            template_pdf = xqueue_body.get('template_pdf', None)
            grade = xqueue_body.get('grade', None)
            issued_date = xqueue_body.get('issued_date', None)
            designation = xqueue_body.get('designation', None)
            if last_course != course_id:
                cert = CertificateGen(course_id, template_pdf, aws_id=args.aws_id, aws_key=args.aws_key, long_course=course_name, issued_date=issued_date)
                last_course = course_id
            if action in ['remove', 'regen']:
                cert.delete_certificate(xqueue_body['delete_download_uuid'],
                                        xqueue_body['delete_verify_uuid'])
                if action in ['remove']:
                    continue

        except (TypeError, ValueError, KeyError, IOError) as e:
            log.critical('Unable to parse queue submission ({0}) : {1}'.format(e, certdata))
            if settings.DEBUG:
                raise
            else:
                continue

        try:
            log.info(
                "Generating certificate for {username} ({name}), "
                "in {course_id}, with grade {grade}".format(
                    username=username.encode('utf-8'),
                    name=name.encode('utf-8'),
                    course_id=course_id.encode('utf-8'),
                    grade=grade,
                )
            )
            (download_uuid,
             verify_uuid,
             download_url) = cert.create_and_upload(name.encode('utf-8'), grade=grade, designation=designation)

        except Exception as e:
            # global exception handler, if anything goes wrong
            # during the generation of the pdf we will let the LMS
            # know so it can be re-submitted, the LMS will update
            # the state to error

            # get as much info as possible about the exception
            # for the post back to the LMS

            exc_type, exc_obj, exc_tb = sys.exc_info()
            fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
            error_reason = (
                "({username} {course_id}) "
                "{exception_type}: {exception}: "
                "{file_name}:{line_number}".format(
                    username=username,
                    course_id=course_id,
                    exception_type=exc_type,
                    exception=e,
                    file_name=fname,
                    line_number=exc_tb.tb_lineno,
                )
            )

            log.critical(
                'An error occurred during certificate generation {reason}'.format(
                    reason=error_reason,
                )
            )

            xqueue_reply = {
                'xqueue_header': json.dumps(xqueue_header),
                'xqueue_body': json.dumps({
                    'error': 'There was an error processing the certificate request: {error}'.format(
                        error=e,
                    ),
                    'username': username,
                    'course_id': course_id,
                    'error_reason': error_reason,
                }),
            }
            manager.respond(xqueue_reply)
            if settings.DEBUG:
                raise
            else:
                continue

        # post result back to the LMS
        xqueue_reply = {
            'xqueue_header': json.dumps(xqueue_header),
            'xqueue_body': json.dumps({
                'action': action,
                'download_uuid': download_uuid,
                'verify_uuid': verify_uuid,
                'username': username,
                'course_id': course_id,
                'url': download_url,
            }),
        }
        log.info("Posting result to the LMS: {0}".format(xqueue_reply))
        manager.respond(xqueue_reply)


if __name__ == '__main__':
    args = parse_args()
    main()
