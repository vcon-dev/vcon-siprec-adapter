"""SDP parsing of RTP/SAVP m-lines and RFC 4568 a=crypto attributes."""

from siprec_srs.siprec_parser import SIPRECParser

SAVP_OFFER = (
    "v=0\r\n"
    "o=- 1 1 IN IP4 10.0.0.1\r\n"
    "s=-\r\n"
    "c=IN IP4 10.0.0.1\r\n"
    "t=0 0\r\n"
    "m=audio 20000 RTP/SAVP 0 8\r\n"
    "a=rtpmap:0 PCMU/8000\r\n"
    "a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:WVNfX19zZW1jdGwgKCkgewkyMjA7fQp9CnVubGVz|2^20|1:4\r\n"
    "a=crypto:2 AES_CM_128_HMAC_SHA1_32 inline:WVNfX19zZW1jdGwgKCkgewkyMjA7fQp9CnVubGVz\r\n"
    "a=sendonly\r\n"
    "a=label:1\r\n"
    "m=audio 20002 RTP/AVP 0\r\n"
    "a=label:2\r\n"
)


def test_profile_and_crypto_parsed():
    savp, avp = SIPRECParser().parse_sdp(SAVP_OFFER)
    assert savp["profile"] == "RTP/SAVP"
    assert avp["profile"] == "RTP/AVP"
    assert avp["crypto"] == []

    c1, c2 = savp["crypto"]
    assert c1["tag"] == 1
    assert c1["suite"] == "AES_CM_128_HMAC_SHA1_80"
    assert c1["key"] == "WVNfX19zZW1jdGwgKCkgewkyMjA7fQp9CnVubGVz"
    assert c1["lifetime"] == "2^20"
    assert c1["mki"] == (1, 4)
    assert c2["tag"] == 2
    assert c2["suite"] == "AES_CM_128_HMAC_SHA1_32"
    assert c2["lifetime"] is None and c2["mki"] is None


def test_malformed_crypto_ignored():
    sdp = SAVP_OFFER.replace("a=crypto:2 AES_CM_128_HMAC_SHA1_32 inline:",
                             "a=crypto:2 AES_CM_128_HMAC_SHA1_32 uri:")
    savp, _ = SIPRECParser().parse_sdp(sdp)
    assert [c["tag"] for c in savp["crypto"]] == [1]
