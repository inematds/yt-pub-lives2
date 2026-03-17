FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Deno (JS runtime for yt-dlp)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_DIR=/root/.deno
ENV PATH="/root/.deno/bin:${PATH}"

# yt-dlp
RUN pip install --no-cache-dir yt-dlp Pillow

# Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# App
COPY dashboard/ /app/dashboard/
COPY scripts/ /app/scripts/
COPY scheduler.py /app/scheduler.py
RUN chmod +x /app/scripts/*

# Directories
RUN mkdir -p /data/lives /config

# ENV defaults
ENV GWS_CONFIG_DIR=/config
ENV LIVES_DIR=/data/lives
ENV PYTHONUNBUFFERED=1

WORKDIR /app

EXPOSE 8091

CMD ["python3", "dashboard/server.py", "8091"]
