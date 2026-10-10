# CI test runner image for Linux.
# Runs the full test suite matching the GitHub Actions CI environment.
# Used by `tools/slice_check.py` to run tests over SSH with Docker `--init`
# so that pytest is not PID 1, process groups are handled correctly, and zombies are reaped.
#
# Build command (context is the REPOSITORY ROOT: requirements-dev.txt is copied
# from there, so one version list serves CI, the image and slice_check):
#   docker build -t reelsi-ci:py310 -f tools/docker/ci.Dockerfile .

FROM python:3.10
RUN apt-get update -qq && apt-get install -y -qq ffmpeg curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y -qq nodejs && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0 --extra-index-url https://download.pytorch.org/whl/cpu
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt
RUN pip install --no-cache-dir flask numpy scipy soundfile fonttools "pillow>=10.0" pyarrow "zstandard>=0.22"
WORKDIR /src
