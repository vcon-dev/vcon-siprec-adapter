"""NetSapiens metadata 1.1: a real attended transfer, as three SIPREC sessions.

Verbatim from David Wang, 2026-07-29 20:36 UTC. The call was:

    1001 (Din Djarin) called 1002 (Boba Fett), who answered
    1002 made a consultation call to 1006 (Obi Wan Kenobi), who answered
    post transfer, 1001 was talking with 1006

The mechanism matters more than the payload. David, 2026-07-29 20:07 UTC:

    "Our side closes the existing SIPREC session start a new SIPREC session
    upon changes in the parties. However, these sequence of SIPREC session
    share the same group_id but with an incrementing groupSeq inside the
    NetSapiens meta data session. Attended xfer would be a good example to
    demonstrate how the original group survive and continue with incremented
    groupSeq post xfer, while the consultation group terminated."

So NetSapiens does **not** re-INVITE on party change. Each leg change is a
BYE plus a fresh INVITE, and the only thing tying them together is the
metadata: `group_id`, `groupSeq`, and the transfer references.

Trace: https://core1-phx.dw.nseng.dev/ns-api/?object=trace&action=export&k=2026072987519cd1233c9015a9b5dcbd91e57611&location=Y29yZTEtcGh4LmR3Lm5zZW5nLmRldg==
"""

# Session 1: the original call, 1001 -> 1002. groupSeq 0.
INITIAL = """<recording xmlns="urn:ietf:params:xml:ns:recording:1">
  <datamode>complete</datamode>
  <group group_id="58cc3154ca0bdd2b0efbf9a04139526e">
    <associate-time>2026-07-29T20:17:21Z</associate-time>
  </group>
  <session session_id="20260729201721000631">
    <group-ref>58cc3154ca0bdd2b0efbf9a04139526e</group-ref>
    <sipSessionID>58cc3154ca0bdd2b0efbf9a04139526e;remote=20260729201719000629-0018491486ec5db64acd5aca455acfe8</sipSessionID>
  </session>
  <participant participant_id="58cc3154ca0bdd2b0efbf9a04139526e">
    <nameID aor="sip:1001@dwang.netsapiens.com">
      <name>Din Djarin</name>
    </nameID>
  </participant>
  <participant participant_id="20260729201719000629-0018491486ec5db64acd5aca455acfe8">
    <nameID aor="sip:1002@dwang.netsapiens.com">
      <name>Boba Fett</name>
    </nameID>
  </participant>
  <stream stream_id="wJwvQ18yqrywIhoZ00975F" session_id="20260729201721000631">
    <label>1</label>
  </stream>
  <stream stream_id="LjU9KTyGQlU7nWe8009760" session_id="20260729201721000631">
    <label>2</label>
  </stream>
  <participantsessionassoc participant_id="58cc3154ca0bdd2b0efbf9a04139526e" session_id="20260729201721000631"/>
  <participantsessionassoc participant_id="20260729201719000629-0018491486ec5db64acd5aca455acfe8" session_id="20260729201721000631"/>
  <participantstreamassoc participant_id="58cc3154ca0bdd2b0efbf9a04139526e">
    <send>wJwvQ18yqrywIhoZ00975F</send>
    <recv>LjU9KTyGQlU7nWe8009760</recv>
  </participantstreamassoc>
  <participantstreamassoc participant_id="20260729201719000629-0018491486ec5db64acd5aca455acfe8">
    <send>LjU9KTyGQlU7nWe8009760</send>
    <recv>wJwvQ18yqrywIhoZ00975F</recv>
  </participantstreamassoc>
  <netsapiensExtension xmlns="http://schema.netsapiens.com/netsapiensSipRec" version="1.1">
    <groupSeq>0</groupSeq>
    <serviceProviderID>tenant_1_23584</serviceProviderID>
    <user userID="1001@dwang.netsapiens.com">
      <resellerID>NetSapiens</resellerID>
      <site>Mandalore</site>
      <department>Marketing</department>
      <callType>origCall</callType>
    </user>
    <user userID="1002@dwang.netsapiens.com">
      <resellerID>NetSapiens</resellerID>
      <site>San Diego</site>
      <department>Engineering</department>
      <callType>termCall</callType>
    </user>
    <callingParty participant_id="58cc3154ca0bdd2b0efbf9a04139526e">
      <nameID aor="sip:1001@dwang.netsapiens.com">
        <uid>1001@dwang.netsapiens.com</uid>
        <name>Din Djarin</name>
      </nameID>
      <number>8587641001</number>
    </callingParty>
    <calledParty participant_id="20260729201719000629-0018491486ec5db64acd5aca455acfe8">
      <nameID aor="sip:1002@dwang.netsapiens.com">
        <uid>1002@dwang.netsapiens.com</uid>
        <name>Boba Fett</name>
      </nameID>
      <number>8587641002</number>
    </calledParty>
    <byAction>ForwardSRing</byAction>
    <byUserID>1002@dwang.netsapiens.com</byUserID>
  </netsapiensExtension>
</recording>"""

