# -*- coding: utf-8 -*-
"""
This is a standalone utility for generating certficiates.
It will use test data in tests/test_data.py for names and courses.
PDFs by default will be dropped in /var/tmp/certs for review
"""
from gen_cert import S3_CERT_PATH
from gen_cert import CertificateGen
import os
import shutil
from tests.test_data import NAMES
import settings
import sys
import csv
from argparse import ArgumentParser, RawTextHelpFormatter

description = """
  Sample certificate generator
"""


def parse_args(args=sys.argv[1:]):
    parser = ArgumentParser(description=description,
                            formatter_class=RawTextHelpFormatter)

    parser.add_argument('-c', '--course-id', help='optional course-id')
    parser.add_argument('-n', '--name', help='optional name for the cert')
    parser.add_argument('-t', '--template-file', help='optional template file')
    parser.add_argument('-o', '--long-org', help='optional long org')
    parser.add_argument('-l', '--long-course', help='optional long course')
    parser.add_argument('-i', '--issued-date', help='optional issue date')
    parser.add_argument('-f', '--input-file',
                        help='optional input file for names, one name per line')
    parser.add_argument('-w', '--output-file',
                        help='optional output file for certificate')

    return parser.parse_args()


def main():
    """
    Generates some pfds using each template
    for different names for review in a pdf
    viewer.
    Will copy out the pdfs into the certs/ dir
    """
    pdf_dir = "/var/tmp/gen_certs"
    copy_dir = "/var/tmp/certs"
    if args.output_file:
        # ensure we can open the output file
        output_f = open(args.output_file, 'aw')

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
            (download_uuid, verify_uuid,
                download_url) = cert.create_and_upload(
                    name, upload=True, copy_to_webroot=False, cleanup=False)
            certificate_data.append([name, download_url])
            gen_dir = os.path.join(
                cert.dir_prefix, S3_CERT_PATH, download_uuid)
            copy_dest = '{copy_dir}/{course}-{name}.pdf'.format(
                copy_dir=copy_dir,
                name=name.replace(" ", "-").replace("/", "-"),
                course=course.replace("/", "-"))

            shutil.copyfile('{0}/Certificate.pdf'.format(gen_dir),
                            unicode(copy_dest.decode('utf-8')))
            print "Created {0}".format(copy_dest)

    for row in certificate_data:
        print '\t'.join(row)
    if args.output_file:
        certificate_writer = csv.writer(output_f, quoting=csv.QUOTE_MINIMAL)
        for row in certificate_data:
            certificate_writer.writerow(row)
        output_f.close()

if __name__ == '__main__':
    args = parse_args()
    main()
