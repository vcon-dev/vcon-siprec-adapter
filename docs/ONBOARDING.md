# Onboarding Guide — vcon-siprec-adapter

Welcome. This guide gets a new contributor productive on the SIPREC →
vCon adapter. It assumes you've read the top-level `README.md` and
have Python 3.12+ available (a venv is enough; no PJSIP).

> Updated 2026-07-30 for the pure-Python capture path, NetSapiens interop
> fixes, and external filesystem/S3 media publishing.

---

## 1. Project Overview

| | |
|---|---|
| **Name** | `siprec-srs-vcon` |
| **Language** | Python 3.12 (asyncio); Docker image is `python:3.12-slim` |
| **Domain** | Telephony — SIPREC capture, vCon emission |
| **Spec target** | IETF `draft-ietf-vcon-vcon-core-02` (vCon syntax `0.4.0`) |
| **License** | MIT |
| **Tests** | 125 collected offline (no PJSIP / `pjsua2`) |

**What it does in one sentence:** receives SIPREC INVITE requests on
SIP/UDP/TCP/TLS, captures the RTP audio streams, and emits a
spec-compliant vCon (with optional JWS signing and external audio
publishing) to local storage and configured webhook receivers.

---

## 2. Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                       Layer 5 — Egress                          │
│   storage_handler.py  │  webhook_delivery.py  │  health_server  │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│                  Layer 4 — Cryptography & Audit                 │
│                         signing.py                              │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│                  Layer 3 — vCon Construction                    │
│  vcon_converter.py │ vcon_extensions.py │ transcription.py      │
│                    │ media_publisher.py (external audio)        │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│                    Layer 2 — Capture                            │
│      sip_server.py   │   siprec_parser.py   │   rtp_recorder.py │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│                  Layer 1 — Foundation                           │
│            config.py    │    main.py    │    cli.py             │
└─────────────────────────────────────────────────────────────────┘
```

### Layer 1 — Foundation
**`config.py`** holds dataclass-based config (`Config`, `WebhookConfig`,
`MediaConfig`, `LawfulBasisConfig`, `SigningConfig`, `HealthConfig`)
plus `ConfigManager` which loads from YAML or env vars and validates.

**`main.py`** is the entry point. `SIPRECSRSApp` wires every component
together and registers `_on_session_complete` for BYE-driven emission.

**`siprec_srs/cli.py`** is the packaged shim for the `siprec-srs`
console script (since `main.py` lives at the repo root, not inside the
package).

### Layer 2 — Capture
**`sip_server.py`** — pure-Python asyncio SIP UAS. Accepts SIPREC
INVITEs (UDP/TCP/TLS), answers SDP (including `a=label` echo), binds
`RTPRecorder` instances in the configured RTP port range, absorbs
re-INVITE/UPDATE metadata, and fires the session-complete callback on BYE.

**`siprec_parser.py`** — extracts SDP + `application/rs-metadata+xml`
(participants, stream labels, NetSapiens vendor extensions / group keys).

**`rtp_recorder.py`** — asyncio UDP RTP capture to per-stream WAV files
(G.711 via stdlib/`audioop`). Returns `{stream_id: file_path}` via the
session’s `get_audio_files()` contract.

> `rtp_handler.py` is a legacy helper kept for typing/tests. Runtime
> capture uses `rtp_recorder.py`.

### Layer 3 — vCon Construction
**`vcon_converter.py`** — heart of the adapter. Builds parties, recording
dialogs, attachments, and optional external media references.

**`media_publisher.py`** — `none` / `filesystem` / `s3` publishers used
when `media.mode: external`.

**`vcon_extensions.py`** — pure helpers for `sip-signaling` and
`lawful_basis`.

**`transcription.py`** — `TranscriptionProvider` Protocol + noop default.

### Layer 4 — Cryptography & Audit
**`signing.py`** — `Signer` wrapping `Vcon.sign()` (RS256 JWS).

### Layer 5 — Egress
**`storage_handler.py`** — local-filesystem persistence of vCon JSON.

**`webhook_delivery.py`** — async POST with HMAC, idempotency key,
retries, and optional DLQ.

**`health_server.py`** — aiohttp `/healthz` + Prometheus `/metrics`.

---

## 3. Key Concepts

### vCon = Virtualized Conversation
A standardized JSON container for conversation data (parties, dialogs,
attachments, analysis, signatures). Spec target:
`draft-ietf-vcon-vcon-core-02` with syntax `"0.4.0"`.

### Spec-compliance non-negotiables
- Analysis field is **`schema`**, never `schema_version`. **`vendor`**
  REQUIRED on every analysis entry.
- Attachments use **`purpose`**, never `type` (`lawful_basis` is the
  documented exception).
- Body is always a **string** — JSON content goes through `json.dumps`
  with `encoding: "json"`.
- External references require both `url` and `content_hash` formatted
  `sha512-<base64url-unpadded>`.
- Timestamps are ISO-8601 with explicit timezone.
- Tags live in a `purpose: "tags"` attachment, not a top-level object.

### Media modes and publishers
- **`mode: inline`** (default): audio in Dialog `body` as base64url.
  Publisher settings are ignored.
- **`mode: external`**: Dialog carries `url` + `content_hash`.
  - `publisher: none` — operator publishes bytes; adapter hashes and
    composes `base_url` + filename (`base_url` required).
  - `publisher: filesystem` — adapter copies WAV under
    `filesystem.path`; URL is `file://` or `base_url` override.
  - `publisher: s3` — adapter uploads to `s3.bucket`; URL is derived
    HTTPS or `base_url` override.
