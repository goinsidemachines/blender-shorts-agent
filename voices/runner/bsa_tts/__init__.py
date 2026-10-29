"""bsa-tts: one command for the free local voices. Each engine runs in its own uv environment."""

# Exit codes (stable; bsa/narrate/local.py relies on them).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2  # bad arguments or lines file
EXIT_GPU_BUSY = 3  # not enough free video memory before starting
EXIT_OOM = 4  # CUDA out of memory while running
EXIT_NOT_INSTALLED = 5  # engine, weights, voice or reference clip missing
