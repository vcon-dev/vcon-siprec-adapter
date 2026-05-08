# Onboarding Guide — vcon-siprec-adapter

Welcome. This guide gets a new contributor productive on the SIPREC →
vCon adapter. It assumes you've read the top-level `README.md` and
have Python 3.8+ available.

> **Note:** This guide was hand-authored from the post-refactor codebase
> (after the `refactor/phase1-spec-compliance` merge, commit `56c5da8`).
> Re-run `/understand` to regenerate a knowledge-graph-driven version.

---

## 1. Project Overview

| | |
|---|---|
| **Name** | `siprec-srs-vcon` |
| **Language** | Python 3.8+ (asyncio) |
| **Domain** | Telephony — SIPREC capture, vCon emission |
| **Spec target** | IETF `draft-ietf-vcon-vcon-core-02` (vCon syntax `0.4.0`) |
| **License** | MIT |
| **Tests** | 66 passing (offline; one integration test needs PJSIP) |

**What it does in one sentence:** receives SIPREC INVITE requests on
SIP/UDP/TCP/TLS, captures the RTP audio streams, and emits a
spec-compliant vCon (with optional JWS signing) to local storage and
configured webhook receivers.

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
│   vcon_converter.py  │  vcon_extensions.py  │  transcription.py │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌─────────────────────────────────────────────────────────────────┐
│                    Layer 2 — Capture                            │
│      sip_server.py   │   siprec_parser.py   │   rtp_handler.py  │
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
together: SIP server, RTP capture, conversion, signing, storage,
webhook delivery, health endpoint.

**`siprec_srs/cli.py`** is the packaged shim for the `siprec-srs`
console script (since `main.py` lives at the repo root, not inside the
package).

### Layer 2 — Capture
**`sip_server.py`** — pjsua2-backed SIP listener. Accepts SIPREC
INVITEs, manages call lifecycle, fires session callbacks.

**`siprec_parser.py`** — extracts SIPREC metadata (call_id,
recording-session-id, party URIs, codec list) from `pjsua2.CallInfo`
and SDP.

**`rtp_handler.py`** — captures RTP streams to per-stream WAV files.
Returns `{stream_id: file_path}` to the converter.

### Layer 3 — vCon Construction
**`vcon_converter.py`** — heart of the adapter. `VConConverter` takes
parsed session_data + an `RTPHandler` and emits a `Vcon` object.
Responsible for spec-compliant party/dialog/attachment shape.

**`vcon_extensions.py`** — pure helpers for the `sip-signaling` and
`lawful_basis` extensions. Operates directly on `vcon_dict` because
some extension shapes (`type: "lawful_basis"`) don't fit the lib's
core attachment model.

**`transcription.py`** — `TranscriptionProvider` Protocol +
`NoopTranscriptionProvider` default. Adapters that integrate Whisper,
Deepgram, or any ASR plug in here without changing the converter.

### Layer 4 — Cryptography & Audit
**`signing.py`** — `Signer` class wrapping `Vcon.sign()` (RS256 JWS).
Loads the PEM key once at startup so misconfiguration fails fast.
Rejects non-RSA keys explicitly.

### Layer 5 — Egress
**`storage_handler.py`** — local-filesystem persistence with
configurable filename pattern, search, cleanup, organization-by-date.

**`webhook_delivery.py`** — async POST to configured endpoints with:
canonical body serialization (HMAC-stable), `X-Hub-Signature-256`,
`Idempotency-Key` (= vCon UUID), exponential backoff, and a
dead-letter queue for vCons that exhaust all retries on every endpoint.

**`health_server.py`** — aiohttp `/healthz` + Prometheus `/metrics`.

---

## 3. Key Concepts

### vCon = Virtualized Conversation
A standardized JSON container for conversation data (parties, dialogs,
attachments, analysis, signatures). The spec target is
`draft-ietf-vcon-vcon-core-02` with syntax parameter `"0.4.0"`.

### Spec-compliance non-negotiables (from speckit)
- Field is **`schema`** on analysis, never `schema_version`. **`vendor`**
  REQUIRED on every analysis entry.
- Field is **`purpose`** on attachments, never `type` (lawful_basis is
  the documented exception).