- Publish failures are fail-closed: no signed/stored/delivered vCon, and
  temporary WAVs are retained.

### Spec exceptions you must remember
- `lawful_basis` attachments use **`type:`** not `purpose:`.
- Transcripts go in **`analysis[]`** not `attachments[]`.
- Every extension used MUST be declared in top-level `extensions[]`.

---

## 4. Guided Tour

### Step 1 — Read config end to end
Start at `config.yaml`, then `siprec_srs/config.py`.

### Step 2 — Trace a completed session in `main.py`
Open `SIPRECSRSApp._on_session_complete` (fired after BYE):
1. Build `session_data` from the finished SIPREC session.
2. `vcon_converter.convert_session_to_vcon(...)` (publishes audio first
   when external).
3. Optional `signer.sign(vcon)`.
4. `storage_handler.save_vcon(...)`.
5. `webhook_delivery.deliver_vcon(...)`.
6. Cleanup temporary WAVs only on the success path.

### Step 3 — Read `VConConverter.convert_session_to_vcon`
Walk `_add_participants`, `_add_audio_dialogs` (inline vs publish),
tags / session metadata / sip-signaling / lawful_basis attachments.

### Step 4 — Read `media_publisher.py` and `vcon_extensions.py`
Publisher contract + extension attachment helpers.

### Step 5 — Run the tests
```bash
.venv/bin/python -m pytest -q
```
Read at least:
- `tests/test_siprec_capture.py` — loopback INVITE/RTP/BYE.
- `tests/test_media_publisher.py` — filesystem + S3 publishers.
- `tests/test_vcon_converter.py` — emitted vCon shape.
- `tests/test_webhook_delivery.py` — HMAC round-trip.

### Step 6 — Skim ops docs
`docs/README.md` indexes deploy and interop runbooks.

---

## 5. File Map

### Layer 1 — Foundation
| File | Purpose |
|---|---|
| `config.yaml` | Annotated configuration template |
| `siprec_srs/config.py` | Dataclass config + YAML/env loading |
| `main.py` | Entry point, session-complete orchestration |
| `siprec_srs/cli.py` | `siprec-srs` console-script shim |
| `.env.example` | Env-var template |

### Layer 2 — Capture
| File | Purpose |
|---|---|
| `siprec_srs/sip_server.py` | Asyncio SIP UAS, call lifecycle |
| `siprec_srs/siprec_parser.py` | SDP + rs-metadata extraction |
| `siprec_srs/rtp_recorder.py` | RTP → WAV capture |

### Layer 3 — vCon Construction
| File | Purpose |
|---|---|
| `siprec_srs/vcon_converter.py` | session_data → `Vcon` |
| `siprec_srs/media_publisher.py` | External audio publishers |
| `siprec_srs/vcon_extensions.py` | sip-signaling + lawful_basis helpers |
| `siprec_srs/transcription.py` | Pluggable ASR / WTF analysis interface |

### Layer 4 — Cryptography
| File | Purpose |
|---|---|
| `siprec_srs/signing.py` | RS256 JWS signing + key loading |

### Layer 5 — Egress
| File | Purpose |
|---|---|
| `siprec_srs/storage_handler.py` | Filesystem persistence |
| `siprec_srs/webhook_delivery.py` | HMAC + idempotency + retries + DLQ |
| `siprec_srs/health_server.py` | `/healthz` + Prometheus `/metrics` |

---

## 6. Complexity Hotspots

### `siprec_srs/vcon_converter.py`
Spec-compliance core: parties, dialogs, attachments, external publish
branching, lib quirk stripping. Read the speckit before changing shape.

### `siprec_srs/sip_server.py`
SIP state machine, re-INVITE/UPDATE handling, Contact/SDP advertising
(`SIPREC_PUBLIC_IP`), RTP port allocation in the firewall range.

### `siprec_srs/media_publisher.py`
Filesystem atomic copy, S3 upload retries, URL derivation, content
hash format. Failures must abort conversion.

### `siprec_srs/webhook_delivery.py`
Serialize the body once so HMAC signatures stay stable across retries.

---

## 7. Ground Rules

1. **Read the speckit before changing emitted vCon shape** —
   `~/Documents/GitHub/vcon-dev/vcon-speckit/CLAUDE.md`.
2. **Tests must pass before and after** any change (`pytest -q`).
3. **No new top-level fields without a draft URL** — use extensions,
   attachments, or `meta`.
4. **Phased PRs** — each change should compile and pass tests alone.
5. **Don’t trust `vcon.is_valid()`** for extension attachments; enforce
   compliance in tests.

---

## 8. Where to go next

- Ops / partner bring-up: start at [`docs/README.md`](README.md).
- New ASR backend: implement `TranscriptionProvider`.
- New extension: helper in `vcon_extensions.py` + declare in
  `extensions[]` + tests.
- One-hour reading order: `README.md` → this guide →
  `sip_server.py` (INVITE/BYE) → `vcon_converter.py` →
  `media_publisher.py` → `tests/test_siprec_capture.py`.
