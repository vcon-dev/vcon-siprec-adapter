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
- **External-media publishing** (optional) - publish audio to a filesystem or
  S3 bucket, then emit `url` + `sha512-<base64url>` `content_hash` instead of
  inlining audio.
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

- Python 3.12+ recommended (3.8+ may work; Docker uses 3.12).
- An RSA private key (PEM) only if JWS signing is enabled.
- No PJSIP / `pjsua2` install. The SRS is a pure-Python asyncio SIP UAS
  plus RTP recorder.

### Installation

```bash
git clone https://github.com/vcon-dev/vcon-siprec-adapter
cd vcon-siprec-adapter

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Or, for development:
pip install -e ".[dev]"
```

### Configuration

Configure via `config.yaml` (copy and edit) or environment variables.
A starter `.env.example` is committed.

```bash
cp .env.example .env
# or
cp config.yaml config.local.yaml
```

Common env vars include listen/storage/webhook settings plus optional
`SIPREC_PUBLIC_IP` (IP advertised in SDP/`Contact` when the host bind
address is not the public address). S3 publishing uses the standard AWS
credential provider chain; see *External audio publishing* below.

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
  -v "$(pwd)/certs:/app/certs:ro" \
  -e SIPREC_PUBLIC_IP=YOUR.PUBLIC.IP \
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
  publisher: "none"          # "none" | "filesystem" | "s3"
  base_url: null             # optional public/CDN URL override
  key_pattern: "{recording_session_id}/{stream_id}.wav"
  filesystem:
    path: "./recordings"
  s3:
    bucket: null
    region: null
    prefix: ""
    endpoint_url: null
    retry_attempts: 3
    backoff_factor: 1.0

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

### External audio publishing

`media.mode: inline` retains the default self-contained vCon behavior and
ignores publisher settings.

`media.mode: external` selects one of these publishers:

- `none` retains the previous operator-managed behavior. The adapter hashes
  the local WAV and composes its URL from `base_url` plus the WAV filename.
  `base_url` is required because the adapter does not copy the file.
- `filesystem` atomically copies each WAV below `filesystem.path`. If
  `base_url` is set, dialogs use that public or CDN URL. Otherwise, dialogs
  contain the stored file's absolute `file://` URL. Mount the destination into
  the container when running under Docker.
- `s3` uploads each WAV to `s3.bucket` using `key_pattern` and `s3.prefix`.
  If `base_url` is set, dialogs use it as a CDN or public URL origin.
  Otherwise, the adapter derives the standard S3 HTTPS object URL.
  `endpoint_url` supports S3-compatible stores.

S3 credentials come from boto3's standard AWS credential provider chain,
including IAM roles, web identity, shared credentials, and the
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN`
environment variables. Do not put credentials in `config.yaml`.

The minimal S3 identity policy needs `s3:PutObject` for the configured bucket
and prefix. The adapter does not list or delete objects. Bucket lifecycle rules
or filesystem operations own retention.

Publishing fails closed. S3 retries transient network, throttling, and server
errors using bounded exponential backoff. If any stream still fails, the
adapter does not sign, store, or deliver a partial vCon, and it keeps temporary
WAV files for operator recovery. It does not fall back to inline audio.

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
      "purpose": "tags",
      "party": 0, "dialog": 0,
      "encoding": "json",
      "body": "{\"source\":\"siprec\",\"call_id\":\"call-123@example.com\",\"recording_session_id\":\"session-456\"}"
    },
    {
      "type": "lawful_basis",
      "party": 0, "dialog": 0,
      "encoding": "json",
      "body": "{\"lawful_basis\":\"legitimate_interests\", ...}"
    }
  ]
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

## Testing SIPREC capture

There is no checked-in SIPp scenario file. Prefer the repo’s loopback
capture test, which drives INVITE → RTP → BYE → vCon without an external
SIP stack:

```bash
.venv/bin/python -m pytest tests/test_siprec_capture.py -q
```

For live-target debugging, see [`docs/`](docs/) (DigitalOcean runbook,
capture-window runbook, and complex-call-flow notes).

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

The suite currently collects **125 tests**. All of them run offline with
the project venv (no PJSIP / `pjsua2`):

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

Focused capture / publisher checks:

```bash
.venv/bin/python -m pytest tests/test_siprec_capture.py tests/test_media_publisher.py -q
```

Coverage:

```bash
.venv/bin/python -m pytest --cov=siprec_srs -q
```

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
`pytest -q` and `black` before opening a PR. Start with
[`docs/README.md`](docs/README.md) for the documentation map.
