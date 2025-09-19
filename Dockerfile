# Minimal runtime for the Slack Socket Mode bot
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install uv (fast Python package manager)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.local/bin:${PATH}"

# Copy project metadata first for better Docker layer caching
COPY pyproject.toml ./
# If present, include lock file for reproducible builds
COPY uv.lock* ./

# Sync dependencies (no dev deps by default)
RUN uv sync --frozen --no-install-project || uv sync --no-install-project

# Copy application code
COPY . .

# Install the project itself into the virtual environment
RUN uv sync

# Ensure the venv is used at runtime
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# Run as non-root user for safety
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# No ports to expose (Slack Socket Mode uses outbound websockets)
CMD ["python", "-u", "socket_app.py"]

