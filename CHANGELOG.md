# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project loosely follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

> Test counts inside dated entries below are point-in-time figures for
> that entry. The suite currently collects **125 tests**.

### 2026-07-30 — External audio publishing (filesystem + S3)

- **`media.publisher`** added: `none` | `filesystem` | `s3`. In
  `mode: external`, the adapter can now publish the captured WAV itself
  instead of only labelling a URL.
  - `filesystem` copies each recording atomically under
    `media.filesystem.path`.
  - `s3` uploads to `media.s3.bucket` using boto3's standard AWS
    credential provider chain, with bounded retries for transient
    network / throttling / 5xx errors.
  - `none` preserves the previous operator-managed behavior and still
    requires `media.base_url`.
- **Deterministic object keys** via `media.key_pattern`
  (default `{recording_session_id}/{stream_id}.wav`).
- **URL derivation:** `media.base_url` overrides the publisher URL for
  CDN / public front doors; otherwise filesystem emits `file://` and S3
  emits its HTTPS object URL.
- **Fail-closed publishing:** if any stream cannot be published, no vCon
  is signed, stored, or delivered, and temporary WAVs are retained for
  operator recovery. No silent fallback to inline audio.
- **Startup validation** for media mode, publisher name, bucket / path,
  retry settings, and `key_pattern`.
- `boto3` added to `requirements.txt`.

### 2026-07-29 — NetSapiens / Crexendo interop fixes

- SDP answers echo `a=label` per RFC 7866 §5.2 (root cause of the
  zero-RTP interop failure).
- Streams correlate to participants via rs-metadata instead of
  positional order, with positional mapping only as a fallback.
- Re-INVITE / UPDATE re-offers are answered with SDP and updated
  rs-metadata is absorbed rather than discarded.
- RFC 7865 group / session keys are retained so transfer legs stitch
  across multiple SIPREC sessions.
- RTP recorders bind inside the configured firewall port range, and
  `Contact` reflects the actual transport and port.

### 2026-07-20 — Capture path rewritten as a pure-Python SIP UAS

- Dropped `pjsua2` / PJSIP entirely. The SRS is now an asyncio SIP UAS
  (`sip_server.py`) plus an RTP recorder (`rtp_recorder.py`, stdlib
  `audioop` G.711 decode).
- `siprec_parser.py` parses the real multipart INVITE
  (`application/sdp` + `application/rs-metadata+xml`).
- Session emission is BYE-driven via `_on_session_complete`, replacing
  the placeholder fixed-duration sleep.
- Docker image no longer builds pjproject from source.
- Added end-to-end loopback capture test
  (`tests/test_siprec_capture.py`).

### 2026-05-10 — Phase 5 (post-merge speckit re-audit)

Re-audit against the 2026-05-07 speckit refresh (which verified spec
alignment with vcon-lib v0.9.1) surfaced five issues caused by
vcon-library quirks. All five fixed.

- **Tags attachment compliance:** `Vcon.add_tag()` emits a
  `purpose: "tags"` attachment without the `party` / `dialog` indices
  that draft-02 §4.4 REQUIRES. Replaced the five `add_tag()` calls with
  a `_add_tags_attachment()` helper that emits `party: 0`, `dialog: 0`,
  `encoding: "json"`, and a JSON-stringified object body (lib emitted
  an array of `"key:value"` strings).
- **Dialog default-strip extended:** `_strip_default_empty_fields` now
  also removes lib-emitted empty `metadata: {}` and `meta: {}` keys
  from every Dialog. `metadata` isn't a spec Dialog field; `meta` is
  documented as Party-extension-only.
- **Dialog `session_id` populated:** every recording dialog now carries
  `session_id: {local: <recording-session-id>, remote: <call-id>}` per
  draft-02 / RFC 7989 §5. Omitted when `recording_session_id` is empty.
- **Transcription analysis carries `mediatype`:** speckit Analysis
  Object lists `mediatype` as recommended; `add_transcription_analysis`
  now sets it to `"application/json"`.

6 new tests added; 72 passing total.

---