# Session 2: the consultation call, 1002 -> 1006. Its OWN group, groupSeq 0.
# Note the group_id format: a SIP Call-ID with an @host, not a hex hash.
CONSULTATION = """<recording xmlns="urn:ietf:params:xml:ns:recording:1">
  <datamode>complete</datamode>
  <group group_id="5ed05251-7abca882-e05368bf@192.168.0.245">
    <associate-time>2026-07-29T20:17:41Z</associate-time>
  </group>
  <session session_id="20260729201741000664">
    <group-ref>5ed05251-7abca882-e05368bf@192.168.0.245</group-ref>
    <sipSessionID>5ed05251-7abca882-e05368bf@192.168.0.245;remote=20260729201738000654-0018491486ec5db64acd5aca455acfe8</sipSessionID>
  </session>
  <participant participant_id="5ed05251-7abca882-e05368bf@192.168.0.245">
    <nameID aor="sip:1002@dwang.netsapiens.com">
      <name>Boba Fett</name>
    </nameID>
  </participant>
  <participant participant_id="20260729201738000654-0018491486ec5db64acd5aca455acfe8">
    <nameID aor="sip:1006@dwang.netsapiens.com">
      <name>Obi Wan Kenobi</name>
    </nameID>
  </participant>
  <stream stream_id="k2H4V3wORSj9JKES00977C" session_id="20260729201741000664">
    <label>1</label>
  </stream>
  <stream stream_id="uEJ5NSEvncwFIg2500977D" session_id="20260729201741000664">
    <label>2</label>
  </stream>
  <participantsessionassoc participant_id="5ed05251-7abca882-e05368bf@192.168.0.245" session_id="20260729201741000664"/>
  <participantsessionassoc participant_id="20260729201738000654-0018491486ec5db64acd5aca455acfe8" session_id="20260729201741000664"/>
  <participantstreamassoc participant_id="5ed05251-7abca882-e05368bf@192.168.0.245">
    <send>k2H4V3wORSj9JKES00977C</send>
    <recv>uEJ5NSEvncwFIg2500977D</recv>
  </participantstreamassoc>
  <participantstreamassoc participant_id="20260729201738000654-0018491486ec5db64acd5aca455acfe8">
    <send>uEJ5NSEvncwFIg2500977D</send>
    <recv>k2H4V3wORSj9JKES00977C</recv>
  </participantstreamassoc>
  <netsapiensExtension xmlns="http://schema.netsapiens.com/netsapiensSipRec" version="1.1">
    <groupSeq>0</groupSeq>
    <serviceProviderID>tenant_1_23584</serviceProviderID>
    <user userID="1002@dwang.netsapiens.com">
      <resellerID>NetSapiens</resellerID>
      <site>San Diego</site>
      <department>Engineering</department>
      <callType>origCall</callType>
    </user>
    <callingParty participant_id="5ed05251-7abca882-e05368bf@192.168.0.245">
      <nameID aor="sip:1002@dwang.netsapiens.com">
        <uid>1002@dwang.netsapiens.com</uid>
        <name>Boba Fett</name>
      </nameID>
      <number>8587641002</number>
    </callingParty>
    <calledParty participant_id="20260729201738000654-0018491486ec5db64acd5aca455acfe8">
      <nameID aor="sip:1006@dwang.netsapiens.com">
        <uid>1006@dwang.netsapiens.com</uid>
        <name>Obi Wan Kenobi</name>
      </nameID>
      <number>8587641006</number>
    </calledParty>
    <byAction>ForwardSRing</byAction>
    <byUserID>1006@dwang.netsapiens.com</byUserID>
  </netsapiensExtension>
</recording>"""