- Body is always a **string** — JSON content goes through `json.dumps`
  with `encoding: "json"`.
- External references require both `url` and `content_hash` formatted
  `sha512-<base64url-unpadded>`.
- All vCon-payload timestamps are ISO-8601 with explicit timezone.

### Pluggable provider pattern
The codebase uses `Protocol` interfaces for extensibility:
- `TranscriptionProvider` (in `transcription.py`) — ASR plug-in seam.
- A future `AnalysisProvider` would mirror it for LLM-driven analysis.

### vcon library quirks (you WILL hit these)
The upstream `vcon` Python lib has subtleties documented inline:
- `Vcon.build_new()` does **not** set syntax `"0.4.0"`; the converter
  writes it explicitly via `vcon.vcon_dict["vcon"] = "0.4.0"`.
- `add_attachment()` rejects `encoding="json"`; build the dict and
  append directly to `vcon_dict["attachments"]`.
- `vcon.is_valid()` rejects extension-defined attachments that legally
  use `type:` (lawful_basis); we don't gate delivery on it.
- Default empty `redacted: {}` and `group: []` are emitted by the lib
  and stripped by `_strip_default_empty_fields()`.

### Two media modes
- **inline** (default): audio in Dialog `body` as base64url.
- **external**: audio published separately, Dialog carries `url` +
  `content_hash`. Operator is responsible for the upload.

### Spec exceptions you must remember
- `lawful_basis` attachments use **`type:`** not `purpose:` — per
  `draft-howe-vcon-lawful-basis`.
- Transcripts go in **`analysis[]`** not `attachments[]` — per
  `draft-howe-vcon-wtf-extension`.
- Every extension used MUST be declared in top-level `extensions[]`.

---

## 4. Guided Tour

Follow these in order to understand the request → vCon flow:

### Step 1 — Read a config file end to end
Start at `config.yaml` (commented), then `siprec_srs/config.py`. This
is the simplest module and shows the surface area: server, storage,
webhooks, media, lawful_basis, signing, health.

### Step 2 — Trace a session in `main.py`
Open `SIPRECSRSApp._on_session_created`. This is the orchestration
sequence:
1. Build session_data dict from the SIP session.
2. `vcon_converter.convert_session_to_vcon(...)` — Layer 3.
3. `signer.sign(vcon)` if signing enabled — Layer 4.
4. `storage_handler.save_vcon(...)` — Layer 5.
5. `webhook_delivery.deliver_vcon(...)` — Layer 5.

### Step 3 — Read `VConConverter.convert_session_to_vcon`
This is the spec-compliance core. Walk through:
- `Vcon.build_new()` + setting syntax to `"0.4.0"`.
- `_add_participants` (note: drops non-spec `role`/`uuid` kwargs).
- `_add_audio_dialogs` (per-stream party indexing, base64url encoding,
  inline-vs-external mode).
- `_add_session_metadata_attachment` (NOT a synthetic text dialog).
- `_add_sip_signaling` (extension declaration + `sip_call_id` stamp).
- `_add_lawful_basis` (uses `type:` exception).
- `_strip_default_empty_fields` (lib quirk cleanup).

### Step 4 — Read `vcon_extensions.py`
Pure functions, no I/O. Cleanest place to learn the spec-attachment
shape. Note how each helper calls `declare_extension(...)` so the
extension is registered in `extensions[]` exactly when an attachment
is emitted.

### Step 5 — Run the tests
```bash
.venv/bin/python -m pytest tests/ --ignore=tests/test_integration.py -v
```
Read at least:
- `test_vcon_extensions.py` — spec-shape contracts (no lib needed).
- `test_vcon_converter.py` — integration with the `vcon` lib.
- `test_webhook_delivery.py::test_signed_delivery_round_trip` — best
  one-shot demo of the egress layer; it spins up an in-process aiohttp
  server and verifies HMAC signatures end-to-end.

### Step 6 — Read the CHANGELOG
`CHANGELOG.md` summarizes what landed in the four-phase refactor and
why. It's the fastest catch-up on architectural decisions.

---

## 5. File Map

