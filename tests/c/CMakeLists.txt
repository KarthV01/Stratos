cmake_minimum_required(VERSION 3.16)
project(stratos_embedded_tests C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    # The official generated dialect intentionally decodes packed wire structs.
    add_compile_options(-Wno-address-of-packed-member)
endif()

enable_testing()

add_library(stratos_smp1 STATIC ../../firmware/smp1/smp1.c)
target_include_directories(stratos_smp1 PUBLIC ../../firmware/smp1)

add_executable(test_smp1 test_smp1.c)
target_link_libraries(test_smp1 PRIVATE stratos_smp1)
add_test(NAME smp1_codec COMMAND test_smp1)

add_library(stratos_mavlink STATIC ../../firmware/mavlink/stratos_mavlink.c)
target_include_directories(stratos_mavlink PUBLIC
    ../../firmware/mavlink
    ../../firmware/mavlink/c_library_v2
)
target_link_libraries(stratos_mavlink PUBLIC stratos_smp1)

add_executable(test_mavlink test_mavlink.c)
target_link_libraries(test_mavlink PRIVATE stratos_mavlink)
add_test(NAME mavlink_ingestion COMMAND test_mavlink)
