# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project loosely follows [Semantic Versioning](https://semver.org/).

## [Unreleased] — branch `refactor/phase1-spec-compliance`

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
