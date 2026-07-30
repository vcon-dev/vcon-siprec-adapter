# External Audio Publishing Tasks

**Status:** Implemented and verified on 2026-07-30. Full test suite: 125 passed.

## Task 1: Define publishing configuration

**Description:** Extend media configuration for publisher selection, object key
formatting, filesystem settings, and S3 settings while retaining current
defaults.

**Acceptance criteria:**
- [ ] Inline mode remains the default.
- [ ] `publisher` accepts `none`, `filesystem`, or `s3`.
- [ ] Existing external mode with `base_url` and no publisher remains valid.
- [ ] Invalid external publisher configuration fails startup validation.
- [ ] No credentials are stored in YAML.

**Verification:**
- [ ] Focused configuration tests pass.

**Dependencies:** None

## Task 2: Implement publisher contract and filesystem publisher

**Description:** Add publisher result and error types, a no-op publisher for
operator-managed media, and a filesystem publisher that durably copies WAV
files and reports a spec-correct content hash.

**Acceptance criteria:**
- [ ] Filesystem publisher creates parent directories and preserves bytes.
- [ ] Returned hash uses `sha512-` plus unpadded base64url.
- [ ] Configured `base_url` overrides the derived `file://` URL.
- [ ] Existing destination files are safely replaced.
- [ ] Publishers never delete stored objects.

**Verification:**
- [ ] Publisher unit tests pass.

**Dependencies:** Task 1

## Task 3: Publish during vCon conversion

**Description:** Inject the configured publisher into the converter, generate
deterministic object keys, and add external dialogs only after publishing
succeeds.

**Acceptance criteria:**
- [ ] External dialogs contain `url` and `content_hash`, not `body`.
- [ ] Inline dialogs remain unchanged.
- [ ] `publisher: none` retains current URL composition behavior.
- [ ] A publish failure aborts conversion rather than dropping a dialog.
- [ ] Multi-stream conversion does not return a partial vCon.

**Verification:**
- [ ] Focused external media and converter tests pass.

**Dependencies:** Tasks 1 and 2

## Task 4: Preserve temporary media on failure

**Description:** Ensure cleanup runs only after conversion, signing, vCon
storage, and webhook processing complete successfully.

**Acceptance criteria:**
- [ ] Publish failure results in no saved or delivered vCon.
- [ ] Temporary WAVs remain after any failed processing path.
- [ ] Temporary WAVs are removed after the full success path when cleanup is
  enabled.

**Verification:**
- [ ] Application lifecycle tests pass.

**Dependencies:** Task 3

## Task 5: Implement S3 publisher

**Description:** Upload media using the standard AWS credential provider chain,
bounded exponential backoff, optional endpoint override, and derived object
URLs.

**Acceptance criteria:**
- [ ] Bucket and prefix produce the expected object key.
- [ ] Upload sends the recorded bytes and WAV content type.
- [ ] Transient failures retry up to the configured limit.
- [ ] Final failure raises a publishing error.
- [ ] `base_url` overrides the derived S3 URL.
- [ ] The adapter never deletes S3 objects.

**Verification:**
- [ ] Mocked S3 unit tests pass without AWS credentials.

**Dependencies:** Tasks 1 and 2

## Task 6: Document operations

**Description:** Document all modes, configuration, IAM requirements, URL
derivation, failure behavior, recovery, and external retention ownership.

**Acceptance criteria:**
- [ ] `config.yaml`, `.env.example`, and README agree.
- [ ] Filesystem permissions and mount guidance are documented.
- [ ] Minimal S3 IAM permissions are documented.
- [ ] Bucket lifecycle policy is identified as the retention mechanism.
- [ ] Failure recovery explains why temporary WAVs are retained.

**Verification:**
- [ ] Configuration examples match parser fields.

**Dependencies:** Tasks 1 through 5

## Task 7: Final verification

**Description:** Run focused and full regression checks.

**Acceptance criteria:**
- [ ] All focused tests pass.
- [ ] Full pytest suite passes.
- [ ] No inline media regression is present.
- [ ] No secrets or unrelated files are included in the change.

**Dependencies:** Tasks 1 through 6
