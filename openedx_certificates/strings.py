"""
Define custom error and message strings
"""

ERROR_GENERATE = "An error occurred during certificate generation: %s"
ERROR_EXCEPTION = (
    "({username} {course_id}) {exception_type}: "
    "{exception}: {file_name}:{line_number}"
)
ERROR_LEN = "Unable to get queue length: %s"
ERROR_PARSE = "Unable to parse queue submission (%s): %s"
ERROR_PROCESS = "There was an error processing the certificate request: {error}"
ERROR_VALIDATE = "Invalid return code ({0}): {1}"

MESSAGE_GENERATE = (
    "Generating certificate for user %s (%s), "
    "in %s, with grade %s"
)
MESSAGE_GET = "XQueue response: %s"
MESSAGE_ITERATIONS = "%s iterations remaining"
MESSAGE_LENGTH = "queue length: %s: %s"
MESSAGE_POST = "Posting result to the LMS: %s"
MESSAGE_RESPONSE = 'Response: %s'
