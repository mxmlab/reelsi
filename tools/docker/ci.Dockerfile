# CI test runner image for Linux.
# Runs the full test suite matching the GitHub Actions CI environment.
# Used by `tools/slice_check.py` to run tests over SSH with Docker `--init`
# so that pytest is not PID 1, process groups are handled correctly, and zombies are reaped.
#
# Build command:
#   docker build -t reelsi-ci:py310 -f tools/docker/ci.Dockerfile tools/docker

FROM python:3.10
RUN apt-get update -qq && apt-get install -y -qq ffmpeg curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y -qq nodejs && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch torchaudio --extra-index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir flask numpy scipy soundfile pytest pytest-timeout pytest-cov ruff "mypy>=1.11,<2" fonttools "pillow>=10.0" pyarrow "zstandard>=0.22"
WORKDIR /src
