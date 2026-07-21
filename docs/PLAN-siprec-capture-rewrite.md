# Work plan — SIPREC capture path rewrite

The deploy target works (TLS 5061 listens, reachable, firewalled). The
INVITE → media → vCon path does **not** and needs a rewrite, not a patch.
This plan scopes that work. Discovered 2026-07-20 via a sipp shakeout on the
DO test droplet (see `RUNBOOK-digitalocean-testing.md`).

## Goal / acceptance criteria

A synthetic SIPREC call reproducibly yields a spec-valid vCon:

1. `sipp` sends a SIPREC INVITE (multipart: `application/sdp` +
   `application/rs-metadata+xml`) plus RTP media to the SRS.
2. SRS answers 200 OK, records the audio, and on BYE emits a vCon to
   `./vcons/` that passes `Vcon.build_from_json(...).is_valid()` and carries:
   parties from the rs-metadata, a `recording` dialog with the captured audio
   (base64url or external), and the `sip-signaling` + `lawful_basis`
   attachments already implemented in `vcon_converter` / `vcon_extensions`.
3. A pytest integration test drives this end to end and is green.

## Root problems (evidence-backed)

1. **Incoming INVITE not handled.** sipp got no 1xx/2xx and no
   "Incoming SIPREC call" log. pjsua2 event processing / account-accept needs
   fixing before `onIncomingCall` fires at all.
2. **Metadata parsing is stubbed.** `siprec_parser.py` regexes the SIP URI and
   reads a `recordingSessionId` attr that doesn't exist. Real SIPREC metadata
   is an `application/rs-metadata+xml` body part; `pj.CallInfo` doesn't expose
   the raw body.
3. **Media capture is wrong for pjsua2.** `rtp_handler.py` binds its own UDP
   socket to pjmedia's RTP port (impossible) and hand-rolls RTP + μ-law. Must
   use `pj.AudioMediaRecorder` on the call's `AudioMedia`.
4. **Wrong thread.** `asyncio.create_task` runs inside pjsip callback threads
   where there is no asyncio loop; it raises.

## Work items

### 1. Fix event handling so calls are received
- In `SIPRECServer.start`, set `EpConfig().uaConfig.threadCnt` explicitly and
  either let pjsua2 run its worker thread(s) or run `libHandleEvents()` in a
  dedicated thread. Add an INFO log at the top of
  `SIPRECAccount.onIncomingCall` to confirm it fires.
- Confirm the account actually accepts inbound (the recorder is a UAS). The
  current manual `SIPRECCall(self, prm.callId); call.onIncomingCall(prm)` is
  suspect — follow the pjsua2 UAS pattern (create the Call, `answer()` inside
  the account's `onIncomingCall`).

### 2. Media capture via pjmedia (replaces rtp_handler hand-rolling)
- In `SIPRECCall.onCallMediaState`: for each active audio media, get the
  `AudioMedia`, create `pj.AudioMediaRecorder("stream_N.wav")`, and
  `audioMedia.startTransmit(recorder)`.
- On `onCallState` DISCONNECTED: stop recorders, then hand the WAV path(s) to
  the existing `vcon_converter` (which already inlines/externalizes audio).
- Delete or gut `rtp_handler.py` (RTPPacket parser, μ-law/A-law decoders,
  socket bind). pjmedia does all of it.

### 3. rs-metadata parsing (the hard part — evaluate first)
- pjsua2's high-level API hides the INVITE body. Options, cheapest first:
  a. Check whether pjsua2 exposes the multipart body anywhere reachable
     (some builds surface it via `CallInfo` / `onCallTsxState`); if so, parse
     the `rs-metadata+xml` from there.
  b. Register a pjsip module through the C layer to grab the raw INVITE
     (awkward from pjsua2 Python).
  c. **Architectural question to answer up front:** is pjsua2 the right layer
     at all? We are only a UAS recorder. A lighter SIP receiver (raw/asyncio
     SIP) would give us the full INVITE body trivially, and we'd still use
     pjmedia (or a plain RTP recorder) for audio. Weigh a smaller custom UAS
     vs. fighting pjsua2's abstraction. Decide before writing (2b).
- Parse rs-metadata (RFC 7865/7866) into parties + participant associations;
  feed `vcon_converter`.

### 4. Threading
- No `asyncio` inside pjsua2 callbacks. Do recorder setup synchronously in the
  callback. For vCon emit + webhook (async), marshal to the asyncio loop with
  `loop.call_soon_threadsafe(...)`, capturing the loop reference at startup.

### 5. Repeatable test harness
- Commit a `tests/siprec/` sipp scenario: INVITE with a real multipart
  (SDP + a sample rs-metadata+xml fixture) and RTP media playback (sipp
  `-mi`/media or a small pcap). Run against a locally-run container.
- Add `tests/test_integration_siprec.py` implementing the acceptance criteria.
  Keep it opt-in (marker) so the 66-test offline suite stays fast.

## Suggested order

3c (decide the SIP layer) → 1 (receive) → 2 (media) → 3 (metadata) →
4 (threading) → 5 (harness + test). Item 3c gates everything: if we drop
pjsua2 for receive, items 1 and 3 change shape.

## Risks / open questions

- rs-metadata extraction is the real unknown; 3c may flip the architecture.
- SRTP: pjproject links `-lsrtp`, so pjmedia can do SRTP later — but only if we
  stay on pjsua2 for media. Factor into the 3c decision (David asked for SRTP).
- Self-signed TLS: David's side must not verify the chain, or import our cert.

## Not in scope here

Deploy/infra (done, in the runbook). This plan is only the capture path.
