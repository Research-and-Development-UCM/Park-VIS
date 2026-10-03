# Stage 1 — Build the Vue SPA frontend
FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2 — Runtime image with CUDA + cuDNN
FROM nvidia/cuda:12.6.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1

ARG APP_VERSION=v-dev
ENV PARK_VIS_VERSION=$APP_VERSION

# Install system deps: Python 3.12, GStreamer, PyGObject, OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    libgstreamer1.0-0 \
    libgstreamer-plugins-base1.0-0 \
    libgirepository-1.0-1 \
    libglib2.0-0 \
    libcairo2 \
    libcairo-gobject2 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python deps first (layer caching)
COPY requirements.txt ./

# Set up venv and install Python packages
RUN python3 -m venv --system-site-packages /app/venv && \
    /app/venv/bin/pip install --no-cache-dir -r requirements.txt && \
    /app/venv/bin/pip install --no-cache-dir http://download.lotvulture.com/dist/vulturevision/vulturevision-1.0.0-cp312-cp312-linux_x86_64.whl

# Copy the application code
COPY backend/ ./backend/
COPY rollinglmdb/ ./rollinglmdb/
COPY main.py ./
COPY EULA.txt ./

# Copy the pre-built frontend from stage 1
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

# Persist all runtime state (SQLite DB, LMDB blob store, logs, .env
# overrides) under /var/lib/park-vis. The compose file bind-mounts a
# named volume over this directory so state survives container
# restarts and image upgrades. Declaring the directory as VOLUME here
# also signals to `docker inspect` and any orchestrator that this is
# the persistent-data path, and guarantees it exists with the right
# permissions even on a fresh image without the compose mount.
VOLUME ["/var/lib/park-vis"]

EXPOSE 8000

CMD ["/app/venv/bin/python3", "main.py", "--host", "0.0.0.0", "--no-reload"]
