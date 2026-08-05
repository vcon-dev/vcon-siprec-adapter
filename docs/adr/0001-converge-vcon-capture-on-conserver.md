# ADR 0001: Converge vCon capture and inspection on the conserver, retire the bespoke `/vcons` endpoint

- Status: Accepted
- Date: 2026-08-05
- Deciders: Thomas Howe
- Related: CON-701 (SIPREC interop), CON-677 (vCon programmatic query API), PR #4 (the `/vcons` stopgap)

## Context

During the NetSapiens (Crexendo) SIPREC interop we needed a way for a partner QA
team, and our own engineers, to capture and inspect the vCons the SIPREC adapter
produces. Under time pressure we added a bespoke read-only API to the adapter's
health server: `GET /vcons` (list) and `GET /vcons/{name}` (fetch), bearer-token
auth, firewall allowlist, plaintext HTTP on a droplet (PR #4).

That endpoint is a stopgap. Growing it (filter by participant, time windows,
audio playback, a UI) is re-implementing, badly and on a single box, capabilities
we already run: listing, filtering by party/tel/time/tags, content and semantic
search, auth, and tenancy.

The adapter's job is to be a **producer**: SIPREC in, vCon out. Where vCons land,
get indexed, queried, and eyeballed is a **store**, and we already have one.

## Decision

Separate producer from store, and inspect at the store.

- **Store / ingest:** the open-source conserver (`vcon-dev/vcon-server`), not the
  enterprise build. FastAPI on :8000, ingest via its API with per-ingress-list
  API keys, pipeline (transcription, tagging, webhooks), storage to **Postgres**
  (also elasticsearch/mongo/redis/s3 available). The adapter's existing webhook
  delivery (with DLQ + HMAC) posts vCons into a conserver ingress list.
- **Agent / programmatic surface:** `vcon-dev/vcon-mcp` (active, Postgres-backed,
  spec-compliant). No new code.
- **Human dashboard:** `VCONIC/vconic-app-search` (active, runs against the same
  Postgres vCon schema; already shows call detail: parties, recording, transcript,
  tags). Extend it with the one interop-specific view rather than building new.
- **Interop debugging view:** the adapter attaches the raw INVITE / SDP /
  rs-metadata to each vCon as a vCon **attachment**. Any standard vCon viewer then
  shows wire-in next to vCon-out in one object. No new UI required for the base
  case; a true side-by-side diff is an optional stretch.
- **Deterministic testing (CI):** unchanged. Golden fixtures in the repo
  (`tests/fixtures/*`) + the loopback harness. No server in this path.
- **Retire** the adapter `/vcons` endpoint once the store path is proven.

All three lenses (conserver storage, vcon-mcp, vconic-app-search) sit on one
Postgres vCon schema, so this is convergence on a single store, not three stores.

## Alternatives considered

- **Grow the `/vcons` endpoint** (add `?party=`, time filters, a UI). Rejected:
  rebuilds the store's features on a single box with weaker auth and no TLS.
- **Revive `vcon-dev/vcon-admin`.** It is the conceptually correct tool (a
  Streamlit toolkit explicitly "for conserver developers, testers, operators":
  import/export, live docker logs, QA vector uploads, analysis). But it is stale
  (last commit 2026-04-16) and its storage assumptions (Redis/Mongo-era) predate
  the current Postgres schema. Reviving it is more work than extending the
  actively-maintained `vconic-app-search`. **Kept as a reference** for dev/ops
  affordances worth porting (import/export, live logs, QA uploads), not revived.
- **Enterprise conserver (`vcon-server-enterprise`).** Rejected for this: license
  gating and deployment-manager config are unnecessary for an interop test
  surface. Use the open-source `vcon-server`.

## Consequences

- The adapter stays a thin producer; inspection, search, and auth are the store's
  concern, where they are already solved (David's `?party=` request included).
- One net-new piece of adapter code: attach raw wire input to the vCon.
- Retiring `/vcons` removes a plaintext-HTTP, single-box exposure of recordings.
- Dependency on conserver ingress health (see Open questions).

## Plan

- **Phase 0 (decide, no code):** this ADR. Confirm the target tenant/namespace and
  whether `vconic-app-search`'s call-detail view renders arbitrary vCon
  attachments (needed for the raw-wire idea).
- **Phase 1 (ingest):** point the adapter webhook at a conserver ingress list
  (test tenant). Verify a live SIPREC call lands in Postgres and is queryable.
  Keep `/vcons` running in parallel as fallback until proven.
- **Phase 2 (inspect):** give QA + engineers the dashboard/MCP. Validate filtering
  by participant UID, Call-ID, group-id, time. Feature already exists in the store.
- **Phase 3 (raw-input attachment):** adapter attaches raw INVITE/SDP/rs-metadata
  to each vCon. Optional stretch: a diff view.
- **Phase 4 (retire + harden):** remove `/vcons`; clear the droplet debt on CON-701
  (webhook.site sink, RTP allowlist re-lock off 0.0.0.0/0, stale tcpdump, stale
  admin IPs). Auth and TLS come from the store, not the droplet.

Sequencing: 0 -> 1 -> 2 unblock the partner and are mostly reuse. 3 is the durable
win. 4 trails. `/vcons` stays as fallback through Phase 1 so David is never blocked.

## Open questions

- Does `vconic-app-search`'s detail view render arbitrary vCon attachments? Drives
  whether the raw-wire idea needs UI work or is free.
- Is the conserver ingress healthy enough to depend on? CON-671 reported a 500
  (storage-sync `ImportError: No module named 'links'`). If unresolved, that is a
  Phase 1 blocker to fix first.
- One shared interop tenant or per-partner tenants? Drives the ingress-list and
  auth model in Phase 1.
