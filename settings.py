# -*- coding: utf-8 -*-

"""
Settings file for the certificate agent
"""

import json
import os
from logsettings import get_logger_config
from path import path

ROOT_PATH = path(__file__).dirname()
REPO_PATH = ROOT_PATH
ENV_ROOT = REPO_PATH.dirname()
# Override TEMPLATE_DATA_DIR if you have have
# private templates, fonts, etc.
# Needs to be relative to the certificates repo
# root
TEMPLATE_DATA_DIR = 'template_data'

# DEFAULTS
DEBUG = False
LOGGING = get_logger_config(ENV_ROOT,
                            logging_env="dev",
                            local_loglevel="DEBUG",
                            dev_env=True,
                            debug=True)

# Default long names, these can be overridden in
# env.json
#  Full list of courses:
#            'BerkeleyX/CS169.1x/2012_Fall',
#            'BerkeleyX/CS169.2x/2012_Fall',
#            'BerkeleyX/CS188.1x/2012_Fall',
#            'BerkeleyX/CS184.1x/2012_Fall',
#            'HarvardX/CS50x/2012',
#            'HarvardX/PH207x/2012_Fall',
#            'MITx/3.091x/2012_Fall',
#            'MITx/6.002x/2012_Fall',
#            'MITx/6.00x/2012_Fall',
#            'BerkeleyX/CS169/fa12',
#            'BerkeleyX/CS188/fa12',
#            'HarvardX/CS50/2012H',
#            'MITx/3.091/MIT_2012_Fall',
#            'MITx/6.00/MIT_2012_Fall',
#            'MITx/6.002x-EE98/2012_Fall_SJSU',
#            'MITx/6.002x-NUM/2012_Fall_NUM']

# What we support:

CERT_DATA = {
  "edX/Open_DemoX/edx_demo_course" : {
    "LONG_ORG" : "Sample Org",
    "LONG_COURSE" : "Sample course",
    "ISSUED_DATE" : "Jan. 1st, 1970"
  },
}


# Default for the gpg dir
# Specify the CERT_KEY_ID before running the test suite
CERT_GPG_DIR = '{0}/.gnupg'.format(os.environ['HOME'])
# dummy key - https://raw.githubusercontent.com/edx/configuration/master/playbooks/roles/certs/files/example-private-key.txt
CERT_KEY_ID = 'FEF8D954'

# Specify these credentials before running the test suite
# or ensure that your .boto file has write permission
# to the bucket
CERT_AWS_ID = None
CERT_AWS_KEY = None
# Update this with your bucket name
CERT_BUCKET = 'verify-test.edx.org'
CERT_WEB_ROOT = '/var/tmp'
# when set to true this will copy the generated certificate
# to the CERT_WEB_ROOT. This is not something you want to do
# unless you are running your certificate service on a single
# server
COPY_TO_WEB_ROOT = False
S3_UPLOAD = True
# This is the base URL used for CERT uploads to s3
CERT_URL = 'http://{}.s3.amazonaws.com'.format(CERT_BUCKET)
# This is the base URL that will be displayed to the user in the dashboard
# It's different than CERT_URL because because CERT_URL will not have a valid
# SSL certificate.
CERT_DOWNLOAD_URL = 'https://s3.amazonaws.com/{}'.format(CERT_BUCKET)
CERT_VERIFY_URL = 'http://s3.amazonaws.com/{}'.format(CERT_BUCKET)


# load settings from env.json and auth.json
if os.path.isfile(ENV_ROOT / "env.json"):
    with open(ENV_ROOT / "env.json") as env_file:
        ENV_TOKENS = json.load(env_file)
    LOG_DIR = ENV_TOKENS.get('LOG_DIR', '/var/tmp')
    local_loglevel = ENV_TOKENS.get('LOCAL_LOGLEVEL', 'INFO')
    QUEUE_NAME = ENV_TOKENS.get('QUEUE_NAME', 'test-pull')
    QUEUE_URL = ENV_TOKENS.get('QUEUE_URL', 'https://stage-xqueue.edx.org')
    CERT_GPG_DIR = ENV_TOKENS.get('CERT_GPG_DIR', CERT_GPG_DIR)
    CERT_KEY_ID = ENV_TOKENS.get('CERT_KEY_ID', CERT_KEY_ID)
    CERT_BUCKET = ENV_TOKENS.get('CERT_BUCKET', CERT_BUCKET)
    CERT_URL = ENV_TOKENS.get('CERT_URL', CERT_URL)
    CERT_VERIFY_URL = ENV_TOKENS.get('CERT_VERIFY_URL', CERT_VERIFY_URL)
    CERT_DOWNLOAD_URL = ENV_TOKENS.get('CERT_DOWNLOAD_URL', CERT_DOWNLOAD_URL)
    CERT_WEB_ROOT = ENV_TOKENS.get('CERT_WEB_ROOT', CERT_WEB_ROOT)
    COPY_TO_WEB_ROOT = ENV_TOKENS.get('COPY_TO_WEB_ROOT', COPY_TO_WEB_ROOT)
    S3_UPLOAD = ENV_TOKENS.get('S3_UPLOAD', S3_UPLOAD)
    LOGGING = get_logger_config(LOG_DIR,
                                logging_env=ENV_TOKENS['LOGGING_ENV'],
                                local_loglevel=local_loglevel,
                                debug=False,
                                service_variant=os.environ.get('SERVICE_VARIANT', None))
    TEMPLATE_DATA_DIR = ENV_TOKENS.get('TEMPLATE_DATA_DIR', TEMPLATE_DATA_DIR)

if os.path.isfile(ENV_ROOT / "auth.json"):
    with open(ENV_ROOT / "auth.json") as env_file:
        ENV_TOKENS = json.load(env_file)
    QUEUE_USER = ENV_TOKENS.get('QUEUE_USER', 'lms')
    QUEUE_PASS = ENV_TOKENS.get('QUEUE_PASS')
    QUEUE_AUTH_USER = ENV_TOKENS.get('QUEUE_AUTH_USER', '')
    QUEUE_AUTH_PASS = ENV_TOKENS.get('QUEUE_AUTH_PASS', '')
    CERT_AWS_KEY = ENV_TOKENS.get('CERT_AWS_KEY', CERT_AWS_KEY)
    CERT_AWS_ID = ENV_TOKENS.get('CERT_AWS_ID', CERT_AWS_ID)

TEMPLATE_DIR = os.path.join(REPO_PATH, TEMPLATE_DATA_DIR)
