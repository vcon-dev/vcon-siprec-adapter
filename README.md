# SIPREC SRS to vCon Server

A Session Recording Server (SRS) that receives SIPREC recording sessions and converts them into vCon format for standardized conversation storage and analysis.

## Features

- **SIPREC Protocol Support**: Full RFC 7866 compliance for session recording
- **Multiple Transport Support**: TCP, UDP, and TLS transport protocols
- **vCon Conversion**: Automatic conversion of recorded sessions to vCon format
- **Flexible Storage**: Local filesystem storage with configurable naming
- **Webhook Delivery**: POST vCons to external APIs with retry logic
- **Audio Codec Support**: G.711, G.722, Opus, and other common codecs
- **Concurrent Sessions**: Handle multiple recording sessions simultaneously

## Quick Start

### Prerequisites

- Python 3.8+
- PJSIP development libraries
- SSL certificates for TLS support (optional)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd vcon-siprec-adapter
```

2. Install dependencies:
```bash
# Install PJSIP development libraries (Ubuntu/Debian)
sudo apt-get install libpjsua2-dev

# Install Python dependencies
pip install -r requirements.txt

# Or install in development mode
pip install -e .
```

3. Configure the server:
```bash
cp .env.example .env
# Edit .env with your configuration
```

4. Run the server:
```bash
python main.py
```

### Docker Installation (Alternative)

```bash
# Build the Docker image
docker build -t siprec-srs .

# Run with configuration
docker run -d \
  --name siprec-srs \
  -p 5060:5060/udp \
  -p 5060:5060/tcp \
  -p 5061:5061/tcp \
  -v $(pwd)/vcons:/app/vcons \
  -v $(pwd)/config.yaml:/app/config.yaml \
  siprec-srs
```

## Configuration

The server can be configured via `config.yaml` or environment variables. See `config.yaml` for all available options.

### Basic Configuration

```yaml
server:
  listen_address: "0.0.0.0"
  sip_port_udp: 5060
  sip_port_tcp: 5060
  sip_port_tls: 5061

storage:
  local_path: "./vcons"
  filename_pattern: "{timestamp}_{call_id}.vcon.json"

webhooks:
  enabled: true
  endpoints:
    - url: "https://api.example.com/vcons"
      headers:
        Authorization: "Bearer your-token"
```

## Usage

### Starting the Server

```bash
# Using config file
python main.py --config config.yaml

# Using environment variables
python main.py --env-file .env

# With custom log level
python main.py --log-level DEBUG
```

### Testing with SIPREC Client

The server accepts SIPREC INVITE requests on the configured ports. A typical SIPREC INVITE looks like:

```
INVITE sip:recorder@your-server.com:5060 SIP/2.0
Via: SIP/2.0/UDP client.example.com:5060
From: <sip:client@example.com>;tag=abc123
To: <sip:recorder@your-server.com>
Call-ID: call-123@example.com
CSeq: 1 INVITE
Recording-Session-ID: session-456
Content-Type: application/sdp

v=0
o=client 123 456 IN IP4 192.168.1.100
s=Session Recording
c=IN IP4 192.168.1.100
t=0 0
m=audio 5004 RTP/AVP 0 8
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
```

### Testing with SIPp

You can use SIPp to test the server:

```bash
# Install SIPp
sudo apt-get install sipp

# Send a test SIPREC INVITE
sipp -sf test_siprec.xml your-server.com:5060
```

Create `test_siprec.xml`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<scenario name="SIPREC Test">
  <send>
    <![CDATA[
      INVITE sip:recorder@[remote_ip]:[remote_port] SIP/2.0
      Via: SIP/2.0/UDP [local_ip]:[local_port];branch=z9hG4bK[call_id]
      From: <sip:test@[local_ip]:[local_port]>;tag=[call_id]
      To: <sip:recorder@[remote_ip]:[remote_port]>
      Call-ID: [call_id]
      CSeq: 1 INVITE
      Recording-Session-ID: test-session-[call_id]
      Content-Type: application/sdp
      Content-Length: [len]

      v=0
      o=test 123 456 IN IP4 [local_ip]
      s=Test Recording
      c=IN IP4 [local_ip]
      t=0 0
      m=audio 5004 RTP/AVP 0
      a=rtpmap:0 PCMU/8000
    ]]>
  </send>
  <recv response="200" />
  <send>BYE</send>
  <recv response="200" />
</scenario>
```

## Generated vCon Format

The server generates vCon files with the following structure:

```json
{
  "vcon": "0.0.1",
  "uuid": "generated-uuid",
  "created_at": "2023-01-01T12:00:00Z",
  "parties": [
    {
      "tel": "+1234567890",
      "name": "Caller",
      "role": "caller"
    },
    {
      "tel": "+1987654321", 
      "name": "Callee",
      "role": "callee"
    }
  ],
  "dialog": [
    {
      "type": "recording",
      "start": "2023-01-01T12:00:00Z",
      "parties": [0, 1],
      "mimetype": "audio/wav",
      "body": "base64-encoded-audio-data",
      "encoding": "base64"
    }
  ],
  "tags": {
    "call_id": "call-123@example.com",
    "recording_session_id": "session-456",
    "source": "siprec"
  }
}
```

## API Reference

### Command Line Options

- `--config FILE`: Path to YAML configuration file
- `--env-file FILE`: Path to environment file
- `--log-level LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)
- `--daemon`: Run as daemon process

### Configuration Options

See `config.yaml` for complete configuration reference.

## Development

### Running Tests

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest --cov=siprec_srs tests/

# Run specific test file
pytest tests/test_vcon_converter.py
```

### Code Formatting

```bash
# Format code
black siprec_srs/ tests/

# Check code style
flake8 siprec_srs/ tests/

# Type checking
mypy siprec_srs/
```

### Development Setup

```bash
# Install in development mode
pip install -e .

# Install development dependencies
pip install -e ".[dev]"

# Run pre-commit hooks (if configured)
pre-commit install
```

## License

[License information]

## Contributing

[Contributing guidelines]
