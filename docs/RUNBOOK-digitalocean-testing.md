# Runbook — SIPREC adapter test target on DigitalOcean

Independent, copy-pasteable runbook for standing up the `vcon-siprec-adapter`
SRS on a DigitalOcean droplet as the target for David's SIPREC source, and
tearing it back down. Everything is `doctl` / `ssh` from the command line.

- Owner: Thomas Howe (`thomas.howe@strolid.com`, doctl already authed)
- Partner source IPs (inbound SIPREC): `132.226.155.215`, `129.153.104.169`
  (both Oracle Cloud, US-East — pick a DO NYC region for proximity)
- Related note: `~/ObsidianVault/memory/projects/siprec-adapter.md`
- Decisions in force: TLS on 5061, **plain RTP** (no SRTP yet), verify via
  metrics + webhook sink + sample vCon.

Status log is at the bottom — append, don't rewrite.

---

## 0. Variables (edit once, reused below)

```bash
export DO_REGION=nyc3
export DO_SIZE=s-2vcpu-2gb          # enough RAM to build pjsua2 image
export DO_IMAGE=ubuntu-24-04-x64
export DROPLET=siprec-test
export SSH_KEY_NAME=thomas-ed25519  # name as it appears in `doctl compute ssh-key list`
export DAVID_1="132.226.155.215/32"
export DAVID_2="129.153.104.169/32"
export ADMIN_IP="$(curl -s https://ifconfig.me)/32"   # your current public IP, for metrics access
```

## 1. Register SSH key (once)

```bash
doctl compute ssh-key list
# If ~/.ssh/id_ed25519.pub isn't listed:
doctl compute ssh-key import "$SSH_KEY_NAME" --public-key-file ~/.ssh/id_ed25519.pub
export SSH_KEY_ID=$(doctl compute ssh-key list --no-header --format ID,Name | awk -v n="$SSH_KEY_NAME" '$2==n{print $1}')
```

## 2. Create the droplet

```bash
doctl compute droplet create "$DROPLET" \
  --region "$DO_REGION" --size "$DO_SIZE" --image "$DO_IMAGE" \
  --ssh-keys "$SSH_KEY_ID" --wait

export DROPLET_IP=$(doctl compute droplet get "$DROPLET" --format PublicIPv4 --no-header)
echo "Target address for David: ${DROPLET_IP}:5061 (TLS)"
```

## 3. Cloud firewall (lock inbound to David + you)

```bash
doctl compute firewall create \
  --name "${DROPLET}-fw" \
  --droplet-ids "$(doctl compute droplet get "$DROPLET" --format ID --no-header)" \
  --inbound-rules "\
protocol:tcp,ports:5061,address:${DAVID_1},address:${DAVID_2} \
protocol:udp,ports:10000-20000,address:${DAVID_1},address:${DAVID_2} \
protocol:tcp,ports:8080,address:${ADMIN_IP} \
protocol:tcp,ports:22,address:${ADMIN_IP}" \
  --outbound-rules "\
protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 \
protocol:udp,ports:all,address:0.0.0.0/0,address:::/0 \
protocol:icmp,address:0.0.0.0/0,address:::/0"
```

- `5061/tcp` — SIP over TLS, David's IPs only.
- `10000-20000/udp` — RTP media, David's IPs only. (pjsua2 negotiates media
  ports in this range; widen if capture logs show ports outside it.)
- `8080/tcp` — health/metrics, your admin IP only.
- `22/tcp` — SSH, your admin IP only.

## 4. Install Docker + the adapter on the droplet

```bash
ssh root@$DROPLET_IP
```

On the droplet:

```bash
apt-get update && apt-get install -y docker.io git openssl
systemctl enable --now docker

git clone https://github.com/vcon-dev/vcon-siprec-adapter
cd vcon-siprec-adapter

# Self-signed TLS cert for testing (CN = droplet IP)
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 90 \
  -keyout certs/siprec-srs.key -out certs/siprec-srs.pem \
  -subj "/CN=$(curl -s http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address)"

docker build -t siprec-srs .
```

## 5. Configure (TLS 5061, RTP, metrics, webhook sink)

Edit `config.yaml` on the droplet (`server:` cert paths point at the mounted
certs; leave `signing.enabled: false` for the first test):

```yaml
server:
  listen_address: "0.0.0.0"
  sip_port_tls: 5061
  tls_cert: "/app/certs/siprec-srs.pem"
  tls_key: "/app/certs/siprec-srs.key"
webhooks:
  enabled: true
  endpoints:
    - url: "https://webhook.site/<your-unique-id>"   # quick disposable sink
      retry_attempts: 3
health:
  enabled: true
  port: 8080
```

Grab a throwaway webhook URL from https://webhook.site (this is the "webhook
sink" we promised David) and paste it above.

