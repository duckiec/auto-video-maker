FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /video-factory

# OS dependencies for MoviePy/Whisper and text rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    imagemagick \
    curl \
    ca-certificates \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Relax ImageMagick policy for MoviePy TextClip caption rendering.
RUN if [ -f /etc/ImageMagick-6/policy.xml ]; then \
      sed -i 's/<policy domain="path" rights="none" pattern="@\*"\/>/<policy domain="path" rights="read|write" pattern="@*"\/>/g' /etc/ImageMagick-6/policy.xml; \
    fi

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# Install Playwright Chromium + required system dependencies.
RUN playwright install --with-deps chromium

COPY src ./src
COPY templates ./templates
COPY config.json ./config.json
COPY assets ./assets
COPY output ./output
COPY cookies ./cookies

EXPOSE 5000

CMD ["python", "src/app.py"]
