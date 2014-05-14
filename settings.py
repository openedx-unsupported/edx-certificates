# -*- coding: utf-8 -*-

"""
Settings file for the certificate agent
"""

import json
import os
import yaml

from logsettings import get_logger_config
from path import path


ROOT_PATH = path(__file__).dirname()
REPO_PATH = ROOT_PATH
ENV_ROOT = REPO_PATH.dirname()

# Override CERT_PRIVATE_DIR if you have have private templates, fonts, etc.
CERT_PRIVATE_DIR = REPO_PATH

# If CERT_PRIVATE_DIR is set in the environment use it

if 'CERT_PRIVATE_DIR' in os.environ:
    CERT_PRIVATE_DIR = path(os.environ['CERT_PRIVATE_DIR'])

# This directory and file must exist in CERT_PRIVATE_DIR
# if you are using custom templates and custom cert config
TEMPLATE_DATA_SUBDIR = 'template_data'
CERT_DATA_FILE = 'cert-data.yml'

# DEFAULTS
DEBUG = False
LOGGING = get_logger_config(ENV_ROOT,
                            logging_env="dev",
                            local_loglevel="INFO",
                            dev_env=True,
                            debug=False)

# Default for the gpg dir
# Specify the CERT_KEY_ID before running the test suite
CERT_GPG_DIR = '{0}/.gnupg'.format(os.environ['HOME'])
# dummy key - https://raw.githubusercontent.com/edx/configuration/master/playbooks/roles/certs/files/example-private-key.txt
CERT_KEY_ID = 'FEF8D954'

# Specify the default name of the certificate PDF
CERT_FILENAME = 'Certificate.pdf'

# Specify these credentials before running the test suite
# or ensure that your .boto file has write permission
# to the bucket.
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
S3_VERIFY_PATH = 'cert'

# A knob to control what certs are called, some places have restrictions on the
# word 'certificate'
CERTS_ARE_CALLED = 'certificate'
CERTS_ARE_CALLED_PLURAL = 'certificates'

# These are initialized below, after the environment is read
CERT_URL = ''
CERT_DOWNLOAD_URL = ''
CERT_VERIFY_URL = ''

# load settings from env.json and auth.json
if os.path.isfile(ENV_ROOT / "env.json"):
    with open(ENV_ROOT / "env.json") as env_file:
        ENV_TOKENS = json.load(env_file)
    LOG_DIR = ENV_TOKENS.get('LOG_DIR', '/var/tmp')
    TMP_GEN_DIR = ENV_TOKENS.get('TMP_GEN_DIR', '/var/tmp')
    local_loglevel = ENV_TOKENS.get('LOCAL_LOGLEVEL', 'INFO')
    QUEUE_NAME = ENV_TOKENS.get('QUEUE_NAME', 'test-pull')
    QUEUE_URL = ENV_TOKENS.get('QUEUE_URL', 'https://stage-xqueue.edx.org')
    CERT_GPG_DIR = ENV_TOKENS.get('CERT_GPG_DIR', CERT_GPG_DIR)
    CERT_KEY_ID = ENV_TOKENS.get('CERT_KEY_ID', CERT_KEY_ID)
    CERT_BUCKET = ENV_TOKENS.get('CERT_BUCKET', CERT_BUCKET)
    CERT_FILENAME = ENV_TOKENS.get('CERT_FILENAME', CERT_FILENAME)
    CERT_URL = ENV_TOKENS.get('CERT_URL', '')
    CERT_DOWNLOAD_URL = ENV_TOKENS.get('CERT_DOWNLOAD_URL', "")
    CERT_VERIFY_URL = ENV_TOKENS.get('CERT_VERIFY_URL', "")
    CERT_WEB_ROOT = ENV_TOKENS.get('CERT_WEB_ROOT', CERT_WEB_ROOT)
    COPY_TO_WEB_ROOT = ENV_TOKENS.get('COPY_TO_WEB_ROOT', COPY_TO_WEB_ROOT)
    S3_UPLOAD = ENV_TOKENS.get('S3_UPLOAD', S3_UPLOAD)
    S3_VERIFY_PATH = ENV_TOKENS.get('S3_VERIFY_PATH', S3_VERIFY_PATH)
    CERTS_ARE_CALLED = ENV_TOKENS.get('CERTS_ARE_CALLED', CERTS_ARE_CALLED)
    CERTS_ARE_CALLED_PLURAL = ENV_TOKENS.get('CERTS_ARE_CALLED_PLURAL', CERTS_ARE_CALLED_PLURAL)
    LOGGING = get_logger_config(LOG_DIR,
                                logging_env=ENV_TOKENS['LOGGING_ENV'],
                                local_loglevel=local_loglevel,
                                debug=False,
                                service_variant=os.environ.get('SERVICE_VARIANT', None))
    CERT_PRIVATE_DIR = ENV_TOKENS.get('CERT_PRIVATE_DIR', CERT_PRIVATE_DIR)

# This is the base URL used for logging CERT uploads to s3
CERT_URL = CERT_URL or 'http://{}.s3.amazonaws.com'.format(CERT_BUCKET)
# This is the base URL that will be displayed to the user in the dashboard
# It's different than CERT_URL because because CERT_URL will not have a valid
# SSL certificate. # FIXME: confirm whether this is true
CERT_DOWNLOAD_URL = CERT_DOWNLOAD_URL or 'https://{}.s3.amazonaws.com'.format(CERT_BUCKET)
CERT_VERIFY_URL = CERT_VERIFY_URL or 'http://{}.s3.amazonaws.com'.format(CERT_BUCKET)

if os.path.isfile(ENV_ROOT / "auth.json"):
    with open(ENV_ROOT / "auth.json") as env_file:
        ENV_TOKENS = json.load(env_file)
    QUEUE_USER = ENV_TOKENS.get('QUEUE_USER', 'lms')
    QUEUE_PASS = ENV_TOKENS.get('QUEUE_PASS')
    QUEUE_AUTH_USER = ENV_TOKENS.get('QUEUE_AUTH_USER', '')
    QUEUE_AUTH_PASS = ENV_TOKENS.get('QUEUE_AUTH_PASS', '')
    CERT_AWS_KEY = ENV_TOKENS.get('CERT_AWS_KEY', CERT_AWS_KEY)
    CERT_AWS_ID = ENV_TOKENS.get('CERT_AWS_ID', CERT_AWS_ID)


# Use the custom CERT_PRIVATE_DIR for paths to the
# template sub directory and the cert data config

TEMPLATE_DIR = CERT_PRIVATE_DIR / TEMPLATE_DATA_SUBDIR

with open(CERT_PRIVATE_DIR / CERT_DATA_FILE) as f:
    CERT_DATA = yaml.load(f.read())
