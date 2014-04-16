# Read-only certificate code

This is a copy of the code we use the generate certificates at edX.
Unfortunately, our code is in the same repo as our certificate templates, which
our partners would not like distributed.  The code is not very flexible, but
if there are parts that are helpful for you, please feel free to use them.

We would like to build a better certificate generation facility soon, but in
the meantime, this code might help you.


# Generate edX certificates

This script will continuously monitor an xqueue queue
for the purpose of generating a course certificate for a user.

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
post a result back to the LMS via the xqueue server
indicating there was a problem.

    
    optional arguments:
      -h, --help         show this help message and exit
      --aws-id AWS_ID    AWS ID for write access to the S3 bucket
      --aws-key AWS_KEY  AWS KEY for write access to the S3 bucket


## Generation overview

### State diagram:

    [deleted,error,unavailable] [error,downloadable]
                +                +             +
                |                |             |
                |                |             |
             add_cert       regen_cert     del_cert
                |                |             |
                v                v             v
           [generating]    [regenerating]  [deleting]
                +                +             +
                |                |             |
           certificate      certificate    certificate
             created       removed,created   deleted
                +----------------+-------------+------->[error]
                |                |             |
                |                |             |
                v                v             v
          [downloadable]   [downloadable]  [deleted]
    

This code is responsbile for the
"generating", "regenerating" and "deleting" state
changes in the above diagram.  
When those changes are complete the results are posted
back to the LMS via the xqueue server where the GeneratedCertificate
table will be updated.

If there is an error the "error\_reason" field in the GeneratedCertfificate
table will contain the exception name, error, filename and line number.

## Failure Modes

* A connection to xqueue fails - logs the error, retries
* Fails to make a connection to S3 or the upload fails - logs the error, the certificate state will toggle to error and the reason will be recorded in the db.
* Certificate generation fails - ^^^
* Post to xqueue server fails - This will be logged on the certificate server, the certificate state will remain as it was (and require intervention).
* Post to the LMS from the xqueue server fails - This will be logged on the LMS, the certificate state will remain as it was (and require intervention).


## Installation:

Clone the repo and run certificate\_agent.py
In a production environment this script will run continuously
as an upstart job.

## Configuration:

Normally configuration is read from {auth,env}.json though
all options can be passed in on the commandline.
The two files should be on the same directory level as the
repository.

### env.json example:

    {
        "CERT_ORGS_LONG" : {
          "BerkeleyX" : "the University of California at Berkeley",
          "MITx" : "the Massachusetts University of Technology",
          "HarvardX" : "Harvard University",
          },
          "LOGGING_ENV" : "myhost",
          "LOG_DIR" : "/var/tmp",
          "SYSLOG_SERVER" : "syslog.a.m.i4x.org",
          "QUEUE_NAME" : "myqueue",
          "QUEUE_URL" : "https://sandbox-xqueue.edx.org"
        },
    
    }
    
### auth.json example:
    {
        "QUEUE_USER" : "lms",
        "QUEUE_PASS" : "*****",
        "QUEUE_AUTH_USER" : "***",
        "QUEUE_AUTH_PASS" : "******",
        "CERT_AWS_ID" : "***",
        "CERT_AWS_KEY" : "***"
    }


## Logging:

Logging is setup similar to Django logging, logsettings.py
will generate a configuration dict for logging where in a production
environment all log messages are sent through rsyslog

## Tests:

To run the test suite:

1. Configure your credential information in `settings.py`.  You will need to specify:

        CERT_KEY_ID = # The id for the key which will be used by gpg to sign certificates
        CERT_AWS_ID = # Amazon Web Services ID
        CERT_AWS_KEY = # Amazon Web Services Key
        CERT_BUCKET = # Amazon Web Services S3 bucket name

   It is also fine to have a .boto file or a run this code from a server that has a proper
   IAM role.    

2. To run all of the tests from the `certificates` directory, run:

        nosetests

   Note that this will run tests that will fail unless AWS credentials are setup. To run just
   the tests for local on-disk publishing run:

        nosetests tests.gen_cert_test:test_cert_gen


**Troubleshooting**: If tests fail with errors, try running:

    pip install -r requirements.txt

to install necessary requirements.  

In addition, you must install `gpg`.  See [gnugp](http://www.gnupg.org/)
for instructions.
