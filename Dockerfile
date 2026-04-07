FROM python:3.11-slim

WORKDIR /app

# Copy application files
COPY server.py .
COPY public/ ./public/

# Create data directory for SQLite (persistent volume mount point)
RUN mkdir -p /data

# Set environment variables
ENV PORT=8080
ENV DATABASE_PATH=/data/sealtrust.db
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/restaurants')" || exit 1

CMD ["python3", "server.py"]