## 6. Run (host networking so RTP ports aren't mapped)

```bash
docker run -d --name siprec-srs --restart unless-stopped \
  --network host \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -v "$(pwd)/certs:/app/certs:ro" \
  -v "$(pwd)/vcons:/app/vcons" \
  -v "$(pwd)/logs:/app/logs" \
  siprec-srs

docker logs -f siprec-srs   # watch for "listening" on 5061
```

> `--network host` is used deliberately: SIPREC RTP arrives on dynamic UDP
> ports that are painful to enumerate for `-p` mappings. Host networking lets
> the DO cloud firewall (step 3) be the single place exposure is controlled.
> The repo `docker-compose.yml` is NOT used here — its 8080 maps to an nginx
> test container and it maps no RTP range.

## 7. Verify

```bash
# From your laptop (admin IP is allowed on 8080):
curl http://$DROPLET_IP:8080/healthz
curl http://$DROPLET_IP:8080/metrics | grep siprec_webhook

# After David sends a test call, on the droplet:
ls -la vcons/                       # a {timestamp}_{call_id}.vcon.json appears
docker logs siprec-srs | tail -50   # RTP capture + vCon emit
```

Deliverables to David after a good test call:
1. Metrics URL: `http://$DROPLET_IP:8080/metrics` (from an allowed IP)
2. The webhook.site sink URL (he can watch vCons land live)
3. A sample `vcons/*.vcon.json` file

## 8. Give David the target

```
Target: <DROPLET_IP>:5061
Transport: TLS
Media: plain RTP (no SRTP)
```

## 9. Teardown (stop the meter)

```bash
doctl compute firewall delete "${DROPLET}-fw"
doctl compute droplet delete "$DROPLET" --force
```

---

## Known gaps / watch items

- **SRTP unbuilt.** David asked about AES-CM suites; adapter does plaintext RTP
  only. This test is RTP. Secure media is a separate work item.
- **RTP port range is an assumption** (`10000-20000/udp`). If capture fails,
  check `docker logs` for the actual negotiated ports and widen the firewall.
- **Self-signed TLS cert.** Fine for testing; David's side must not verify the
  chain (or import our cert). Real cert before anything production.
- **8080 has no auth.** Firewall is the only control — keep it pinned to admin
  IPs.

## Live resources (test session 2026-07-19)

- Droplet: `siprec-test`, ID `585884080`, **138.197.42.97**, nyc3, s-2vcpu-2gb,
  ubuntu-24-04-x64.
- SSH key: "MBP Public" (DO ID `45061097`) = local `~/.ssh/id_ed25519`.
- Firewall: `siprec-test-fw`, ID `c528a3b7-328e-44b1-a95e-ff782deca77d`.
- Admin IP allowlisted at provisioning: `172.58.167.173/32` (mobile — if SSH or
  :8080 stops working, re-add your current IP to the firewall).
- **Target for David: `138.197.42.97:5061`, TLS, plain RTP.**

Teardown when done:
```bash
doctl compute firewall delete c528a3b7-328e-44b1-a95e-ff782deca77d --force
doctl compute droplet delete siprec-test --force
```

## Status log

- 2026-07-19 — runbook created; doctl authed as thomas.howe@strolid.com.
- 2026-07-19 — droplet + firewall provisioned (see Live resources). Deploy
  (docker.io install, repo clone, self-signed TLS cert) running via ssh.
- 2026-07-19 — SSH source-IP churn: laptop is on mobile, v4 egress changed
  172.58.167.173 -> 31.46.246.169 within minutes; re-added current IP to
  firewall (rules 22 + 8080). Expect to re-add again. Long ops run detached
  on the droplet (nohup + log) so a dropped SSH doesn't kill them.
- 2026-07-19 — **Dockerfile was broken**: `libpjsua2-dev` is not a real Debian
  package and there is no `pjsua2` PyPI wheel, so the image never built on
  current `python:3.11-slim` (Debian trixie). Fixed by building pjproject
  2.13.1 from source with SWIG Python bindings (`./configure --enable-shared`,
  then `pjsip-apps/src/swig/python && make && make install`) and stripping the
  pjsua2 pin before `pip install`. Rebuild in progress.
- 2026-07-19 — Dockerfile had a 3-bug chain, all fixed in the repo Dockerfile:
  (1) `libpjsua2-dev` not a real pkg -> build pjproject 2.13.1 from source;
  (2) base `python:3.11-slim` too old, `vcon>=0.9.1` needs Python >=3.12 ->
  bumped to `python:3.12-slim`; (3) Python 3.12 dropped stdlib `distutils` and
  pjproject's swig python build does `from distutils.core import setup` ->
  `pip install setuptools` before the swig make. Rebuild in progress.
