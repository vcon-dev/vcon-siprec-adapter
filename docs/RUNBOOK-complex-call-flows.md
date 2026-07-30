# Runbook: complex call flow interop, NetSapiens metadata 1.1

Session: **Thu 2026-07-30 10:00-11:00 PT** (13:00 ET, 17:00 UTC) with David Wang,
Crexendo. Confirmed by David 2026-07-29 17:15 UTC: *"Yes, tomorrow 10:00 Pacific
is still a good time for us to connect. May be we can try more complex call
flow."*

Basic two-party G.711 passed 2026-07-29 16:44 UTC (CON-704 closed, first RTP
ever, 409KB vCon). CON-705 landed and deployed 18:53 UTC, so per-stream party
attribution is now correct by rs-metadata rather than by list position. This
session is the next gate: the flows where 1.1 actually differs from 1.0.

David on what changes in 1.1, 2026-07-29 17:15 UTC:

- `participant_id` is now the call leg's **SIP Call-ID**, not their system User ID
- the NS extension **explicitly names caller vs callee**
- on transfer it carries a **reference to the xfer-from Call-ID** (the
  consultation call, for an attended transfer)
- *"From other SIPREC partner we have tested with, most of the effect manifest
  only for more complicated call flow, such as Supervised Transfer."*

---

## 0. READ FIRST: the mechanism is not re-INVITE

Answered by David Wang at 2026-07-29 20:07 UTC, after section 1 below was
written on the opposite assumption:

> Our side closes the existing SIPREC session start a new SIPREC session upon
> changes in the parties. However, these sequence of SIPREC session share the
> same group_id but with an incrementing "groupSeq" inside the NetSapiens meta
> data session.

**NetSapiens never re-offers on the existing dialog for a party change.** Every
party change is a BYE plus a fresh INVITE. So an attended transfer arrives as
three separate SIPREC sessions, and today produces three separate vCons.

Section 1 is therefore about a shape this counterparty does not send. The fix
there is still correct and still deployed, and it protects against any SRC that
*does* re-offer, but it is not what tomorrow exercises. The relevant work is
cross-session correlation, section 2 gap 3 and CON-708.

Real payload, from David's 20:36 UTC mail, saved verbatim at
`tests/fixtures/netsapiens_attended_transfer_11.py`:

| Leg | group_id | groupSeq | byAction | parties |
|-----|----------|----------|----------|---------|
| Initial 20:17:21Z | `58cc3154ca0bdd2b0efbf9a04139526e` | 0 | ForwardSRing | 1001, 1002 |
| Consultation 20:17:41Z | `5ed05251-7abca882-e05368bf@192.168.0.245` | 0 | ForwardSRing | 1002, 1006 |
| Post-transfer 20:17:48Z | `58cc3154…` (the original) | **1** | **XferSup** | 1001, 1006 |

**Agreed flows: blind transfer, then attended transfer.** No conference or barge
until their v46, since their implementation is two-party only. Simultaneous ring
changes only `byAction`, because they start SIPREC on answer.

`group_id`, `groupSeq`, their `stream_id`s, and the transfer references are all
retained and tagged as of `c64e2e8`, deployed 20:54 UTC (CON-708 step 1). The
three legs are now stitchable; whether they *should* be stitched into one vCon
is still undecided, deliberately, until blind transfer's shape is also in hand.

## 1. A re-INVITE was answered without SDP — FIXED 2026-07-29, but off-path

> **Status: fixed, committed, deployed.** `a632372` on
> `thomashowe/con-707-reinvite-reoffer-sdp-answer`, live on 138.197.42.97 as of
> 19:43 UTC, health ok and `/root/probe_labels.py` passing. Tracked as CON-707
> (Done). The section below is kept as the diagnosis, since it explains what to
> watch for tomorrow and why the re-offer path is the risky one.
>
> What now happens: a re-INVITE or UPDATE gets a full SDP answer with labels and
> Contact; existing streams keep their advertised ports matched by `a=label`; an
> unseen label gets a new recorder; updated rs-metadata is absorbed with
> participants appended (never reordered, because party indices are referenced
> by dialogs built at conversion time). Covered by
> `tests/test_complex_call_flows.py`, 5 tests, all verified to fail against the
> old behavior.

**Original diagnosis, 2026-07-29. This was a hard blocker for attended transfer
and the same failure class as CON-704.**

`sip_server._on_invite`, lines 241-244:

```python
# Re-INVITE on an existing dialog: just 200 the existing SDP setup.
if call_id in self.sessions:
    reply(self._response(msg, 200, "OK"))
    return
```

`_response()` called that way passes no `body` and no `content_type`, and no
`add_contact_ip`. So the re-INVITE gets a **200 OK with `Content-Length: 0`, no
SDP, and no Contact.**

