FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

# Set work directory
WORKDIR /app

# Install system dependencies (if any)
# apt-get update && apt-get install -y --no-install-recommends ...

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Create data directory for volumes
RUN mkdir -p /app/data

# Run the application
CMD ["python", "-m", "bot"]
