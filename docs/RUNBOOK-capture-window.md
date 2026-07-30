# RUNBOOK: RTP capture window (NetSapiens interop)

For the live window with David Wang (Crexendo/NetSapiens). Goal: find out which
source IP NetSapiens RTP actually arrives from, then lock the firewall to it.

Companion to `RUNBOOK-digitalocean-testing.md` (provisioning). Linear CON-701.

## Why this is needed

Two calls (2026-07-20, 2026-07-22) had clean TLS signaling and **zero RTP**.
The first failure was ours: recorders bound OS-ephemeral ports outside the
firewall's `10000-20000/udp` range (fixed in `e40fac5`). The second still
captured nothing.

David's instance is in Oracle Cloud behind NAT. He confirmed 2026-07-25 that
SIP and RTP leave his compute instance from the same IP, but he has no control
over the WAN translation:

> Both SIP RTP should be sourced from the same IP when they leave our compute
> instance. However, I've no control how they actually transition to the WAN.

So the working theory is that media egresses a public IP that is not one of the
two allowlisted signaling IPs, and the DO cloud firewall drops it.

**The critical detail: the DO cloud firewall filters upstream of the droplet.
Dropped packets never reach the host, so `tcpdump` on the droplet cannot see
them.** That is the whole reason this window exists. The ports must be opened
*before* the call, or the capture shows nothing and proves nothing.

