# SIPREC SRS to vCon Server

A Session Recording Server (SRS) that receives SIPREC recording sessions and
emits them as **spec-compliant vCons** (IETF `draft-ietf-vcon-vcon-core-02`,
syntax `0.4.0`).

## Features

- **SIPREC Protocol Support** — RFC 7866; UDP, TCP, and TLS transports.
- **Spec-compliant vCon emission** — vcon syntax `0.4.0`, `mediatype`,
  `base64url` body encoding, ISO-8601 UTC timestamps, attachment vs.
  analysis vs. dialog placed correctly.
- **Extension support** — emits structured attachments for the
  `sip-signaling` extension (call_id, recording_session_id, URIs) and the
  `lawful_basis` extension (recording consent / legitimate interest).
- **JWS signing** (optional) — RS256-sign every vCon before storage and
  delivery using a configured RSA private key.
- **External-media mode** (optional) — emit `url` + `sha512-<base64url>`
  `content_hash` instead of inlining audio as base64url.
- **Pluggable transcription** — `TranscriptionProvider` Protocol places
  WTF transcripts in `analysis[]` per `draft-howe-vcon-wtf-extension`.
  Default is no-op; plug in Whisper / Deepgram / etc. without modifying
  the converter.
- **Hardened webhook delivery** — per-endpoint HMAC-SHA256 body signing
  (`X-Hub-Signature-256`), `Idempotency-Key` header (= vCon UUID),
  exponential-backoff retries, and an optional dead-letter queue (DLQ)
  for vCons whose every endpoint fails.
- **Health & metrics** — built-in `/healthz` and Prometheus `/metrics`
  endpoint exposing webhook-delivery counters.
- **Local storage** — filesystem with configurable filename pattern.
- **Audio codec support** — G.711 (μ-law / A-law), G.722, Opus.
- **Concurrent sessions** — multiple SIPREC sessions in flight at once.

## Quick Start

### Prerequisites

- Python 3.8+
- PJSIP / pjsua2 (only required for live SIPREC capture; tests can run
  without it — see *Running Tests* below).
- An RSA private key (PEM) — only if JWS signing is enabled.

### Installation

```bash
git clone https://github.com/vcon-dev/vcon-siprec-adapter
cd vcon-siprec-adapter

# System libraries for pjsua2 (Debian / Ubuntu)
sudo apt-get install libpjproject-dev

# Python dependencies
pip install -r requirements.txt
# Or, for development:
pip install -e ".[dev]"
```

> **Note on pjsua2:** the `pjsua2` Python wheel needs a working PJSIP
> install on the host. If you only intend to run the test suite or
> exercise the converter / signing / webhook code, you can skip pjsua2
> entirely — none of those modules import it. See *Running Tests*.

### Configuration

Configure via `config.yaml` (copy and edit) or environment variables.
A starter `.env.example` is committed.

```bash
cp .env.example .env
# or
cp config.yaml config.local.yaml
```

### Run the server

```bash
python main.py --config config.yaml
# or, with environment variables:
python main.py --env-file .env
# log-level override:
python main.py --log-level DEBUG
```

### Docker

```bash
docker build -t siprec-srs .

docker run -d \
  --name siprec-srs \
  -p 5060:5060/udp \
  -p 5060:5060/tcp \
  -p 5061:5061/tcp \
  -p 8080:8080/tcp \
  -v "$(pwd)/vcons:/app/vcons" \
  -v "$(pwd)/dlq:/app/dlq" \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  siprec-srs
```

## Configuration

All sections in `config.yaml` are optional; each ships with sensible
defaults. The most commonly tuned blocks:

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
  dlq_path: "./dlq"          # null disables the dead-letter queue
  endpoints:
    - url: "https://api.example.com/vcons"
      headers:
        Authorization: "Bearer your-token"
      retry_attempts: 3
      timeout: 30
      backoff_factor: 2.0
      hmac_secret: null      # set to enable X-Hub-Signature-256

media:
  mode: "inline"             # "inline" | "external"
  base_url: null             # required when mode == "external"

lawful_basis:
  enabled: true
  lawful_basis: "legitimate_interests"
  purposes: ["recording", "transcription", "analysis"]

signing:
  enabled: false
  private_key_path: null     # PEM path; RS256 only
  private_key_password: null

health:
  enabled: true
  host: "0.0.0.0"
  port: 8080
