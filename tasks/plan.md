# External Audio Publishing Plan

## Goal

Allow the SIPREC adapter to publish recorded audio to durable storage and emit
vCon recording dialogs containing `url` and `content_hash` instead of an inline
base64url `body`.

The supported publishers are:

- `none`: preserve the current operator-managed publishing behavior
- `filesystem`: copy recordings to a durable local or mounted directory
- `s3`: upload recordings to an S3 bucket

Inline mode remains the default and remains backward compatible.

## Decisions

- Both filesystem and S3 publishers are supported.
- `publisher: none` is retained.
- `media.base_url`, when configured, overrides the publisher-derived URL.
- Without `base_url`, filesystem emits a `file://` URL and S3 emits the
  publisher's HTTPS object URL.
- Remote retention and deletion are controlled outside the adapter by the
  filesystem operator or bucket lifecycle policy.
- The adapter never deletes published media.
- The default publish failure policy is fail closed:
  - Retry transient S3 failures with bounded exponential backoff.
  - If any stream cannot be published, do not save, sign, or deliver the vCon.
  - Keep all temporary WAV files for recovery.
  - Do not fall back to inline media.
  - Do not emit a partial multi-stream vCon.
- Files uploaded before another stream fails may remain as orphans. Storage
  lifecycle policy is responsible for their cleanup.

## Configuration

```yaml
media:
  mode: inline
  publisher: none
  base_url: null
  key_pattern: "{recording_session_id}/{stream_id}.wav"
  filesystem:
    path: ./recordings
  s3:
    bucket: null
    region: null
    prefix: ""
    endpoint_url: null
    retry_attempts: 3
    backoff_factor: 1.0
```

Credentials use the standard AWS credential provider chain. Secrets are not
stored in this configuration.

## Architecture

Introduce an `AudioPublisher` interface:

```python
publish(local_path, object_key) -> PublishedAudio
```

`PublishedAudio` contains:

- `url`: publisher-derived URL
- `content_hash`: `sha512-` plus unpadded base64url SHA-512 digest
- `object_key`: final storage key

The application constructs the configured publisher at startup and injects it
into `VConConverter`. During conversion, each recording is published before its
dialog is added. A configured `base_url` replaces only the URL origin, while
the publisher remains responsible for persisting the bytes.

## Processing Flow

1. SIPREC session ends and RTP recorders close temporary WAV files.
2. Inline mode reads each WAV into the dialog body as it does today.
3. External mode builds a deterministic key from session and stream metadata.
4. The selected publisher persists each WAV and returns its URL and hash.
5. The converter adds `url` and `content_hash` to each recording dialog.
6. Only after every stream succeeds, the application signs, stores, and
   delivers the vCon.
7. Temporary WAVs are deleted only after the full success path completes.
8. Any publishing or downstream processing failure leaves temporary WAVs in
   place.

## Validation

Startup validation rejects:

- unknown media modes or publisher names
- `external` plus `none` without `base_url`
- `filesystem` without a writable destination path
- `s3` without a bucket
- malformed key patterns
- retry counts below one or negative backoff values

## Compatibility

- `mode: inline` ignores publisher settings.
- Existing `mode: external` plus `base_url` maps to `publisher: none`.
- Existing URL and hash dialog fields remain unchanged.
- No published object is deleted by the adapter.

## Delivery Slices

### Slice 1: Publisher contract and filesystem backend

Add the publisher types, deterministic key generation, filesystem publishing,
configuration parsing, validation, and focused tests. Wire external conversion
to the publisher and preserve `none`.

### Slice 2: Fail-closed application flow

Make publishing failures abort conversion and verify that no vCon is saved or
delivered and temporary recordings remain. Test successful cleanup.

### Slice 3: S3 backend

Add S3 publishing using the AWS credential provider chain, bounded retries,
derived URLs, endpoint override support, and mocked unit tests.

### Slice 4: Operator documentation

Update example configuration, environment guidance, README, and deployment
runbook with filesystem permissions, IAM policy, URL behavior, and lifecycle
ownership.

## Verification

- Focused publisher, converter, config, and application tests pass.
- Full pytest suite passes.
- External filesystem mode creates durable audio and resolvable dialog URLs.
- External S3 mode uploads the exact bytes and emits matching SHA-512 hashes.
- Publish failure prevents vCon output and preserves temporary recordings.
- Inline mode output remains unchanged.
