# edx-certificates

This is the code we use the generate certificates at edX.



# Generate edX certificates

This script will continuously monitor an xqueue queue
for the purpose of generating a course certificate for a user.


Generating sample certificates
-------------------------

1. Create a new python virtualenv 
    ```shell
    mkvirtualenv certificates
    ```

2. Clone the certificate repo 
    ```shell
    git clone https://github.com/edx/edx-certificates.git
    ```

3. Clone the internal certificate repo for templates and private data (optional) 
    ```shell
    git clone git@github.com:edx/edx-certificates-internal
    ```

4. Install the python requirements into the virtualenv 
    ```shell
    pip install -r edx-certificates/requirements.txt
    ```

5. In order to generate sample certificates that are uploaded to S3 you will need access to the _verify-test_ bucket, create a `~/.boto` file in your home directory
    ```ini
    [Credentials]
    aws_access_key_id = *****
    aws_secret_access_key = ****
    ```

    - *Or* for edX use the `boto.example` in the `${CERT_PRIVATE_DIR}` repo:

        ```shell
        cp edx-certificates-internal/boto.example ~/.boto
        ```

6. Set an environment variable to point to the internal repo for certificate templates 
    ```shell
    export CERT_PRIVATE_DIR=/path/to/edx-certificates-internal
    ```

7. In the edx-certificates directory generate a sample certificate:
    ```shell
    cd edx-certificates
    python create_pdfs.py \
        --course-id 'edX/DemoX/Demo_Course' \
        --name 'Guido' \
        --no-upload \
        ;
    ```

    - `edX/DemoX/Demo_Course` should be a valid course id found in
      `${CERT_PRIVATE_DIR}`

    - View all options with:
        ```shell
        python create_pdfs.py --help
        ```

## Behavioral Overview

The `certificate_agent.py` script will continuously monitor a queue for 
certificate generation, it does the following:

* Connect to the xqueue server
* Poll for a single certificate request
* If it finds one, it:
  * Processes the request
  * Post a results json back to the xqueue server

A global exception handler will catch any error during the certificate
generation process and post a result back to the LMS via the xqueue server
indicating there was a problem.

    optional arguments:
      -h, --help                show this help message and exit
      --aws-id AWS_ID           AWS ID for write access to the S3 bucket
      --aws-key AWS_KEY         AWS KEY for write access to the S3 bucket
      --basic-auth-username     Username for basic HTTP authentication
      --basic-auth-password     Password for basic HTTP authentication
      --xqueue-auth-username    Username for XQueue authentication
      --xqueue-auth-password    Password for XQueue authentication
      --xqueue-url              URL for XQueue server
      --xqueue-name             Name for XQueue bucket
      --sleep-seconds           Number of seconds to sleep when XQueue is empty


## Generation overview

TODO

## Logging

Logging is setup similar to Django logging, logsettings.py
will generate a configuration dict for logging where in a production
environment all log messages are sent through rsyslog

## Tests

To run the test suite:

1. Configure your credential information in `settings.py`.  You will need to specify:

        CERT_KEY_ID = # The id for the key which will be used by gpg to sign certificates
        CERT_AWS_ID = # Amazon Web Services ID
        CERT_AWS_KEY = # Amazon Web Services Key
        CERT_BUCKET = # Amazon Web Services S3 bucket name

   It is also acceptable to leave the AWS KEY and ID values as none and instead
   use .boto file or run this code from a server that has an
   IAM role that gives it write access to the bucket in the configuration.

2. To run all of the tests from the `certificates` directory, run:

        nosetests

   These are more integration tests than unit tests, and will be exercising your 
   certificate configuration, your file pathing, and your S3 credentials.  Some tests
   may fail, but the code may still be working properly; you'll have to investigate to
   discover what the failed test is diagnostic of. To run just the tests for local 
   on-disk publishing run:

        nosetests tests.gen_cert_test:test_cert_gen


**Troubleshooting**: 

  * If tests fail with errors, try running:
     ```shell
     pip install -r requirements.txt
     ```
    to install necessary requirements.  

  * If your verification pages are wrong or gnupg starts throwing errors, you
    may not have `gpg` installed. See [gnupg](http://www.gnupg.org/) for help
    installing.

  * If you run `create_pdf.py` and get unicode errors, you may need to set the
    LC_ALL environment variable. To set it permanently, like from your 
    ~/.bashrc, try:
    ```shell
    export LC_ALL=en_US.UTF-8
    ```
    But if you only want to set it for one test run, you could say:
    ```shell
    LC_ALL=en_US.UTF-8 python ./create_pdf.py
    ```

  * If you are running on a Linux virtual machine being hosted by MacOS, and
    your git checkout is being NFS mounted, you may have library import errors
    because of Mac case-folding semantic preservation by Python's pathlib. Try
    doing a git checkout inside the vm that isn't exported from MacOS.

## Roadmap/TODO/Future Features

* Paralellism - Certification should be embarassingly parallel, except we deal
  with xqueue, which lacks atomic pop(). If we ever refactor queue.py to use
  raw celery queues or similar, we should also actualize parallel
  certification.

* Dynamic scaling and placement of signatures from scanned bitmaps, making cert
  rendering completely dynamic and freeing us from the tyranny of template
  preparation. (Freeing us from the tyranny of configuration preparation would
  come in a future PR.)

* Kill XQueue - nobody really likes xqueue. Several ops people have expressed a
  desire to see it replaced by smarter intermediate layers that use celery task
  queues or similar. This repo should be sufficiently modular that only
  queue.py should need to be changed to work this way.

