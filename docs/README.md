# Documentation index

Start with the top-level [`README.md`](../README.md) for what the adapter
does and how to configure it. This directory holds the contributor guide
and the operational runbooks.

## Learn the codebase

| Doc | Read it when |
|---|---|
| [`ONBOARDING.md`](ONBOARDING.md) | You are new to the repo and want the layer-by-layer architecture, key vCon spec rules, and a guided reading order. |

## Operations and interop

| Doc | Read it when |
|---|---|
| [`RUNBOOK-digitalocean-testing.md`](RUNBOOK-digitalocean-testing.md) | Provisioning or redeploying the DigitalOcean test droplet: `doctl` commands, firewall rules, TLS certs, and a dated log of what broke and why. |
| [`RUNBOOK-capture-window.md`](RUNBOOK-capture-window.md) | Running a live RTP capture window with a partner: pre-flight checks, packet capture, and identifying the real media source IP before locking the firewall. |
| [`RUNBOOK-complex-call-flows.md`](RUNBOOK-complex-call-flows.md) | Testing transfer, hold, and multi-leg flows, including NetSapiens rs-metadata 1.1 semantics and how transfer legs stitch together. |

## Historical

| Doc | Status |
|---|---|
| [`PLAN-siprec-capture-rewrite.md`](PLAN-siprec-capture-rewrite.md) | **Completed 2026-07-20.** Kept for the reasoning behind dropping PJSIP for the pure-Python SIP UAS. Its `pjsua2` references describe the pre-rewrite code, not the current architecture. |

Release history lives in [`CHANGELOG.md`](../CHANGELOG.md).
