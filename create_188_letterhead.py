# -*- coding: utf-8 -*-
from gen_cert import S3_CERT_PATH
from gen_cert import CertificateGen
import os
import shutil
from tests.test_data import NAMES
import settings
from argparse import ArgumentParser, RawTextHelpFormatter
import sys
import json

description = """
This is a standalone utility for generating certficiates.
It will use test data in tests/test_data.py for names and courses
unless --file is passed which is a json file containing names
"""


def parse_args(args=sys.argv[1:]):
    parser = ArgumentParser(description=description,
            formatter_class=RawTextHelpFormatter)
    # global create and delete
    parser.add_argument('-f', '--file',
        help="load date from json")
    return parser.parse_args()


def main():
    """
    Generates samples of the Berkeley 188x letterhead
    Will copy out the pdfs into the letterheads/ dir
    """
    pdf_dir = "/var/tmp/gen_letterheads"
    copy_dir = "/var/tmp/letterheads"

    # Remove files if they exist
    for d in [pdf_dir, copy_dir]:
        if os.path.exists(d):
            shutil.rmtree(d)

    if not os.path.exists(copy_dir):
        os.makedirs(copy_dir)

    download_urls = []

    # only CS188.1x has the letterhead
    course = 'BerkeleyX/CS188.1x/2012_Fall'

    if args.file:
        with open(args.file, 'rb') as f:
            dist = json.loads(f.read())
        new_dist = []
        letterhead = CertificateGen(
                course, settings.CERT_AWS_ID,
                settings.CERT_AWS_KEY, dir_prefix=pdf_dir)
        for entry in dist:
            print entry['name']
            (download_uuid, verify_uuid,
                    download_url) = letterhead.create_and_upload(
                                        entry['name'].encode('utf-8'),
                                        upload=True, cleanup=True,
                                        letterhead=True)
            entry.update({'url': download_url})
            new_dist.append(entry)
        with open('/tmp/distinguished_data', 'wb') as f:
            f.write(json.dumps(new_dist))
    else:

        for name in NAMES:
            letterhead = CertificateGen(
                    course, settings.CERT_AWS_ID,
                    settings.CERT_AWS_KEY, dir_prefix=pdf_dir)
            (download_uuid, verify_uuid,
                    download_url) = letterhead.create_and_upload(
                                        name, upload=True, cleanup=False,
                                        letterhead=True)
            download_urls.append(download_url)
            gen_dir = os.path.join(
                    letterhead.dir_prefix, S3_CERT_PATH, download_uuid)
            copy_dest = '{copy_dir}/{course}-{name}.pdf'.format(
                    copy_dir=copy_dir,
                    name=name.replace(" ", "-").replace("/", "-"),
                    course=course.replace("/", "-"))

            shutil.copyfile('{0}/distinction-letter.pdf'.format(gen_dir),
                    unicode(copy_dest.decode('utf-8')))
            print "Created {0}".format(copy_dest)

        print "\n".join(download_urls)

if __name__ == '__main__':
    args = parse_args()
    main()
