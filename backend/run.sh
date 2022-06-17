#!/bin/bash
export XLA_PYTHON_CLIENT_ALLOCATOR=platform
XLA_FLAGS='--xla_gpu_force_compilation_parallelism=1 --xla_gpu_strict_conv_algorithm_picker=false' python app_8GB_VRAM.py 8085