### Layer 1 — Foundation
| File | Purpose |
|---|---|
| `config.yaml` | Annotated configuration template — read first |
| `siprec_srs/config.py` | Dataclass config + YAML/env loading |
| `main.py` | Entry point, app wiring, session orchestration |
| `siprec_srs/cli.py` | `siprec-srs` console-script shim |
| `.env.example` | Env-var template for the subset that's env-loadable |

### Layer 2 — Capture
| File | Purpose |
|---|---|
| `siprec_srs/sip_server.py` | pjsua2 SIP listener, call lifecycle |
| `siprec_srs/siprec_parser.py` | SIPREC metadata extraction |
| `siprec_srs/rtp_handler.py` | RTP capture → WAV files |

### Layer 3 — vCon Construction
| File | Purpose |
|---|---|
| `siprec_srs/vcon_converter.py` | session_data → spec-compliant `Vcon` |
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

### Tests (all passing offline)
| File | What it covers |
|---|---|
| `tests/test_vcon_extensions.py` | 14 — spec-shape, no lib dep |
| `tests/test_vcon_converter.py` | 16 — full converter integration |
| `tests/test_webhook_delivery.py` | 9 — HMAC, idempotency, DLQ, round-trip |
| `tests/test_external_media.py` | 5 — sha512 hash + external mode |
| `tests/test_signing.py` | 8 — JWS signing + verification |
| `tests/test_transcription.py` | 11 — provider Protocol + integration |
| `tests/test_health_server.py` | 3 — /healthz + /metrics |

---

## 6. Complexity Hotspots

Approach these with care; they hold the most state, the most quirks,
or the most spec-compliance burden:

### 🔥 `siprec_srs/vcon_converter.py` (~470 lines)
Largest module by far. Holds:
- All spec-compliance know-how (extensions, attachments, dialogs).
- Branching for inline vs. external media mode.
- Lib-quirk workarounds.
- Per-stream → per-party mapping logic.

When changing it: read the speckit (`vcon-dev/vcon-speckit/CLAUDE.md`)
first, and ensure the corresponding test in `test_vcon_converter.py`
passes before AND after.

### 🔥 `siprec_srs/webhook_delivery.py` (~400 lines)
Async + retry + signing + DLQ. The body must be serialized **once** so
HMAC signatures are stable across retries. If you touch the
serialization path, the round-trip test
(`test_signed_delivery_round_trip`) is your safety net.

### ⚠ `siprec_srs/sip_server.py` + `rtp_handler.py`
These pull in `pjsua2`, which has a system-library dependency. Tests
for these are integration-only and not run by default. Build/run a
PJSIP-enabled venv before modifying.

### ⚠ `main.py:_on_session_created`
The `await asyncio.sleep(5)` placeholder for session-end detection is
a known limitation. Real session-end signalling is a TODO. Don't
copy-paste this pattern.

---

## 7. Ground Rules

1. **Always read the speckit before changing emitted vCon shape** —
   `/Users/thomashowe/Documents/GitHub/vcon-dev/vcon-speckit/CLAUDE.md`.
2. **Tests must pass before AND after** any change. Use the venv
   pattern in the README.
3. **No new top-level fields without a draft URL** — add to the
   appropriate extension or use `meta` / `tags`.
4. **Phased PRs, not mega-merges** — see `feedback_phasing.md` history.
   Each phase should compile and pass tests on its own.
5. **Don't trust `vcon.is_valid()`** — it predates the extensions; we
   enforce compliance via the test suite.

---

## 8. Where to go next

- Building an LLM analysis pass? Mirror `TranscriptionProvider` →
  `AnalysisProvider`. Place output in `analysis[]` with proper
  `vendor` / `product` / `schema`. See "Tier 1 LLM patterns" in the
  team chat / README follow-up.
- Adding a new spec extension? Pattern to follow:
  1. Read the corresponding `draft-howe-vcon-*.md`.
  2. Add a helper to `vcon_extensions.py` that emits the attachment
     and calls `declare_extension(vcon_dict, "<name>")`.
  3. Wire from `VConConverter.convert_session_to_vcon`.
  4. Test in `test_vcon_extensions.py` (no lib) AND
     `test_vcon_converter.py` (integration).
- Reading order if you have one hour: `README.md` → `CHANGELOG.md` →
  `vcon_extensions.py` → `vcon_converter.py::convert_session_to_vcon`
  → `test_vcon_converter.py`.