> ## ⚠ The media range is ALREADY open to any source
>
> Checked 2026-07-28. The live firewall carries **both** of these:
> ```
> protocol:udp,ports:10000-20000,address:0.0.0.0/0
> protocol:udp,ports:10000-20000,address:129.153.104.169/32,address:132.226.155.215/32
> ```
> The wildcard rule is not in this repo's provisioning steps and is not in the
> status log, so **when it was added is unknown**.
>
> This matters for the diagnosis, not just the procedure:
>
> - If the wildcard rule was already in place for the **2026-07-22** call, then
>   the cloud firewall did not drop that media, the NAT-egress theory does not
>   explain the failure, and the fault is below the firewall (app not binding
>   the advertised ports, or media never leaving David's side).
> - If it was added after that call, the theory still stands and is simply
>   untested.
>
> DigitalOcean does not timestamp firewall rules, so this cannot be settled
> from the API. **Resolve it before blaming NAT.** The capture in step 3 is
> still the right move either way: it distinguishes the three outcomes in
> step 4 regardless of which story is true.

## Facts

| Item | Value |
|---|---|
| Droplet | `siprec-test`, ID `585884080`, **138.197.42.97**, nyc3 |
| Cloud firewall ID | `c528a3b7-328e-44b1-a95e-ff782deca77d` |
| SIP | `5061/tcp` TLS, `sip:srs@siprec.vconic.com` |
| RTP range | `10000-20000/udp` (`rtp.port_range_start`/`_end` in `config.yaml`, same as the RTPConfig default) |
| David signaling IPs | `132.226.155.215`, `129.153.104.169` |
| Expected media | plain RTP, G.711 PCMU/PCMA, two streams |

Set once per shell:

```bash
export FW=c528a3b7-328e-44b1-a95e-ff782deca77d
export DROPLET_IP=138.197.42.97
export DAVID_1=132.226.155.215
export DAVID_2=129.153.104.169
```

> **Admin IP churn.** SSH and `:8080` are allowlisted to one admin IP and the
> laptop has changed egress IP mid-session before (see the 2026-07-19 status
> log). Confirm SSH works *before* the window opens, and if it does not, re-add
> your current IP first:
> ```bash
> MY_IP=$(curl -s https://ifconfig.me)
> doctl compute firewall add-rules $FW --inbound-rules \
>   "protocol:tcp,ports:22,address:${MY_IP}/32 protocol:tcp,ports:8080,address:${MY_IP}/32"
> ```

## 1. Before the window (do this early, not while David waits)

**SSH was timing out on 2026-07-28** from this laptop — the admin-IP churn
above. Fix that first; everything else in this runbook needs a shell on the
droplet. Four stale admin IPs have already accumulated in the firewall
(`172.58.167.173`, `31.133.144.1`, `31.46.246.169`, `46.125.135.218`); worth
pruning the dead ones while you are in there.

Then confirm the service is up and the signaling path still works:

```bash
ssh root@$DROPLET_IP 'docker ps --format "{{.Names}} {{.Status}}"; curl -s localhost:8080/healthz'
```

If the container is not running, nothing else in this runbook will produce a
recording, and a zero-RTP result would prove nothing about David's side.

## 2. Confirm media is open to any source

As of 2026-07-28 it already is (see the warning above), so this is a check
rather than a change. Run it anyway before the window, in case the rule was
removed in the meantime:

```bash
doctl compute firewall get $FW --format InboundRules | tr ' ' '\n' | grep '10000-20000'
```

Expect a line containing `address:0.0.0.0/0`. If it is absent, add it — this
is the one step that must happen before David dials:

```bash
doctl compute firewall add-rules $FW --inbound-rules \
  "protocol:udp,ports:10000-20000,address:0.0.0.0/0"
```

The narrow per-IP rule can stay; rules are additive and the open one wins.

## 3. Capture, upstream of the application

Run on the droplet, in its own terminal, **started before the call**:

```bash
ssh root@$DROPLET_IP \
  'tcpdump -n -i any -w /root/capture-$(date +%Y%m%d-%H%M).pcap "udp portrange 10000-20000"'
```

`-n` keeps IPs numeric, which is the whole point. Leave it running for the
entire call, then Ctrl-C.

Live view of source IPs while the call runs, if you want the answer
immediately rather than after analysis:

```bash
ssh root@$DROPLET_IP \
  'timeout 120 tcpdump -n -c 200 "udp portrange 10000-20000" | awk "{print \$3}" | cut -d. -f1-4 | sort | uniq -c | sort -rn'
```

## 4. Read the answer

```bash
ssh root@$DROPLET_IP 'tcpdump -n -r /root/capture-*.pcap | awk "{print \$3}" | cut -d. -f1-4 | sort | uniq -c | sort -rn | head'
```

Three possible outcomes, and what each means:

- **A source IP that is not `$DAVID_1`/`$DAVID_2`** — theory confirmed. That
  address is the Oracle NAT egress. Go to step 5.
- **Source IP *is* one of David's two** — theory wrong, and the problem is
  below the firewall. Check the app actually bound the ports it advertised:
  `docker logs <container> | grep -i "bind\|rtp\|port"`, and compare against
  the SDP answer in the 200 OK.
- **No packets at all** — media is not leaving his side, or is being dropped
  before us. Send him the pcap timestamp and ask for his egress capture.

## 5. Re-lock immediately after the call

Do not leave the range open. Replace the wildcard with the address the capture
actually showed:

```bash
export MEDIA_IP=<from step 4>

doctl compute firewall remove-rules $FW --inbound-rules \
  "protocol:udp,ports:10000-20000,address:0.0.0.0/0"

doctl compute firewall add-rules $FW --inbound-rules \
  "protocol:udp,ports:10000-20000,address:${MEDIA_IP}"

doctl compute firewall get $FW --format InboundRules
```

If the capture showed a NAT pool rather than a single address, allowlist the
range David confirms, not a guess inferred from one call.

## 6. Confirm the capture produced a vCon

```bash
ssh root@$DROPLET_IP 'docker logs --tail 100 <container> | grep -i "session\|vcon\|packet"'
curl -s http://$DROPLET_IP:8080/metrics   # from the admin IP
```

Then verify the vCon itself, which is what actually proves interop:

- two parties, names from rs-metadata (David's test users are Star Wars names)
- one audio dialog per stream with non-zero duration
- `session_metadata` attachment carrying the `vendor_extension` block with the
  real numbers (`callingPartyNumber` / `calledPartyNumber`)
- **check the extension's `version`** — their instance was upgraded to 1.1 on
  2026-07-25 and we have never seen a 1.1 payload. If it reads `1.1`, save the
  raw rs-metadata as a fixture in `tests/test_netsapiens_metadata.py`; the
  parser is schema-agnostic and should carry the new fields through, but that
  has only been proven against a synthetic 1.1 sample.

## 7. Evidence for David

Promised in the 2026-07-20 mail: a metrics/health readout, a sample of the
recording as a vCon JSON object, and optionally a live webhook endpoint. Also
still open: our test certificate, if he prefers pinning to disabling chain
validation.

## Rollback (abort at any point)

Removes only the wildcard rule; the narrow David-only rules are untouched.

```bash
doctl compute firewall remove-rules $FW --inbound-rules \
  "protocol:udp,ports:10000-20000,address:0.0.0.0/0"
```