## Phases 1–4 — branch `refactor/phase1-spec-compliance`

A four-phase refactor bringing the adapter into compliance with
`draft-ietf-vcon-vcon-core-02` (vcon syntax `0.4.0`) and adding the
production-hardening features common to other vCon adapters.

### Added

- **vCon extensions**
  - `sip-signaling` (draft-howe-vcon-sip-signaling): emits a
    `purpose: "sip-message-trace"` attachment with call_id,
    recording_session_id, URIs, and media-stream summary; stamps
    `sip_call_id` on every recording dialog.
  - `lawful_basis` (draft-howe-vcon-lawful-basis): emits a
    `type: "lawful_basis"` attachment with configurable basis,
    purposes, expiration, and justification. Default: enabled with
    `legitimate_interests` for `[recording]`.
  - `wtf_transcription` (draft-howe-vcon-wtf-extension): pluggable
    `TranscriptionProvider` Protocol places transcripts in
    `analysis[]`.
- **JWS signing** (`Signer` class) — RS256 signing with eager PEM key
  loading. Disabled by default.
- **External-media mode** — Dialog `url` + `content_hash` of form
  `sha512-<base64url-unpadded>` instead of inline base64url body.
- **Webhook hardening**
  - HMAC-SHA256 body signing per endpoint, sent as
    `X-Hub-Signature-256: sha256=<hex>`.
  - `Idempotency-Key: <vCon-UUID>` on every request.
  - Dead-letter queue (`webhooks.dlq_path`) for vCons whose every
    endpoint fails after retries.
- **Health server** — `/healthz` + Prometheus `/metrics` on `:8080`
  (configurable; enabled by default).
- **Configuration** — new top-level sections: `media`, `lawful_basis`,
  `signing`, `health`. New webhook fields: `dlq_path`,
  `endpoints[].hmac_secret`.
- **Tests** — 66 tests (covering converter, extensions, signing,
  webhook delivery + HMAC round-trip, external media, health server,
  and transcription provider interface).
- **Docs** — `.env.example`, JWS-verification and webhook-HMAC-verification
  snippets in README, this CHANGELOG.

### Changed (spec compliance)

- vCon syntax version is now `"0.4.0"` (was `"0.0.1"` / unset).
- Recording dialog `mimetype` → `mediatype`; `encoding` `"base64"` →
  `"base64url"` (with matching `urlsafe_b64encode`).
- Session metadata moved from a synthetic `type: "text"` Dialog to a
  proper `purpose: "session_metadata"` attachment.
- All vCon-payload timestamps are now timezone-aware UTC ISO-8601.
- Stream provenance moved from non-spec `dialog.metadata` to a
  `purpose: "stream_provenance"` attachment.
- Party constructor calls drop non-spec `role`/`uuid` kwargs; identity
  hints (`name`, `tel`, `mailto`) only.
- Per-stream party indexing: when SIPREC stream count matches participant
  count, recording dialogs map 1:1 to a single participant index and
  set `originator`.
- `vcon.is_valid()` is no longer a delivery gate (it rejects
  extension-defined attachments such as `lawful_basis` that legally
  use `type:`).

### Fixed

- `setup.py` console-script entry point pointed at a non-existent
  `siprec_srs.main:main`; now resolves to `siprec_srs.cli:run`.
- `main.py --daemon` argparse flag removed (was advertised but
  unimplemented).
- `vcon` library pin tightened from `>=0.0.1` to `>=0.9.1` so spec-
  correct names (`amended`, `critical`) and modern helpers are present.
- Eliminated default empty `redacted: {}` and `group: []` keys per
  speckit guidance.
- SIPREC parser had a copy-paste-duplicated header list.

### Deferred

- `lifecycle` extension — current `draft-howe-vcon-lifecycle` is
  SCITT-protocol-focused and does not yet define a concrete
  attachment shape.
- Redaction hooks — heavily policy-driven; needs a stable spec for
  the `redacted` block before implementation.
- Concrete ASR backends (Whisper, Deepgram) — the
  `TranscriptionProvider` interface is in place; specific vendor
  adapters belong in their own packages.
