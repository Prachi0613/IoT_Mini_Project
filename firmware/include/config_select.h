#pragma once

#if __has_include("config.h")
#include "config.h"
#else
#include "config.example.h"
#warning "Using config.example.h; create include/config.h before hardware use"
#endif
