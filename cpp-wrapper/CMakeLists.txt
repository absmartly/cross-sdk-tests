cmake_minimum_required(VERSION 3.16)
project(cpp-wrapper LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)

FetchContent_Declare(
  httplib
  URL https://github.com/yhirose/cpp-httplib/archive/refs/tags/v0.15.3.tar.gz
)
FetchContent_MakeAvailable(httplib)

set(ABSMARTLY_BUILD_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../cpp-sdk ${CMAKE_CURRENT_BINARY_DIR}/cpp-sdk)

add_executable(cpp-wrapper server.cpp)
target_link_libraries(cpp-wrapper PRIVATE absmartly-sdk httplib::httplib)
