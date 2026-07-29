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

## 1. The blocker: we do not answer a re-INVITE with SDP

**Read this before anything else. This is a hard blocker for attended transfer
and it is the same failure class as CON-704.**

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

| # | Gap | Where | Cost in the session |
|---|-----|-------|---------------------|
| 1 | Re-INVITE answered with no SDP | `sip_server.py:241` | Transfer media never flows. Blocker. |
| 2 | Updated rs-metadata on re-INVITE dropped | same early return | Transferee missing from the vCon. Wrong output, not a failure. |
| 3 | `group_id` / `group-ref` never parsed | `siprec_parser.parse_rs_metadata` only iterates `participant` | Two SIPREC dialogs from one transfer produce two unlinked vCons. |
| 4 | `recording_session_id = call_id` | `sip_server.py:255`, comment already says "refine if metadata carries one" | We ignore the metadata's own `session_id`, so correlation keys differ from theirs. |
| 5 | `UPDATE` gets bare 200 OK | `sip_server.py:223` | Same as #1 if they use UPDATE instead of re-INVITE. |
| 6 | Participant disassociation not represented | `participantsessionassoc` / `disassociate-time` unparsed | A party who leaves mid-call looks present for the whole recording. |

Gaps 3 and 4 are worth a decision, not a rush fix: if a transfer opens a second
recording dialog, the choice is one vCon per dialog cross-referenced by group, or
one vCon assembled across dialogs. Do not invent that during the call. Note
which shape their transfer actually produces and decide after.

## 3. Pre-call work, in order

Now is 2026-07-29 19:35 UTC. About 21 hours, realistically one evening plus one
morning.

1. **Ask David for the flow list and a metadata sample per flow.** Highest value
   per minute, and it is a question only he can answer. Specifically: which
   flows he wants to run (supervised/attended transfer, blind transfer,
   simultaneous ring, conference/barge, hold/resume), whether each is signalled
   as re-INVITE, UPDATE, or a brand-new INVITE, and a raw rs-metadata sample for
   the transfer case the way he sent the 1.1 sample today. A sample lets us build
   a fixture and pre-validate offline instead of discovering shapes live. Send
   via Superhuman as thomas.howe@strolid.com.
2. **Fix gap #1** (section 1). Blocker, and small.
3. **Build local synthetic tests for the flows** (section 4) so we know our own
   behavior before their traffic arrives. Extend
   `tests/test_siprec_capture.py`, which already drives INVITE -> RTP -> BYE on
   loopback and is the right harness.
4. **Re-run the on-the-wire probe** after any deploy: `/root/probe_labels.py` on
   the droplet exits non-zero unless the answer carries both labels. Add a
   re-INVITE leg to it if time allows, since that is now the risky path.
5. **Housekeeping that is now overdue** and will muddy evidence if left:
   relock the media allowlist from `0.0.0.0/0` to `132.226.155.215` (real source
   IP is known now), `pkill tcpdump` (pid 116213, moot since David's own egress
   capture settled the NAT question), and decide on the public
   webhook.site sink before three-party traffic touches it.

## 4. Local synthetic tests to write first

Each of these is our own behavior, provable without David. Model them on the
existing loopback client in `tests/test_siprec_capture.py`.

- **Re-INVITE re-offer.** INVITE, RTP, then re-INVITE with the same two labels
  and a third stream `a=label:3` plus rs-metadata adding a participant. Assert
  the 200 OK carries SDP with three m-lines and three labels, that a recorder
  exists for label 3, and that the finished vCon has three parties with the
  third mapped by label rather than position.
- **Re-INVITE with unchanged SDP.** Assert we still answer with a full SDP
  echoing the existing ports, and that we do not double-bind recorders or
  duplicate participants. This is the idempotency case and the likeliest real
  shape.
- **Second INVITE, new Call-ID, same `group_id`.** Assert current behavior
  explicitly, two independent sessions, so the decision in section 2 is made
  against a documented baseline rather than a guess.
- **CON-705 under three streams.** The reversed-order fixture in
  `tests/test_stream_party_mapping.py` covers two. Add a three-stream case with
  labels out of order, since transfer is where stream order is most likely to
  stop matching participant order.
- **1.1 participant_id shape.** The 1.0 fixtures in
  `tests/test_netsapiens_metadata.py` use a User ID
  (`1003@dwang.netsapiens.com`); 1.1 uses the leg's SIP Call-ID
  (`20260729164412058133-0018491486ec5db64acd5aca455acfe8`). Our join reads
  whatever the metadata says, so this should pass unchanged. Assert it, so the
  version-agnosticism is pinned rather than assumed.

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
