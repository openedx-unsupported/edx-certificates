# -*- coding: utf-8 -*-
"""
This is a standalone utility for generating certficiates.
It will use test data in tests/test_data.py for names and courses.
PDFs by default will be dropped in TMP_GEN_DIR for review
"""
from argparse import ArgumentParser, RawTextHelpFormatter
import csv
import logging
import os
import random
import shutil
import sys

from gen_cert import CertificateGen, S3_CERT_PATH, TARGET_FILENAME, TMP_GEN_DIR
import settings
from tests.test_data import NAMES


logging.config.dictConfig(settings.LOGGING)
LOG = logging.getLogger('certificates.create_pdfs')

description = """
  Sample certificate generator
"""

stanford_cme_titles = (('AuD', 'AuD'),
                       ('DDS', 'DDS'),
                       ('DO', 'DO'),
                       ('MD', 'MD'),
                       ('MD,PhD', 'MD,PhD'),
                       ('MBBS', 'MBBS'),
                       ('NP', 'NP'),
                       ('PA', 'PA'),
                       ('PharmD', 'PharmD'),
                       ('PhD', 'PhD'),
                       ('RN', 'RN'),
                       ('Other', 'Other'),
                       ('None', 'None'),
                       (None, None))


def parse_args(args=sys.argv[1:]):
    parser = ArgumentParser(description=description,
                            formatter_class=RawTextHelpFormatter)

    parser.add_argument('-c', '--course-id', help='optional course-id')
    parser.add_argument('-n', '--name', help='optional name for the cert')
    parser.add_argument('-t', '--template-file', help='optional template file')
    parser.add_argument('-o', '--long-org', help='optional long org')
    parser.add_argument('-l', '--long-course', help='optional long course')
    parser.add_argument('-i', '--issued-date', help='optional issue date')
    parser.add_argument('-U', '--no-upload', help='skip s3 upload step', action="store_true")
    parser.add_argument('-R', '--random-title', help='add random title to name')
    parser.add_argument('-f', '--input-file',
                        help='optional input file for names, one name per line')
    parser.add_argument(
        '-r',
        '--report-file',
        help='optional report file for generated output',
    )
    parser.add_argument('-G', '--grade-text',
                        help='optional grading label to apply')

    return parser.parse_args()


def main():
    """
    Generates some pfds using each template
    for different names for review in a pdf
    viewer.
    Will copy out the pdfs into the certs/ dir
    """
    pdf_dir = TMP_GEN_DIR
    copy_dir = TMP_GEN_DIR + "+copy"

    # Remove files if they exist
    for d in [pdf_dir, copy_dir]:
        if os.path.exists(d):
            shutil.rmtree(d)

    if not os.path.exists(copy_dir):
        os.makedirs(copy_dir)

    certificate_data = []

    if args.course_id:
        course_list = [args.course_id]
    else:
        course_list = settings.CERT_DATA.keys()

    upload_files = True
    if args.no_upload:
        upload_files = False

    for course in course_list:
        if args.name:
            name_list = [args.name]
        elif args.input_file:
            with open(args.input_file) as f:
                name_list = [line.rstrip() for line in f.readlines()]
        else:
            name_list = NAMES

        for name in name_list:
            cert = CertificateGen(
                course,
                args.template_file,
                aws_id=settings.CERT_AWS_ID,
                aws_key=settings.CERT_AWS_KEY,
                dir_prefix=pdf_dir,
                long_org=args.long_org,
                long_course=args.long_course,
                issued_date=args.issued_date,
            )
            title = None
            if args.random_title:
                title = random.choice(stanford_cme_titles)[0]
                print "generating random title", name, title
            grade = None
            if args.grade_text:
                grade = args.grade_text
            (download_uuid, verify_uuid,
                download_url) = cert.create_and_upload(name, upload=upload_files, copy_to_webroot=False,
                                                       cleanup=False, designation=title, grade=grade)
            certificate_data.append((name, course, long_org, long_course, download_url))
            gen_dir = os.path.join(cert.dir_prefix, S3_CERT_PATH, download_uuid)
            copy_dest = '{copy_dir}/{course}-{name}.pdf'.format(
                copy_dir=copy_dir,
                name=name.replace(" ", "-").replace("/", "-"),
                course=course.replace("/", "-"))

            try:
                shutil.copyfile('{0}/{1}'.format(gen_dir, TARGET_FILENAME),
                                unicode(copy_dest.decode('utf-8')))
            except Exception, msg:
                # Sometimes we have problems finding or creating the files to be copied;
                # the following lines help us debug this case
                print msg
                print "%s\n%s\n%s\n%s\n%s\n%s" % (name, download_uuid, verify_uuid, download_uuid, gen_dir, copy_dest)
                raise
            print "Created {0}".format(copy_dest)

    should_write_report_to_stdout = True
    if args.report_file:
        try:
            with open(args.report_file, 'wb') as file_report:
                csv_writer = csv.writer(file_report, quoting=csv.QUOTE_ALL)
                csv_writer.writerows(certificate_data)
            should_write_report_to_stdout = False
        except IOError as error:
            LOG.error("Unable to open report file: %s", error)
    if should_write_report_to_stdout:
        for row in certificate_data:
            print '\t'.join(row)

if __name__ == '__main__':
    args = parse_args()
    main()