```

See `config.yaml` in the repo root for the full annotated reference.

## Generated vCon Format

A typical signed vCon emitted by this server looks like the following.
Before signing, the same payload appears at the top level — after
signing, it's wrapped in a JWS form (`payload` + `signatures`).

```json
{
  "vcon": "0.4.0",
  "uuid": "019e08c5-3065-868f-9dd8-dd37220d739c",
  "created_at": "2026-05-08T12:00:00+00:00",
  "extensions": ["sip-signaling", "lawful_basis"],
  "parties": [
    { "tel": "+1234567890", "name": "Alice" },
    { "tel": "+1987654321", "name": "Bob" }
  ],
  "dialog": [
    {
      "type": "recording",
      "start": "2026-05-08T12:00:00+00:00",
      "parties": [0],
      "originator": 0,
      "mediatype": "audio/wav",
      "duration": 12.34,
      "filename": "stream_0.wav",
      "encoding": "base64url",
      "body": "UklGRiQ...",
      "sip_call_id": "call-123@example.com"
    }
  ],
  "attachments": [
    {
      "purpose": "session_metadata",
      "party": 0, "dialog": 0,
      "encoding": "json",
      "body": "{\"call_id\":\"call-123@example.com\", ... }"
    },
    {
      "purpose": "sip-message-trace",
      "party": 0, "dialog": 0,
      "mediatype": "application/json",
      "encoding": "json",
      "body": "{\"call_id\":\"call-123@example.com\", ... }"
    },
    {
      "purpose": "stream_provenance",
      "party": 0, "dialog": 0,
      "encoding": "json",
      "body": "{\"stream_id\":\"stream_0\",\"source\":\"rtp_capture\"}"
    },
    {
      "type": "lawful_basis",
      "party": 0, "dialog": 0,
      "encoding": "json",
      "body": "{\"lawful_basis\":\"legitimate_interests\", ...}"
    }
  ],
  "tags": {
    "call_id": "call-123@example.com",
    "recording_session_id": "session-456",
    "source": "siprec"
  }
}
```

Notes on the spec-defined exceptions:
- `lawful_basis` attachments use `type:` (not `purpose:`), as defined by
  `draft-howe-vcon-lawful-basis`.
- Transcripts (when a `TranscriptionProvider` is configured) appear in
  `analysis[]`, **not** `attachments[]`, per `draft-howe-vcon-wtf-extension`.
- External-media mode replaces the dialog `body` + `encoding` with `url` +
  `content_hash` of the form `sha512-<base64url-unpadded(digest)>`.

### Verifying a signed vCon

```python
from vcon import Vcon
from cryptography.hazmat.primitives import serialization

vcon = Vcon.build_from_json(open("recording.vcon.json").read())
public_key_pem = open("vcon-signing.pub.pem", "rb").read()
assert vcon.verify(public_key_pem) is True
```

### Verifying a webhook signature (receiver side)

```python
import hmac, hashlib
def verify(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

The `Idempotency-Key` header is set to the vCon UUID, so receivers can
dedupe retries and DLQ replays safely.

## Testing with SIPp

```bash
sudo apt-get install sipp
sipp -sf test_siprec.xml your-server.com:5060
```

See [`tests/`](tests/) for fixture-driven examples.

## Health and metrics

When `health.enabled: true`, the server listens on `:8080` and exposes:

- `GET /healthz` — JSON `{"status": "ok", "timestamp": "..."}`.
- `GET /metrics` — Prometheus text format with counters
  `siprec_webhook_total_attempts`, `siprec_webhook_successful`,
  `siprec_webhook_failed`, `siprec_webhook_retries`.

## API Reference

### Command Line Options

- `--config FILE` — path to YAML configuration file
- `--env-file FILE` — path to environment file
- `--log-level LEVEL` — `DEBUG` | `INFO` | `WARNING` | `ERROR`

### Configuration Options

See `config.yaml` for the complete reference.

## Development

### Running Tests

The full test suite has **66 tests** that run without `pjsua2`:

```bash
# Quick path: a venv that doesn't need PJSIP system libraries.
python3.12 -m venv .venv
.venv/bin/pip install vcon pyyaml aiohttp aiofiles structlog pydub \
    pytest pytest-asyncio pytest-aiohttp cryptography
.venv/bin/python -m pytest tests/ --ignore=tests/test_integration.py
```

Coverage:

```bash
.venv/bin/python -m pytest --cov=siprec_srs tests/ --ignore=tests/test_integration.py
```

`tests/test_integration.py` is the only test that requires a running SIP
stack; everything else (converter, extensions, signing, webhook delivery,
external media, health server, transcription) runs offline.

### Code Formatting

```bash
black siprec_srs/ tests/
flake8 siprec_srs/ tests/
mypy siprec_srs/
```

## License

MIT — see [`LICENSE`](LICENSE).

## Contributing

Issues and pull requests are welcome at
<https://github.com/vcon-dev/vcon-siprec-adapter>. Please run
`pytest tests/ --ignore=tests/test_integration.py` and `black` before
opening a PR.
