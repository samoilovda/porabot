# Production-ready Dockerfile for Porabot Telegram Bot
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Create non-root user for security
RUN groupadd --gid 1000 porabot && \
    useradd --uid 1000 --gid 1000 --shell /bin/bash --create-home porabot

# Set work directory
WORKDIR /app

# Install system dependencies (for NATasha and other native extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies.
# setuptools must be pinned below 81 (and installed first, in its own layer)
# before requirements.txt: pymorphy2 (a natasha dependency, transitively
# required by dateparser's NLP pipeline) imports pkg_resources at runtime,
# which setuptools>=81 removes; its dependency docopt also fails to build
# from its legacy setup.py-only sdist against newer setuptools.
RUN pip install --no-cache-dir "setuptools<81" wheel
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create data directory for volumes (persistent storage)
RUN mkdir -p /app/data && chown -R porabot:porabot /app

# Switch to non-root user
USER porabot

# Run the application
CMD ["python", "-m", "bot"]