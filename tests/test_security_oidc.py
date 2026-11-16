"""OIDC: PKCE, state, nonce, walidacja tokenu ID (RS256) na zamockowanym dostawcy."""
import base64
import hashlib
import json
import random
import time
from urllib.parse import parse_qs, urlparse

import pytest

from src.security.config import OIDCConfig
from src.security.providers.base import AuthError
from src.security.providers.oidc import OIDCProvider, make_pkce_pair, rs256_verify
from tests.test_security_utils import REMOTE, client, make_app, make_cfg

ISS = "https://idp.example.gov.pl/realms/erolnik"
DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _int_b64(i: int) -> str:
    return _b64(i.to_bytes((i.bit_length() + 7) // 8, "big"))


def _prime(bits: int, rnd: random.Random) -> int:
    def probable(n: int) -> bool:
        d, s = n - 1, 0
        while d % 2 == 0:
            d, s = d // 2, s + 1
        for _ in range(20):
            x = pow(rnd.randrange(2, n - 1), d, n)
            if x in (1, n - 1):
                continue
            for _ in range(s - 1):
                x = pow(x, 2, n)
                if x == n - 1:
                    break
            else:
                return False
        return True

    while True:
        c = rnd.getrandbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if all(c % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31)) and probable(c):
            return c


@pytest.fixture(scope="module")
def rsa_key():
    rnd = random.Random(20261115)
    e = 65537
    while True:
        p, q = _prime(1024, rnd), _prime(1024, rnd)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            return p * q, e, pow(e, -1, phi)


def sign(key, header: dict, claims: dict) -> str:
    n, _, d = key
    k = (n.bit_length() + 7) // 8
    si = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    t = DIGEST_INFO + hashlib.sha256(si.encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return f"{si}.{_b64(pow(int.from_bytes(em, 'big'), d, n).to_bytes(k, 'big'))}"


class FakeIdP:
    def __init__(self, key):
        self.key, self.posted, self.nonce_override, self.claims_extra = key, [], None, {}

    def get_json(self, url):
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": ISS, "authorization_endpoint": ISS + "/auth", "token_endpoint": ISS + "/token",
                    "jwks_uri": ISS + "/certs"}
        if url == ISS + "/certs":
            n, e, _ = self.key
            return {"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "n": _int_b64(n), "e": _int_b64(e)}]}
        raise AssertionError(url)

    def post_form(self, url, data, auth=None):
        self.posted.append((url, data, auth))
        now = int(time.time())
        claims = {"iss": ISS, "aud": "mrolnik", "sub": "abc-123", "iat": now, "exp": now + 300,
                  "nonce": self.nonce_override or self.expected_nonce, "name": "Jan Rolnik", "roles": ["doradca"],
                  **self.claims_extra}
        return {"id_token": sign(self.key, {"alg": "RS256", "kid": "k1"}, claims), "access_token": "x"}


def oidc_cfg():
    return OIDCConfig(issuer=ISS, client_id="mrolnik", client_secret="sekret",
                      redirect_uri="http://testserver/auth/callback")


def test_pkce_pair_is_rfc7636():
    v, c = make_pkce_pair()
    assert 43 <= len(v) <= 128 and set(v) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    assert c == _b64(hashlib.sha256(v.encode()).digest())
    assert make_pkce_pair()[0] != v


def test_begin_login_builds_authorization_url(rsa_key):
    idp = FakeIdP(rsa_key)
    url, flow = OIDCProvider(oidc_cfg(), idp.get_json, idp.post_form).begin_login("/web")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith(ISS + "/auth?")
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
    assert q["state"] == flow["state"] and q["nonce"] == flow["nonce"] and len(flow["state"]) >= 32
    assert q["code_challenge"] == _b64(hashlib.sha256(flow["verifier"].encode()).digest())
    assert q["redirect_uri"] == "http://testserver/auth/callback" and q["scope"] == "openid profile"
    _, flow2 = OIDCProvider(oidc_cfg(), idp.get_json, idp.post_form).begin_login("/web")
    assert flow2["state"] != flow["state"] and flow2["nonce"] != flow["nonce"]


def test_discovery_issuer_mismatch_rejected(rsa_key):
    idp = FakeIdP(rsa_key)
    cfg = oidc_cfg()
    cfg.issuer = "https://inny.example"
    with pytest.raises(AuthError):
        OIDCProvider(cfg, idp.get_json, idp.post_form).begin_login("/web")


def _provider_and_flow(rsa_key):
    idp = FakeIdP(rsa_key)
    prov = OIDCProvider(oidc_cfg(), idp.get_json, idp.post_form)
    _, flow = prov.begin_login("/web")
    idp.expected_nonce = flow["nonce"]
    return idp, prov, flow


def test_complete_login_valid(rsa_key):
    idp, prov, flow = _provider_and_flow(rsa_key)
    user = prov.complete_login({"code": "kod", "state": flow["state"]}, flow)
    assert user.id == "oidc:abc-123" and user.role == "doradca" and user.name == "Jan Rolnik"
    _, data, auth = idp.posted[0]
    assert data["code_verifier"] == flow["verifier"] and data["grant_type"] == "authorization_code"
    assert auth == ("mrolnik", "sekret")


def test_complete_login_rejects_bad_state(rsa_key):
    _, prov, flow = _provider_and_flow(rsa_key)
    with pytest.raises(AuthError):
        prov.complete_login({"code": "kod", "state": "inny"}, flow)


def test_complete_login_rejects_bad_nonce(rsa_key):
    idp, prov, flow = _provider_and_flow(rsa_key)
    idp.nonce_override = "obcy-nonce"
    with pytest.raises(AuthError):
        prov.complete_login({"code": "kod", "state": flow["state"]}, flow)


@pytest.mark.parametrize("extra", [{"aud": "inna-aplikacja"}, {"iss": "https://zly.example"}, {"exp": 1000}])
def test_complete_login_rejects_bad_claims(rsa_key, extra):
    idp, prov, flow = _provider_and_flow(rsa_key)
    idp.claims_extra = extra
    with pytest.raises(AuthError):
        prov.complete_login({"code": "kod", "state": flow["state"]}, flow)


def test_rs256_signature_tamper_and_alg_none(rsa_key):
    n, e, _ = rsa_key
    tok = sign(rsa_key, {"alg": "RS256"}, {"a": 1})
    h, p, s = tok.split(".")
    sig = base64.urlsafe_b64decode(s + "==")
    assert rs256_verify(n, e, f"{h}.{p}".encode(), sig)
    assert not rs256_verify(n, e, f"{h}.{p}x".encode(), sig)
    _, prov, flow = _provider_and_flow(rsa_key)
    meta = prov.metadata()
    none_header = _b64(json.dumps({"alg": "none"}).encode())
    none_tok = f"{none_header}.{p}."
    with pytest.raises(AuthError):
        prov.validate_id_token(none_tok, flow["nonce"], meta)


def test_full_http_flow(rsa_key):
    app = make_app(make_cfg(auth_mode="oidc", oidc=oidc_cfg()))
    idp = FakeIdP(rsa_key)
    app.state.security.provider = OIDCProvider(oidc_cfg(), idp.get_json, idp.post_form)
    c = client(app, REMOTE)
    assert c.get("/api/data").status_code == 401
    r = c.get("/auth/login?next=/web", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(ISS + "/auth?")
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    idp.expected_nonce = q["nonce"]
    bad = c.get("/auth/callback?code=kod&state=zly", follow_redirects=False)
    assert bad.status_code == 400
    r = c.get("/auth/login?next=/web", follow_redirects=False)
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    idp.expected_nonce = q["nonce"]
    r = c.get(f"/auth/callback?code=kod&state={q['state']}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/web"
    me = c.get("/auth/me").json()
    assert me["authenticated"] and me["user"]["role"] == "doradca" and me["auth_mode"] == "oidc"
    assert c.get("/api/data").status_code == 200