Why that matters here specifically: we now know from CON-704 that the NetSapiens
SRC client parses our SDP answer to derive the media destination, and requires
`a=label:N` in it to correlate streams. On a re-INVITE it gets no SDP at all,
which is strictly less than the label-less answer that already broke it for a
week. A transfer is normally signalled as exactly this: a re-INVITE on the
existing recording dialog carrying updated SDP and updated rs-metadata.

Two further consequences of the same early return:

- **Updated rs-metadata is discarded.** `session.participants`,
  `session.stream_labels`, and `session.vendor_extension` are only ever set in
  the initial-INVITE path. A transferee added mid-call never becomes a party, so
  the vCon will describe a two-party call that was actually three-party.
- **New streams get no recorder.** Recorders are bound only in the initial
  path, so a stream added by the transfer is never captured, and the answer
  could not advertise a port for it anyway.

`UPDATE` has the same shape: `_dispatch` line 223 replies a bare
`200 OK` for `OPTIONS`/`INFO`/`UPDATE` alike.

### Minimum fix before the call

Handle a re-INVITE as a re-offer: parse SDP + rs-metadata again, reuse the
existing recorder for a stream whose label we already have, bind a new recorder
for a label we do not, refresh participants / stream_labels / vendor_extension,
and answer with a full SDP built by `_build_sdp_answer` including `a=label` and
the Contact. Keep the existing transaction dedup on `(call_id, cseq)`.

Preserve on refresh rather than replace wholesale: a participant list that
shrinks should not orphan a dialog already attributed to a party index. Simplest
safe rule for tomorrow is append-only on participants, and never re-index an
existing party.

Time-box this. If it is not solid by ~08:00 PT, go into the call with it
unfixed and say so up front, because a silent no-SDP answer will burn the hour
the same way the missing label burned the week.

## 2. Known gaps, ranked, with what each costs tomorrow

| # | Gap | Status | Cost in the session |
|---|-----|--------|---------------------|
| 1 | Re-INVITE answered with no SDP | **FIXED**, CON-707 | Was: transfer media never flows. |
| 2 | Updated rs-metadata on re-INVITE dropped | **FIXED**, CON-707 | Was: transferee missing from the vCon. |
| 5 | `UPDATE` gets bare 200 OK | **FIXED**, CON-707 | Was: same as #1 via UPDATE. |
| 3 | `group_id` / `group-ref` never parsed | OPEN, CON-708 | Two dialogs from one transfer produce two unlinked vCons. |
| 4 | `recording_session_id = call_id`, metadata `session_id` ignored | OPEN, CON-708 | Our correlation key differs from theirs. |
| 6 | Participant disassociation not represented | OPEN, CON-709 | A party who leaves mid-call looks present for the whole recording. |

Gaps 1, 2, and 5 shared one fix and are done. What is left is one decision
(CON-708) and one modelling question (CON-709), neither of which should be
improvised during the call.

Gaps 3 and 4 are worth a decision, not a rush fix: if a transfer opens a second
recording dialog, the choice is one vCon per dialog cross-referenced by group, or
one vCon assembled across dialogs. Do not invent that during the call. Note
which shape their transfer actually produces and decide after.

## 3. Pre-call work, in order

Written 2026-07-29 19:35 UTC, updated 19:50 UTC after items 2, 3, and 4 landed.

1. **Ask David for the flow list and a metadata sample per flow.** Highest value
   per minute, and it is a question only he can answer. Specifically: which
   flows he wants to run (supervised/attended transfer, blind transfer,
   simultaneous ring, conference/barge, hold/resume), whether each is signalled
   as re-INVITE, UPDATE, or a brand-new INVITE, and a raw rs-metadata sample for
   the transfer case the way he sent the 1.1 sample today. A sample lets us build
   a fixture and pre-validate offline instead of discovering shapes live.
   **Status: drafted in Superhuman as thomas.howe@strolid.com, draft
   `draft002a40f5c89f7244`, not sent. Thomas sends it.**
2. ~~**Fix gap #1**~~ **DONE**, CON-707, deployed 19:43 UTC (section 1).
3. ~~**Build local synthetic tests for the flows**~~ **DONE**,
   `tests/test_complex_call_flows.py` (section 4).
4. ~~**Re-run the on-the-wire probe**~~ **DONE**, `/root/probe_labels.py` passes
   against the deployed image. Still worth adding a re-INVITE leg to that probe
   if there is time in the morning, since the loopback tests cover the logic but
   the probe is the only check that runs against the real TLS listener.
5. **Housekeeping that is now overdue** and will muddy evidence if left:
   relock the media allowlist from `0.0.0.0/0` to `132.226.155.215` (real source
   IP is known now), `pkill tcpdump` (pid 116213, moot since David's own egress
   capture settled the NAT question), and decide on the public
   webhook.site sink before three-party traffic touches it.

