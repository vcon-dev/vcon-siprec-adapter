# ADR 0001: Converge vCon capture and inspection on the conserver, retire the bespoke `/vcons` endpoint

- Status: Accepted
- Date: 2026-08-05
- Internal tracking: CON-736

## Context

When bringing a SIPREC source up against this adapter, developers (and, during an
interop, a counterparty's QA team) need a way to capture and inspect the vCons the
adapter produces. Under time pressure it is tempting to add a read-only API to the
adapter itself: `GET /vcons` (list) and `GET /vcons/{name}` (fetch), bearer-token
auth over the health server. That endpoint exists today as a stopgap.

It is the wrong long-term home. Growing it (filter by participant, time windows,
audio playback, a UI) re-implements, on a single process, capabilities a vCon
store already provides: listing, filtering by party/tel/time/tags, content and
semantic search, auth, and tenancy.

The adapter's job is to be a **producer**: SIPREC in, vCon out. Where vCons land,
get indexed, queried, and inspected is a **store**.

## Decision

Separate producer from store, and inspect at the store.

- **Store / ingest:** the open-source conserver (`vcon-dev/vcon-server`). FastAPI
  ingest with per-ingress-list API keys, a processing pipeline, and storage to
  Postgres (also elasticsearch/mongo/redis/s3). The adapter's existing webhook
  delivery (with DLQ + HMAC) posts vCons into a conserver ingress list.
- **Agent / programmatic surface:** `vcon-dev/vcon-mcp` (Postgres-backed,
  spec-compliant). No new code.
- **Human dashboard:** an existing vCon search UI over the same Postgres schema
  (call detail: parties, recording, transcript, tags). Extend rather than build.
- **Integration debugging view:** the adapter attaches the raw INVITE / SDP /
  recording metadata to each vCon as a vCon **attachment**. Any standard vCon
  viewer then shows wire-in next to vCon-out in one object. No new UI for the base
  case; a side-by-side diff is an optional stretch.
- **Deterministic testing (CI):** unchanged. Golden fixtures in the repo
  (`tests/fixtures/*`) + the loopback harness. No server in this path.
- **Retire** the adapter `/vcons` endpoint once the store path is proven.

All lenses (conserver storage, vcon-mcp, the search UI) sit on one Postgres vCon
schema, so this is convergence on a single store, not three stores.

## Alternatives considered

- **Grow the `/vcons` endpoint** (add `?party=`, time filters, a UI). Rejected:
  rebuilds the store's features on a single process with weaker auth and no TLS.
- **A standalone Streamlit admin app** (e.g. an older vCon admin toolkit:
  import/export, live logs, QA vector uploads). Conceptually correct for
  developer/tester debugging, but the mature ones are stale and assume pre-Postgres
  storage. Kept as a reference for affordances worth porting, not revived.
- **A commercial/enterprise conserver build.** Rejected for a test surface: license
  gating and remote config are unnecessary. Use the open-source `vcon-server`.

## Consequences

- The adapter stays a thin producer; inspection, search, and auth are the store's
  concern, where they are already solved.
- One net-new piece of adapter code: attach raw wire input to the vCon.
- Retiring `/vcons` removes a plaintext-HTTP, single-process exposure of recordings.
- Adds a dependency on conserver ingress health.

## Plan

- **Phase 0 (decide, no code):** this ADR. Confirm the target tenant and whether the
  search UI's detail view renders arbitrary vCon attachments (needed for raw-wire).
- **Phase 1 (ingest):** point the adapter webhook at a conserver ingress list (test
  tenant). Verify a live capture lands in Postgres and is queryable. Keep `/vcons`
  as fallback until proven.
- **Phase 2 (inspect):** give QA + engineers the dashboard/MCP. Validate filtering
  by participant, call id, group id, time. Already a store feature.
- **Phase 3 (raw-input attachment):** adapter attaches raw INVITE/SDP/metadata to
  each vCon. Optional stretch: a diff view.
- **Phase 4 (retire + harden):** remove `/vcons`; auth and TLS come from the store.

Sequencing: 0 -> 1 -> 2 are mostly reuse. 3 is the durable win. 4 trails. `/vcons`
stays as fallback through Phase 1.

## Open questions

- Does the chosen search UI's detail view render arbitrary vCon attachments? Drives
  whether the raw-wire idea needs UI work or is free.
- Is the conserver ingress healthy enough to depend on?
- One shared tenant or per-source tenants? Drives the ingress-list and auth model.
