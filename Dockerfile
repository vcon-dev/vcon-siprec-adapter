FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpjsua2-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/vcons /app/logs

# Expose ports
EXPOSE 5060/udp 5060/tcp 5061/tcp

# Set environment variables
ENV PYTHONPATH=/app
ENV SIPREC_STORAGE_PATH=/app/vcons
ENV SIPREC_LOG_FILE=/app/logs/siprec-srs.log

# Run the application
CMD ["python", "main.py"]