## 4. Local synthetic tests to write first

**All written and passing** as of 2026-07-29 19:45 UTC. Local suite 103 passed,
plus the one pre-existing `test_vcon_creation_and_storage` failure. Kept here as
the inventory of what is and is not covered going into the call.

`tests/test_complex_call_flows.py` drives the SRS over loopback as a SIPREC
client and asserts on the wire. Every one of the five was verified load-bearing
by patching `_on_reoffer` back to the old bare-200 behavior, and every one fails
against it:

- **Re-INVITE adding a stream.** Third labelled stream plus a third participant.
  Asserts three m-lines and labels 1/2/3 in the answer, the first two ports
  unchanged, three recorders, three parties in order, and the xfer-from
  reference captured from the newer vendor extension.
- **Re-INVITE with unchanged SDP.** The hold/resume and likeliest real shape.
  Same ports answered, no double-bound recorders, no duplicated participants or
  media_streams.
- **Metadata-only re-INVITE.** A party announced with no media offered still
  lands, and no recorder is bound for a stream that was never offered.
- **UPDATE carrying a re-offer.** Same SDP answer as the re-INVITE path.
- **Second dialog, new Call-ID, same `group_id`.** Documents the current
  two-independent-sessions baseline explicitly, so CON-708 is decided against an
  asserted fact rather than a guess.

In `tests/test_stream_party_mapping.py`:

- **Three streams, labels declared out of order** (2, 3, 1) against participants
  listed in a third order, so no positional reading of either list is correct.
  Transfer is where stream order is most likely to stop tracking participants.
- **1.1 `participant_id` shape.** 1.0 used a system user id
  (`1003@dwang.netsapiens.com`), 1.1 uses the leg's SIP Call-ID. The join reads
  whatever the metadata says, so both work; now asserted rather than assumed.

**Not covered, know this going in:** no test drives RTP *through* a re-offer, so
"media keeps flowing on the original ports while a third stream starts" is
argued from the port assertions rather than proven end to end. If a transfer
fails tomorrow in a way the wire trace does not explain, that is the first gap to
suspect.

## 5. Run of show

Before, from 09:30 PT:

- `curl http://138.197.42.97:8080/healthz` returns ok, container up, correct commit deployed
- `docker logs -f siprec-srs` open in one pane for the whole call
- `ls -la vcons/` noted, so new files are obvious by diff rather than by guess
- confirm the webhook sink state matches what David was told

During each flow, one flow at a time, and write down the wall-clock start of
each. For every call capture:

- the SIP trace from David's side (he has been sending `sipflow` / tshark links
  unprompted, they have been the most useful artifact of this whole interop)
- our `Answered SIPREC INVITE ... N stream(s), M participant(s)` line, and
  whether any re-INVITE or UPDATE appeared
- per-recorder `stopped: N packets` lines, expecting non-zero on every stream
- the emitted vCon path and byte size. Under ~5KB means an empty shell, which
  is the old failure signature
- **the raw rs-metadata for that flow**, which we still do not retain, so ask
  him to paste it as he did today

After each flow, check the vCon for: party count matching the humans actually on
the call, one dialog per real stream, each dialog's `parties` pointing at the
right person via label, and the vendor extension carrying the xfer-from
Call-ID when a transfer happened.

## 6. What counts as success

Not "no errors". This session succeeds if we come out knowing, for each flow
David runs:

1. how NetSapiens signals it (re-INVITE / UPDATE / new INVITE)
2. whether we answered correctly and captured every stream
3. whether the vCon's parties and per-party audio attribution match reality
4. a saved raw rs-metadata fixture per flow

Item 4 is the one that keeps paying after the call, and it is the item most
likely to be forgotten in the moment. We store only the *parsed* vendor
extension, so it cannot be recovered from a vCon afterwards.

Partial success is fine and expected. Finding that supervised transfer produces
a shape we mishandle is a good outcome, as long as it is a *known* shape by the
end of the hour.

## 7. Do not fix live

Judgement from the CON-704 week: David walked his own client to root cause
himself, and our best contribution was a correct answer plus not changing his
code. Same posture here. Log, capture, and reproduce offline against a fixture.
The one exception is gap #1 if it is still open at call time, because without an
SDP answer there is nothing to observe at all.

## Cross-references

- Linear: CON-701 (interop), CON-704 (Done, `a=label`), CON-705 (Done,
  stream mapping), CON-706 (Todo, `mimetype`, unrelated dead path)
- `docs/RUNBOOK-digitalocean-testing.md` for droplet provisioning
- `docs/RUNBOOK-capture-window.md` for the 07-29 capture-window procedure
- Deployed commit as of 2026-07-29 18:53 UTC: `16895f1` on
  `thomashowe/con-705-participant-to-stream-mapping-is-positional-ignores-rs`
