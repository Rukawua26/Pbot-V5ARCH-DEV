# Dockerfile for Sniper AI
# Optimized for OCI Ampere (ARM64) and deterministic non-root volumes

FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Ensure the bot looks for the DB in the mapped data directory
ENV SNIPER_DB_PATH=/app/data/sniper_brain.db

# Set working directory
WORKDIR /app

# Install system dependencies
# build-essential is included to handle C/C++ compilation for ARM64 wheels (XGBoost, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with deterministic UID/GID for host volume ownership.
# UID/GID 1000 matches the default cloud user on most OCI Ubuntu/Oracle Linux VPS images.
ARG USER_ID=1000
ARG GROUP_ID=1000
RUN groupadd -g ${GROUP_ID} botgroup && \
    useradd -u ${USER_ID} -g botgroup -m botuser

# Install Python dependencies
# Using --no-cache-dir to keep image size small
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Create necessary data directories and set permissions
# We create /app/data and /app/models so the user can write to them
RUN mkdir -p /app/data /app/models && \
    chown -R botuser:botgroup /app

# Switch to non-root user
USER botuser

# Command to run the bot
CMD ["python", "main.py"]
