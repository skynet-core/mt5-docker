execute_process(COMMAND "${CMAKE_COMMAND}" -E env ${ENVIRONMENT} "${PROGRAM}" ${CASE} RESULT_VARIABLE status
    OUTPUT_VARIABLE output ERROR_VARIABLE error)
if(NOT "${status}" STREQUAL "${EXPECT}")
    message(FATAL_ERROR "${CASE}: expected status ${EXPECT}, got ${status}: ${error}${output}")
endif()
if(NOT "${error}${output}" MATCHES "${MATCH}")
    message(FATAL_ERROR "${CASE}: missing diagnostic ${MATCH}: ${error}${output}")
endif()
