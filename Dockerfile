FROM python:3.12-slim

# The SRS is pure Python (asyncio SIP UAS + RTP recorder); no pjsua2/pjproject
# build, no system SIP/RTP libraries. TLS uses the stdlib `ssl` module.
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
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
