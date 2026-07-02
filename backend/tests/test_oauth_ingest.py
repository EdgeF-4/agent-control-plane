"""OAuth2/JWKS run ingest: accept external-IdP JWTs alongside per-project keys.

RS256 tokens are minted here with stdlib integer math against a fixed throwaway
test keypair, and verified through the gateway engine's JWKS verifier (the same
one the hub composes). No pyjwt / cryptography dependency in the test path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

# A throwaway 2048-bit RSA keypair generated for tests only — never a real key.
_N = 22648935044370878196349111235439165716651395967976710319915952048545624289235811274377733733888075990522318660591811148867163957539307582248306545600416851627648525722330265756751980157648915179707620475511666935828048927085249672066445229406142721732659120581796993946411658902782289148503547595701294042698027985815804751158950264632972920984082615948006752014244495880510859696106891065489878766605957418652545048620177610702315819477414595663206303780567164118076076808476249467158182413586529357384564491910043463348330402066626010688963264919292633022819248577170216041682367709489128047233173843383105057278007
_E = 65537
_D = 2993501004842158581210247669581672237631176158118532505166729887613137580196844488741625793108297820008610773121233321200045382001090716350074481559894575107171392187723343485130318020744844946925056205790348337551956296541075005865992471079176774274811073172094016533619448394279570145175057284800412118312521340411379709812349478393300052836702074803763341062453543474128908240041274801637196744995870597486377772455129903819819652116175038449347684845134745295956138087611835237964182802296661390815219073163076312179507613264667549897490702061032504485674858147877420216280564433504313788079780857878045043646769

# EMSA-PKCS1-v1_5 DigestInfo prefix for SHA-256 (RFC 8017 §9.2), same as the gateway.
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_uint(value: int) -> str:
    return _b64u(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def _jwk(kid: str) -> dict:
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
            "n": _b64u_uint(_N), "e": _b64u_uint(_E)}


def jwks_doc(*kids: str) -> dict:
    return {"keys": [_jwk(k) for k in kids]}


def mint_rs256(payload: dict, *, kid: str = "k1") -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    signing_input = (
        _b64u(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64u(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    k = (_N.bit_length() + 7) // 8
    suffix = _DIGEST_INFO + hashlib.sha256(signing_input.encode("ascii")).digest()
    pad_len = k - 3 - len(suffix)
    em = b"\x00\x01" + b"\xff" * pad_len + b"\x00" + suffix
    signature = pow(int.from_bytes(em, "big"), _D, _N).to_bytes(k, "big")
    return signing_input + "." + _b64u(signature)


ISSUER = "https://idp.test/"
AUDIENCE = "agent-control-plane"


def _claims(**over) -> dict:
    now = int(time.time())
    base = {"sub": "svc-agent-1", "roles": ["agent"], "iss": ISSUER,
            "aud": AUDIENCE, "iat": now, "exp": now + 300}
    base.update(over)
    return base


def make_settings(tmp_path, *, kids=("k1",), tenant_slug="acme", project_claim=None) -> Settings:
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps(jwks_doc(*kids)))
    oauth = {
        "enabled": True, "algorithm": "RS256", "jwks_path": str(jwks_path),
        "issuer": ISSUER, "audience": AUDIENCE, "tenant_slug": tenant_slug,
    }
    if project_claim is not None:
        oauth["project_claim"] = project_claim
    return Settings(
        database={"url": f"sqlite+aiosqlite:///{tmp_path}/cp.db"},
        auth={"jwt_secret": secrets.token_hex(32)},
        data_dir=str(tmp_path / "data"),
        eval_scheduler_seconds=3600,
        ingest={"oauth2": oauth},
        bootstrap={
            "tenant_slug": "acme", "tenant_name": "Acme",
            "admin_email": "admin@acme.test",
            "admin_password": "correct horse battery staple",
        },
    )


def _admin_headers(client: TestClient) -> dict:
    r = client.post("/api/v1/auth/login", json={
        "email": "admin@acme.test", "password": "correct horse battery staple"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_project(client, headers, slug="research"):
    r = client.post("/api/v1/projects",
                    json={"slug": slug, "name": slug.title(), "budget_usd": 50.0},
                    headers=headers)
    assert r.status_code == 201, r.text


def test_valid_jwt_opens_a_run(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        admin = _admin_headers(c)
        _make_project(c, admin)
        jwt = mint_rs256(_claims())
        r = c.post("/api/v1/runs",
                   json={"project_slug": "research", "agent_name": "idp-agent"},
                   headers={"Authorization": f"Bearer {jwt}"})
        assert r.status_code == 201, r.text
        assert r.json()["agent_name"] == "idp-agent"
        # And the operator can see the run the JWT-authenticated agent created.
        runs = c.get("/api/v1/runs", headers=admin).json()
        assert any(run["agent_name"] == "idp-agent" for run in runs)


def test_bad_audience_is_rejected(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        admin = _admin_headers(c)
        _make_project(c, admin)
        jwt = mint_rs256(_claims(aud="someone-else"))
        r = c.post("/api/v1/runs", json={"project_slug": "research"},
                   headers={"Authorization": f"Bearer {jwt}"})
        assert r.status_code == 401


def test_expired_jwt_is_rejected(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        admin = _admin_headers(c)
        _make_project(c, admin)
        jwt = mint_rs256(_claims(exp=int(time.time()) - 3600))
        r = c.post("/api/v1/runs", json={"project_slug": "research"},
                   headers={"Authorization": f"Bearer {jwt}"})
        assert r.status_code == 401


def test_bad_signature_is_rejected(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        admin = _admin_headers(c)
        _make_project(c, admin)
        jwt = mint_rs256(_claims())
        tampered = jwt[:-4] + ("aaaa" if not jwt.endswith("aaaa") else "bbbb")
        r = c.post("/api/v1/runs", json={"project_slug": "research"},
                   headers={"Authorization": f"Bearer {tampered}"})
        assert r.status_code == 401


def test_project_claim_scopes_the_token(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, project_claim="acp_project"))) as c:
        admin = _admin_headers(c)
        _make_project(c, admin, "research")
        _make_project(c, admin, "alpha")
        # Claim says research; a run with no explicit slug lands in research.
        jwt = mint_rs256(_claims(acp_project="research"))
        r = c.post("/api/v1/runs", json={"agent_name": "scoped"},
                   headers={"Authorization": f"Bearer {jwt}"})
        assert r.status_code == 201, r.text
        # Trying to write to a different project than the claim is refused.
        r2 = c.post("/api/v1/runs", json={"project_slug": "alpha"},
                    headers={"Authorization": f"Bearer {jwt}"})
        assert r2.status_code == 403
        # A claim naming an unknown project is refused too.
        jwt_bad = mint_rs256(_claims(acp_project="ghost"))
        r3 = c.post("/api/v1/runs", json={},
                    headers={"Authorization": f"Bearer {jwt_bad}"})
        assert r3.status_code == 403


def test_api_keys_still_work_with_oauth_enabled(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        admin = _admin_headers(c)
        _make_project(c, admin)
        key = c.post("/api/v1/projects/research/keys", json={"name": "agent"},
                     headers=admin).json()["key"]
        r = c.post("/api/v1/runs", json={"agent_name": "keyed"},
                   headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 201, r.text


def test_oauth_without_tenant_binding_is_unauthorized(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, tenant_slug=""))) as c:
        admin = _admin_headers(c)
        _make_project(c, admin)
        jwt = mint_rs256(_claims())
        r = c.post("/api/v1/runs", json={"project_slug": "research"},
                   headers={"Authorization": f"Bearer {jwt}"})
        assert r.status_code == 401


def test_jwks_key_store_picks_up_rotation(tmp_path):
    """The cached JWKS store serves from cache and refreshes on an unknown kid."""
    from mcp_gateway.auth import JwksKeyStore

    path = tmp_path / "jwks.json"
    path.write_text(json.dumps(jwks_doc("k1")))
    loads = {"n": 0}

    def loader():
        loads["n"] += 1
        return json.loads(path.read_text())

    clock = {"t": 1000.0}
    store = JwksKeyStore(loader, cache_seconds=300, clock=lambda: clock["t"])

    assert store.get("k1") is not None
    assert loads["n"] == 1
    # Served from cache within the window; no re-fetch.
    assert store.get("k1") is not None
    assert loads["n"] == 1

    # Rotate: publish a new kid. An unknown kid triggers a refresh (past the
    # min-refresh guard), so the rotated key is picked up without a restart.
    path.write_text(json.dumps(jwks_doc("k1", "k2")))
    clock["t"] += 60
    assert store.get("k2") is not None
    assert loads["n"] == 2
