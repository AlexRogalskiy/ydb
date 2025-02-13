LIBRARY()

PROVIDES(test_framework)

PEERDIR(
    contrib/libs/libxml
    library/cpp/colorizer
    library/cpp/dbg_output
    library/cpp/diff
    library/cpp/json/writer
    library/cpp/testing/common
    library/cpp/testing/hook
)

SRCS(
    gtest.cpp
    checks.cpp
    junit.cpp
    plugin.cpp
    registar.cpp
    tests_data.cpp
    utmain.cpp
)

END()

RECURSE_FOR_TESTS(
    fat
    pytests
    ut
)
