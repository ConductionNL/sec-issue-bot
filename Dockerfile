# Minimal runtime for the Slack Socket Mode bot
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

 # Install project deps first to leverage Docker layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Install the package (src/ layout)
RUN pip install --no-cache-dir .

# Run as non-root user for safety
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# No ports to expose (Slack Socket Mode uses outbound websockets)
CMD ["python", "-u", "socket_app.py"]

