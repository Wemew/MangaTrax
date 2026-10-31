FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV CALIBRE_DIR=/calibre
ENV STAGING_DIR=/staging
ENV DATA_DIR=/app/data

WORKDIR /app

# Install curl and ca-certificates for secure HTTPS feeds
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root unprivileged user (UID/GID 1000)
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/sh -m appuser

# Copy and install dependencies
COPY backend/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY backend/ /app/

# Create mount points and ensure permissions
RUN mkdir -p /calibre /staging /app/data && \
    chown -R appuser:appuser /app /calibre /staging && \
    chmod -R 755 /app && \
    chmod 777 /app/data /calibre /staging

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
