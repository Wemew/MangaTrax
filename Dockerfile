FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV CALIBRE_DIR=/calibre
# Raw downloads land in DOWNLOADS_DIR; STAGING_DIR is the final (tagged) destination.
# Mount both on the same filesystem so routing moves files by rename, not copy.
ENV DOWNLOADS_DIR=/downloads
ENV STAGING_DIR=/staging
ENV DATA_DIR=/app/data

WORKDIR /app

# Install curl, ca-certificates, and gosu for root step-down permission handling
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Create non-root unprivileged user (UID/GID 1000)
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/sh -m appuser

# Copy and install dependencies
COPY backend/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and entrypoint
COPY backend/ /app/
COPY entrypoint.sh /app/entrypoint.sh

# Create mount points and ensure permissions
RUN mkdir -p /calibre /staging /downloads /app/data && \
    chmod +x /app/entrypoint.sh && \
    chown -R appuser:appuser /app /calibre /staging /downloads && \
    chmod -R 755 /app && \
    chmod 777 /app/data /calibre /staging /downloads

EXPOSE 8080

# Probe /health, not /: with MANGATRAX_AUTH_MODE=basic|oidc the root returns 401/302 and the
# container would be marked unhealthy. /health is unauthenticated and exempt from auth.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
