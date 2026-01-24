FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Pillow if needed (usually slim-bullseye has them)
RUN apt-get update && apt-get install -y --no-install-recommends \
  curl \
  && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py index.html ./
# Create an empty config.json if it doesn't exist to avoid errors, 
# although app.py handles its absence.
RUN echo "{}" > config.json

# Create non-root user for security
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# Healthcheck to verify the service is up
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

# Run with Gunicorn (2 workers for basic concurrency within each replica)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "600", "--access-logfile", "-", "app:app"]