# Session 3: post transfer, 1001 <-> 1006. ORIGINAL group_id, groupSeq 1.
# byAction XferSup, and xferredGroupID points at the consultation group.
# Note both stream_ids are REUSED: label 1 is the original call's stream,
# label 2 is the consultation call's stream.
POST_TRANSFER = """<recording xmlns="urn:ietf:params:xml:ns:recording:1">
  <datamode>complete</datamode>
  <group group_id="58cc3154ca0bdd2b0efbf9a04139526e">
    <associate-time>2026-07-29T20:17:48Z</associate-time>
  </group>
  <session session_id="20260729201748000671">
    <group-ref>58cc3154ca0bdd2b0efbf9a04139526e</group-ref>
    <sipSessionID>58cc3154ca0bdd2b0efbf9a04139526e;remote=20260729201738000654-0018491486ec5db64acd5aca455acfe8</sipSessionID>
  </session>
  <participant participant_id="58cc3154ca0bdd2b0efbf9a04139526e">
    <nameID aor="sip:1001@dwang.netsapiens.com">
      <name>Din Djarin</name>
    </nameID>
  </participant>
  <participant participant_id="20260729201738000654-0018491486ec5db64acd5aca455acfe8">
    <nameID aor="sip:1006@dwang.netsapiens.com">
      <name>Obi Wan Kenobi</name>
    </nameID>
  </participant>
  <stream stream_id="wJwvQ18yqrywIhoZ00975F" session_id="20260729201748000671">
    <label>1</label>
  </stream>
  <stream stream_id="uEJ5NSEvncwFIg2500977D" session_id="20260729201748000671">
    <label>2</label>
  </stream>
  <participantsessionassoc participant_id="58cc3154ca0bdd2b0efbf9a04139526e" session_id="20260729201748000671"/>
  <participantsessionassoc participant_id="20260729201738000654-0018491486ec5db64acd5aca455acfe8" session_id="20260729201748000671"/>
  <participantstreamassoc participant_id="58cc3154ca0bdd2b0efbf9a04139526e">
    <send>wJwvQ18yqrywIhoZ00975F</send>
    <recv>uEJ5NSEvncwFIg2500977D</recv>
  </participantstreamassoc>
  <participantstreamassoc participant_id="20260729201738000654-0018491486ec5db64acd5aca455acfe8">
    <send>uEJ5NSEvncwFIg2500977D</send>
    <recv>wJwvQ18yqrywIhoZ00975F</recv>
  </participantstreamassoc>
  <netsapiensExtension xmlns="http://schema.netsapiens.com/netsapiensSipRec" version="1.1">
    <groupSeq>1</groupSeq>
    <serviceProviderID>tenant_1_23584</serviceProviderID>
    <user userID="1001@dwang.netsapiens.com">
      <resellerID>NetSapiens</resellerID>
      <site>Mandalore</site>
      <department>Marketing</department>
      <callType>origCall</callType>
    </user>
    <callingParty participant_id="58cc3154ca0bdd2b0efbf9a04139526e">
      <nameID aor="sip:1001@dwang.netsapiens.com">
        <uid>1001@dwang.netsapiens.com</uid>
        <name>Din Djarin</name>
      </nameID>
      <number>8587641001</number>
    </callingParty>
    <calledParty participant_id="20260729201738000654-0018491486ec5db64acd5aca455acfe8">
      <nameID aor="sip:1006@dwang.netsapiens.com">
        <uid>1006@dwang.netsapiens.com</uid>
        <name>Obi Wan Kenobi</name>
      </nameID>
      <number>8587641006</number>
    </calledParty>
    <byAction>XferSup</byAction>
    <byAor>sip:1002@dwang.netsapiens.com</byAor>
    <byUserID>1002@dwang.netsapiens.com</byUserID>
    <xferredSessionID>5ed05251-7abca882-e05368bf@192.168.0.245</xferredSessionID>
    <xferredGroupID>5ed05251-7abca882-e05368bf@192.168.0.245</xferredGroupID>
  </netsapiensExtension>
</recording>"""

ORIGINAL_GROUP = "58cc3154ca0bdd2b0efbf9a04139526e"
CONSULT_GROUP = "5ed05251-7abca882-e05368bf@192.168.0.245"
