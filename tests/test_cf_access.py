"""Cloudflare Access identity (AUTH_SOURCE=cf-access): the JWT is the only thing believed.

Real RS256 keys and real signatures; only the certs endpoint is replaced, by a fetch
function that serves this file's JWKS. Every refusal is asserted through the REAL auth
chain (no dependency overrides), because the chain is what is being tested.
"""

import time

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, OctKey, RSAKey

import api.auth
from api.app import create_app
from api.cf_access import (
    KEYS_TTL_SECONDS,
    MIN_REFETCH_SECONDS,
    AccessKeyCache,
    AccessTokenError,
    verify_access_jwt,
)

TEAM = "example.cloudflareaccess.com"
AUD = "a" * 64
ADMIN_EMAIL = "magnus@example.com"
READER_EMAIL = "someone.else@example.com"

SIGNING_KEY = RSAKey.generate_key(2048, parameters={"kid": "k1"})
# Same kid, different key: what a forger who read the kid off a real token would use.
FORGER_KEY = RSAKey.generate_key(2048, parameters={"kid": "k1"})
ROTATED_KEY = RSAKey.generate_key(2048, parameters={"kid": "k2"})


def jwks(*keys: RSAKey) -> dict:
    return KeySet(list(keys)).as_dict(private=False)


def token(
    *,
    key: RSAKey | OctKey = SIGNING_KEY,
    alg: str = "RS256",
    kid: str = "k1",
    **overrides,
) -> str:
    now = int(time.time())
    claims = {
        "aud": [AUD],
        "email": ADMIN_EMAIL,
        "exp": now + 3600,
        "iat": now,
        "nbf": now,
        "iss": f"https://{TEAM}",
        "sub": "0000",
        "type": "app",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode({"alg": alg, "kid": kid}, claims, key, algorithms=[alg])


@pytest.fixture
def served_keys(monkeypatch):
    """What the certs endpoint serves, and how often it was asked."""
    state = {"jwks": jwks(SIGNING_KEY), "calls": 0, "fail": False}

    def fetch(team_domain):
        assert team_domain == TEAM
        state["calls"] += 1
        if state["fail"]:
            raise ConnectionError("certs endpoint down")
        return state["jwks"]

    monkeypatch.setattr(api.auth, "_access_keys", AccessKeyCache(fetch=fetch))
    return state


@pytest.fixture
def client(monkeypatch, served_keys):
    monkeypatch.setenv("ALLOWED_ENTRA_EMAIL", ADMIN_EMAIL)
    monkeypatch.setenv("AUTH_SOURCE", "cf-access")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CF_ACCESS_AUD", AUD)
    monkeypatch.delenv("INGRESS_SHARED_SECRET", raising=False)
    return TestClient(create_app())


def get(client, assertion=None, **headers):
    if assertion is not None:
        headers["Cf-Access-Jwt-Assertion"] = assertion
    return client.get("/", headers=headers)


class TestAccepted:
    def test_valid_token_names_the_admin(self, client):
        r = get(client, token())
        assert r.status_code == 200
        assert "role-admin" in r.text

    def test_valid_token_for_another_user_is_a_reader_who_cannot_write(self, client):
        assertion = token(email=READER_EMAIL)
        r = get(client, assertion)
        assert r.status_code == 200
        assert "role-reader" in r.text
        w = client.post("/products", headers={"Cf-Access-Jwt-Assertion": assertion}, json={})
        assert w.status_code == 403

    def test_aud_as_a_plain_string_is_accepted_too(self, client):
        assert get(client, token(aud=AUD)).status_code == 200

    def test_a_rotated_key_is_picked_up_without_waiting_for_the_ttl(self, client, served_keys):
        assert get(client, token()).status_code == 200
        served_keys["jwks"] = jwks(SIGNING_KEY, ROTATED_KEY)
        # The rotation needs a refetch, which the spacing allows only after it has passed.
        api.auth._access_keys._attempted_at -= MIN_REFETCH_SECONDS
        assert get(client, token(key=ROTATED_KEY, kid="k2")).status_code == 200


class TestRefused:
    """The five U3 cases first, then the ones a forger would try next."""

    def test_wrong_aud(self, client):
        assert get(client, token(aud=["b" * 64])).status_code == 403

    def test_expired(self, client):
        past = int(time.time()) - 7200
        assert get(client, token(exp=past, iat=past - 60, nbf=past - 60)).status_code == 403

    def test_forged_headers_without_a_token(self, client):
        """Both headers an attacker would reach for, and no assertion: nobody."""
        r = get(
            client,
            **{
                "X-Auth-Request-Email": ADMIN_EMAIL,
                "Cf-Access-Authenticated-User-Email": ADMIN_EMAIL,
            },
        )
        assert r.status_code == 403

    def test_forged_email_header_beside_a_reader_token_does_not_promote(self, client):
        r = get(
            client,
            token(email=READER_EMAIL),
            **{
                "X-Auth-Request-Email": ADMIN_EMAIL,
                "Cf-Access-Authenticated-User-Email": ADMIN_EMAIL,
            },
        )
        assert r.status_code == 200
        assert "role-reader" in r.text

    def test_missing_token(self, client):
        assert get(client).status_code == 403

    def test_service_token_without_email_is_nobody(self, client):
        """Measured on the lab (home-server U2): a service token's assertion verifies
        and carries no email. That is a machine, not the admin and not a reader."""
        assert get(client, token(email=None, common_name="abc.access")).status_code == 403

    def test_signed_by_another_key_with_the_same_kid(self, client):
        assert get(client, token(key=FORGER_KEY)).status_code == 403

    def test_unknown_kid(self, client):
        assert get(client, token(key=ROTATED_KEY, kid="k2")).status_code == 403

    def test_hs256_with_the_public_key_as_secret(self, client):
        public_pem = SIGNING_KEY.as_pem(private=False)
        hmac_key = OctKey.import_key(public_pem)
        assert get(client, token(key=hmac_key, alg="HS256")).status_code == 403

    def test_alg_none(self, client):
        good = token()
        header, payload, _ = good.split(".")
        # {"alg":"none","kid":"k1"} base64url, and no signature.
        unsigned = "eyJhbGciOiJub25lIiwia2lkIjoiazEifQ." + payload + "."
        assert header != unsigned.split(".")[0]
        assert get(client, unsigned).status_code == 403

    def test_wrong_issuer(self, client):
        assert get(client, token(iss="https://evil.cloudflareaccess.com")).status_code == 403

    def test_not_yet_valid(self, client):
        future = int(time.time()) + 7200
        assert get(client, token(nbf=future, iat=future)).status_code == 403

    def test_garbage(self, client):
        assert get(client, "not-a-jwt").status_code == 403

    def test_missing_aud_setting_fails_closed(self, client, monkeypatch):
        monkeypatch.delenv("CF_ACCESS_AUD")
        assert get(client, token()).status_code == 403

    def test_missing_team_setting_fails_closed(self, client, monkeypatch):
        monkeypatch.delenv("CF_ACCESS_TEAM_DOMAIN")
        assert get(client, token()).status_code == 403

    def test_unknown_auth_source_fails_closed(self, client, monkeypatch):
        """A typo must not fall back to the header anyone can send."""
        monkeypatch.setenv("AUTH_SOURCE", "cf_access")
        r = get(client, token(), **{"X-Auth-Request-Email": ADMIN_EMAIL})
        assert r.status_code == 403

    def test_certs_endpoint_down_with_nothing_cached(self, client, served_keys):
        served_keys["fail"] = True
        assert get(client, token()).status_code == 403

    def test_ingress_secret_still_applies(self, client, monkeypatch):
        monkeypatch.setenv("INGRESS_SHARED_SECRET", "s3cret")
        assert get(client, token()).status_code == 403
        assert get(client, token(), **{"X-Ingress-Auth": "s3cret"}).status_code == 200


class TestOneSourceAtATime:
    def test_default_source_ignores_an_access_token(self, client, monkeypatch):
        """Dokploy mode believes X-Auth-Request-Email only; an assertion there names nobody."""
        monkeypatch.delenv("AUTH_SOURCE")
        assert get(client, token()).status_code == 403
        r = get(client, **{"X-Auth-Request-Email": ADMIN_EMAIL})
        assert r.status_code == 200


class TestKeyCache:
    def make(self, served):
        now = [1000.0]
        calls = []

        def fetch(team):
            calls.append(team)
            if served.get("fail"):
                raise ConnectionError("down")
            return served["jwks"]

        return AccessKeyCache(fetch=fetch, clock=lambda: now[0]), now, calls

    def test_known_kid_is_served_from_cache(self):
        cache, _, calls = self.make({"jwks": jwks(SIGNING_KEY)})
        cache.key_set(TEAM, "k1")
        cache.key_set(TEAM, "k1")
        assert len(calls) == 1

    def test_unknown_kids_refetch_at_most_once_per_interval(self):
        cache, now, calls = self.make({"jwks": jwks(SIGNING_KEY)})
        cache.key_set(TEAM, "k1")
        # Right after a fetch, an unknown kid is answered from what was just fetched.
        cache.key_set(TEAM, "invented-early")
        assert len(calls) == 1
        now[0] += MIN_REFETCH_SECONDS
        for i in range(20):
            cache.key_set(TEAM, f"invented-{i}")
        assert len(calls) == 2
        now[0] += MIN_REFETCH_SECONDS
        cache.key_set(TEAM, "invented-x")
        assert len(calls) == 3

    def test_ttl_forces_a_refetch_for_a_known_kid(self):
        cache, now, calls = self.make({"jwks": jwks(SIGNING_KEY)})
        cache.key_set(TEAM, "k1")
        now[0] += KEYS_TTL_SECONDS
        cache.key_set(TEAM, "k1")
        assert len(calls) == 2

    def test_failed_refresh_keeps_the_keys_it_had(self):
        served = {"jwks": jwks(SIGNING_KEY)}
        cache, now, _ = self.make(served)
        cache.key_set(TEAM, "k1")
        served["fail"] = True
        now[0] += KEYS_TTL_SECONDS
        claims = verify_access_jwt(token(), team_domain=TEAM, audience=AUD, keys=cache)
        assert claims["email"] == ADMIN_EMAIL

    def test_nothing_cached_and_fetch_failing_refuses(self):
        cache, _, _ = self.make({"fail": True})
        with pytest.raises(AccessTokenError):
            cache.key_set(TEAM, "k1")