- 2026-07-19 — observation: pjproject build links `-lsrtp`, so the pjsip stack
  bundles SRTP. The SRTP gap David asked about is app-level wiring in
  sip_server.py, not a missing media stack. Future work item, not this test.
- 2026-07-19 — image built; deployed via `docker run --network host`. Health
  server on :8080 comes up. Found the **live SIP path had never worked** —
  `siprec_srs/sip_server.py` was written against a non-existent pjsua2 API.
  Fixes applied (in repo):
  - endpoint init: `pj.Endpoint().defaultConfig()` (2nd Endpoint + missing
    method, caused C++ `terminate`/abort) -> `self.endpoint.libInit(pj.EpConfig())`.
  - transports: `TransportConfig.setPort()` / `setTlsSetting()` +
    `PJSIP_TLS_SETTING_*` don't exist -> `.port` and
    `.tlsConfig.certFile` / `.tlsConfig.privKeyFile`; one config per transport.
  - account: used base `pj.Account` (incoming calls never handled) -> wired
    `SIPRECAccount` + `set_server`; `setIdUri()` -> `.idUri`.
  - after fixes, UDP + TCP transports create fine; TLS failed with
    `PJSIP_EUNSUPTRANSPORT` because the Dockerfile lacked **`libssl-dev`**, so
    pjproject compiled without OpenSSL/TLS. Added libssl-dev; rebuilding.
- Ground truth: introspect the installed pjsua2, don't trust the old code.
  `pj.EpConfig` exists, `Endpoint.defaultConfig` does not; `TransportConfig`
  uses `.port` + `.tlsConfig` (TlsConfig has `certFile`, `privKeyFile`).
- 2026-07-19 — **SMOKE TEST GREEN.** After libssl-dev rebuild: "SIPREC server
  started successfully / Listening on UDP:5060, TCP:5060, TLS:5061". `ss`
  confirms 5060 + 5061 bound. `/healthz` returns `{"status":"ok"}` externally
  from admin IP. 5061 correctly refuses the admin IP (firewalled to David only)
  — verified by a blocked `nc`. Target `138.197.42.97:5061` is LIVE.
- 2026-07-20 — sipp shakeout (installed `sip-tester`, sent 1 INVITE at
  loopback UDP:5060). Result: **call path does not work.** SRS never logged an
  "Incoming SIPREC call", never answered (sipp got no 100/180/200), no vCon
  emitted. Server is stable when idle (exit 0, no crash) but does not process
  the incoming INVITE. The receive/capture path needs a REDESIGN, not a patch:
  - incoming INVITEs not handled — pjsua2 event processing / account accept
    config needs fixing so `onIncomingCall` actually fires.
  - metadata parsing (`siprec_parser.py`) is stubbed — reads only the SIP URI;
    real SIPREC metadata is an `application/rs-metadata+xml` body part that
    `CallInfo` doesn't expose. Needs raw-message access (custom pjsip module).
  - media capture (`rtp_handler.py`) is wrong for pjsua2 — opens its own UDP
    socket on pjmedia's RTP port (can't); should use `pj.AudioMediaRecorder`
    attached in `onCallMediaState`.
  - `asyncio.create_task` is called from pjsip callback threads (no loop
    there) — capture kickoff would raise.
  VERDICT: live SIPREC capture is a real dev task (hours+), belongs in a normal
  dev cycle, not live droplet iteration. The DEPLOY target (TLS 5061 listening,
  reachable, firewalled) is done and correct.
- 2026-07-20 — capture-path rewrite scoped in `docs/PLAN-siprec-capture-rewrite.md`.
  Droplet left RUNNING as the reachable target (~$18/mo meter). Teardown
  commands under "Live resources" when done.
- 2026-07-20 — **CAPTURE REWRITE DONE.** Dropped pjsua2 entirely; the SRS is
  now a pure-Python asyncio SIP UAS (`sip_server.py`) + RTP recorder
  (`rtp_recorder.py`, stdlib `audioop` G.711 decode), and `siprec_parser.py`
  parses the real multipart INVITE (SDP + rs-metadata). New end-to-end test
  `tests/test_siprec_capture.py` drives INVITE->RTP->BYE->vCon on loopback and
  passes; full offline suite 73 passed. Verified on the droplet: lean image
  builds in seconds (Dockerfile no longer builds pjproject), in-container test
  passes, live container up on the new image. Target `138.197.42.97:5061`
  now actually captures.
- REMAINING before David: real-SBC interop is unproven (my test uses a
  synthetic client; Oracle SBC may differ on Via/rport, multipart ordering,
  re-INVITE, TLS client behavior). Flip webhooks on + set a webhook.site sink
  for the live test. Commit the rewrite (currently only on the droplet + local
  working tree).
