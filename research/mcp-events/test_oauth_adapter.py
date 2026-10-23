"""Real SDK authorization flow with temporary fixtures and no network."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from starlette.applications import Starlette
from starlette.testclient import TestClient

spec = importlib.util.spec_from_file_location("oauth_adapter", Path(__file__).with_name("oauth_adapter.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ISSUER = "https://research.example.test"
CALLBACK = "https://chatgpt.com/connector/oauth/fixture"
VERIFIER = "a" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.sqlite"
        self.adapter = self.make_adapter()
        self.client = TestClient(Starlette(routes=self.adapter.build_auth_routes()), base_url=ISSUER,
                                 client=("127.0.0.1", 1234), follow_redirects=False)
        self.addCleanup(self.client.close)

    def make_adapter(self, **options):
        return module.OAuthAdapter(self.path, ISSUER, ISSUER + "/mcp", "fixture-user", "fixture-client", CALLBACK, **options)

    def authorize(self, **overrides):
        params = {"client_id": "fixture-client", "response_type": "code", "redirect_uri": CALLBACK,
                  "scope": "events:observe", "code_challenge": CHALLENGE, "code_challenge_method": "S256",
                  "resource": ISSUER + "/mcp", "state": "fixture-state"}
        params.update(overrides)
        return self.client.get("/authorize", params={k: v for k, v in params.items() if v is not None})

    def consent_form(self, **overrides):
        response = self.authorize(**overrides)
        self.assertEqual(response.status_code, 302)
        ticket = parse_qs(urlsplit(response.headers["location"]).query)["ticket"][0]
        response = self.client.get("/oauth/consent", params={"ticket": ticket}, headers={"Remote-User": "fixture-user"})
        self.assertEqual(response.status_code, 200)
        csrf = re.search('name="csrf" value="([^"]+)"', response.text).group(1)
        return {"ticket": ticket, "csrf": csrf, "decision": "allow"}

    def grant(self, **overrides):
        response = self.client.post("/oauth/consent", data=self.consent_form(**overrides),
                                    headers={"Remote-User": "fixture-user", "Origin": ISSUER})
        self.assertEqual(response.status_code, 303)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(query["iss"], [ISSUER])
        self.assertEqual(query["state"], ["fixture-state"])
        return query["code"][0]

    def token(self, code, **overrides):
        data = {"grant_type": "authorization_code", "client_id": "fixture-client", "code": code,
                "redirect_uri": CALLBACK, "code_verifier": VERIFIER, "resource": ISSUER + "/mcp"}
        data.update(overrides)
        return self.client.post("/token", data=data)

    def test_sdk_pkce_code_single_use_and_persistent_hashes(self):
        code = self.grant()
        self.assertEqual(self.token(code, code_verifier="wrong").status_code, 400)
        result = self.token(code)
        self.assertEqual(result.status_code, 200, result.text)
        tokens = result.json()
        self.assertEqual(tokens["expires_in"], 600)
        self.assertEqual(self.token(code).status_code, 400)
        self.assertNotIn(code.encode(), self.path.read_bytes())
        for key in ("access_token", "refresh_token"):
            self.assertNotIn(tokens[key].encode(), self.path.read_bytes())
        import asyncio
        loaded = asyncio.run(self.make_adapter().load_access_token(tokens["access_token"]))
        self.assertEqual(loaded.subject, "fixture-user")
        self.assertEqual(loaded.resource, ISSUER + "/mcp")
        self.assertEqual(loaded.claims, {"iss": ISSUER})

    def test_refresh_rotation_scope_expiry_and_family_revocation(self):
        tokens = self.token(self.grant()).json()
        def refresh(token, **kwargs):
            return self.client.post("/token", data={"grant_type": "refresh_token", "client_id": "fixture-client",
                                    "refresh_token": token, **kwargs})
        self.assertEqual(refresh(tokens["refresh_token"], scope="events:write").status_code, 400)
        result = refresh(tokens["refresh_token"], scope="events:observe")
        self.assertEqual(result.status_code, 200, result.text)
        newer = result.json()
        self.assertEqual(refresh(tokens["refresh_token"]).status_code, 400)
        import asyncio
        self.assertIsNone(asyncio.run(self.adapter.load_access_token(tokens["access_token"])))
        response = self.client.post("/revoke", data={"token": newer["refresh_token"], "client_id": "fixture-client"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(refresh(newer["refresh_token"]).status_code, 400)
        self.assertIsNone(asyncio.run(self.adapter.load_access_token(newer["access_token"])))

    def test_consent_get_does_not_grant_and_post_needs_identity_origin_csrf(self):
        form = self.consent_form()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM oauth_items WHERE kind='code'").fetchone()[0], 0)
        cases = [({}, form), ({"Remote-User": "other", "Origin": ISSUER}, form),
                 ({"Remote-User": "fixture-user", "Origin": "https://evil.test"}, form),
                 ({"Remote-User": "fixture-user", "Origin": ISSUER}, {**form, "csrf": "wrong"})]
        for headers, data in cases:
            self.assertEqual(self.client.post("/oauth/consent", data=data, headers=headers).status_code, 403)
        response = self.client.post("/oauth/consent", data=form, headers={"Remote-User": "fixture-user", "Origin": ISSUER})
        self.assertEqual(response.status_code, 303)
        self.assertNotEqual(self.client.post("/oauth/consent", data=form,
                            headers={"Remote-User": "fixture-user", "Origin": ISSUER}).status_code, 303)

    def test_nonloopback_identity_and_wrong_host_rejected(self):
        form = self.consent_form()
        with TestClient(Starlette(routes=self.adapter.build_auth_routes()), base_url=ISSUER,
                        client=("192.0.2.1", 1234)) as remote:
            self.assertEqual(remote.get("/oauth/consent", params={"ticket": form["ticket"]},
                                       headers={"Remote-User": "fixture-user"}).status_code, 403)
        self.assertEqual(self.client.get("/oauth/consent", params={"ticket": form["ticket"]},
                         headers={"Remote-User": "fixture-user", "Host": "evil.test"}).status_code, 403)

    def test_exact_audience_and_registered_redirect(self):
        for changes in ({"resource": "https://other.test/mcp"}, {"scope": "events:write"},
                        {"code_challenge_method": "plain"}):
            response = self.authorize(**changes)
            self.assertNotIn("/oauth/consent", response.headers.get("location", ""))
            if response.status_code == 302:
                self.assertEqual(parse_qs(urlsplit(response.headers["location"]).query)["iss"], [ISSUER])
        self.assertEqual(self.authorize(redirect_uri="https://evil.test/callback").status_code, 400)
        code = self.grant()
        self.assertEqual(self.token(code, resource="https://other.test/mcp").status_code, 400)
        self.assertEqual(self.token(code).status_code, 200)

    def test_expired_code_and_refresh_are_rejected(self):
        code = self.grant()
        with sqlite3.connect(self.path) as db:
            expires = db.execute("SELECT expires FROM oauth_items WHERE kind='code'").fetchone()[0]
            self.assertGreater(expires, time.time() + 55)
            self.assertLessEqual(expires, time.time() + 60)
            db.execute("UPDATE oauth_items SET expires=0 WHERE kind='code'")
        self.assertEqual(self.token(code).status_code, 400)
        tokens = self.token(self.grant()).json()
        with sqlite3.connect(self.path) as db:
            expires = db.execute("SELECT expires FROM oauth_items WHERE kind='refresh'").fetchone()[0]
            self.assertLessEqual(expires, time.time() + 86400)
            db.execute("UPDATE oauth_items SET expires=0 WHERE kind='refresh'")
        response = self.client.post("/token", data={"grant_type": "refresh_token", "client_id": "fixture-client", "refresh_token": tokens["refresh_token"]})
        self.assertEqual(response.status_code, 400)

    def test_pending_ticket_cap_and_expired_cleanup_preserve_live_credentials(self):
        for _ in range(module.MAX_PENDING_TICKETS):
            self.assertIn("/oauth/consent?", self.authorize().headers["location"])
        denied = self.authorize()
        self.assertEqual(parse_qs(urlsplit(denied.headers["location"]).query)["error"], ["temporarily_unavailable"])
        with self.adapter.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM oauth_items WHERE kind='ticket'").fetchone()[0], module.MAX_PENDING_TICKETS)
            db.execute("UPDATE oauth_items SET expires=0 WHERE hash=(SELECT hash FROM oauth_items WHERE kind='ticket' LIMIT 1)")
            self.adapter.put(db, "access", "fixture-expired", 0, {})
            self.adapter.put(db, "access", "fixture-live", time.time() + 600, {})
        self.assertIn("/oauth/consent?", self.authorize().headers["location"])
        with self.adapter.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM oauth_items WHERE kind='ticket'").fetchone()[0], module.MAX_PENDING_TICKETS)
            self.assertEqual(db.execute("SELECT count(*) FROM oauth_items WHERE expires<=?", (time.time(),)).fetchone()[0], 0)
            self.assertIsNotNone(db.execute("SELECT hash FROM oauth_items WHERE hash=?", (module.digest("fixture-live"),)).fetchone())
            self.adapter.put(db, "refresh", "fixture-expired-while-full", 0, {})
        self.authorize()  # Denied admission must still commit expiration cleanup.
        with self.adapter.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM oauth_items WHERE expires<=?", (time.time(),)).fetchone()[0], 0)

    def test_invalid_authorization_does_not_allocate_pending_ticket(self):
        for override in ({"client_id": "unknown"}, {"code_challenge": "malformed"},
                         {"resource": "https://other.test/mcp"}, {"scope": "events:write"}):
            self.authorize(**override)
        with self.adapter.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM oauth_items").fetchone()[0], 0)

    def enable_recovery(self, lifetime=90 * 86400):
        self.client.close()
        self.adapter = self.make_adapter(enable_recovery=True, refresh_lifetime_seconds=lifetime)
        self.client = TestClient(Starlette(routes=self.adapter.build_auth_routes()), base_url=ISSUER,
                                 client=("127.0.0.1", 1234), follow_redirects=False)
        self.addCleanup(self.client.close)

    def refresh(self, token, scope=None):
        data = {"grant_type": "refresh_token", "client_id": "fixture-client", "refresh_token": token}
        if scope is not None:
            data["scope"] = scope
        return self.client.post("/token", data=data)

    def test_recovery_opt_in_metadata_consent_and_scope_retention(self):
        self.assertNotIn("/oauth/consent", self.authorize(scope="events:observe recovery:execute").headers.get("location", ""))
        self.enable_recovery()
        metadata = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(metadata["scopes_supported"], ["events:observe", "recovery:execute"])
        self.assertNotIn("/oauth/consent", self.authorize(scope="recovery:execute").headers.get("location", ""))
        form = self.consent_form(scope="events:observe recovery:execute")
        screen = self.client.get("/oauth/consent", params={"ticket": form["ticket"]}, headers={"Remote-User": "fixture-user"})
        self.assertIn("events:observe recovery:execute", screen.text)
        self.assertIn("This server does not run shell commands", screen.text)
        tokens = self.token(self.grant(scope="events:observe recovery:execute")).json()
        self.assertEqual(tokens["scope"], "events:observe recovery:execute")
        with self.adapter.connect() as db:
            expiry = db.execute("SELECT expires FROM oauth_items WHERE hash=?", (module.digest(tokens["refresh_token"]),)).fetchone()[0]
        self.assertGreater(expiry, time.time() + 89 * 86400)
        newer = self.refresh(tokens["refresh_token"])
        self.assertEqual(newer.status_code, 200, newer.text)
        self.assertEqual(newer.json()["scope"], tokens["scope"])
        with self.adapter.connect() as db:
            rotated_expiry = db.execute("SELECT expires FROM oauth_items WHERE hash=?", (module.digest(newer.json()["refresh_token"]),)).fetchone()[0]
        self.assertEqual(rotated_expiry, expiry)
        import asyncio
        loaded = asyncio.run(self.adapter.load_access_token(newer.json()["access_token"]))
        self.assertIn("recovery:execute", loaded.scopes)
        self.assertIsNone(asyncio.run(self.make_adapter().load_access_token(newer.json()["access_token"])))

    def test_existing_read_grant_and_omitted_scope_never_escalate(self):
        old = self.token(self.grant()).json()
        self.enable_recovery()
        self.assertEqual(self.refresh(old["refresh_token"], "events:observe recovery:execute").status_code, 400)
        renewed = self.refresh(old["refresh_token"])
        self.assertEqual(renewed.status_code, 200, renewed.text)
        self.assertEqual(renewed.json()["scope"], "events:observe")
        default = self.token(self.grant(scope=None))
        self.assertEqual(default.status_code, 200, default.text)
        self.assertEqual(default.json()["scope"], "events:observe")

    def test_recovery_scope_can_be_narrowed_but_not_restored_by_refresh(self):
        self.enable_recovery()
        tokens = self.token(self.grant(scope="events:observe recovery:execute")).json()
        narrowed = self.refresh(tokens["refresh_token"], "events:observe")
        self.assertEqual(narrowed.status_code, 200, narrowed.text)
        self.assertEqual(narrowed.json()["scope"], "events:observe")
        self.assertEqual(self.refresh(narrowed.json()["refresh_token"], "events:observe recovery:execute").status_code, 400)

    def test_refresh_lifetime_bounds(self):
        self.assertEqual(self.make_adapter(refresh_lifetime_seconds=1).refresh_lifetime_seconds, 86400)
        self.assertEqual(self.make_adapter(refresh_lifetime_seconds=999 * 86400).refresh_lifetime_seconds, 90 * 86400)
        with self.assertRaises(ValueError):
            self.make_adapter(enable_recovery="true")
        with self.assertRaises(ValueError):
            self.make_adapter(refresh_lifetime_seconds=True)

    def test_deny_and_metadata_no_dcr(self):
        response = self.client.post("/oauth/consent", data={**self.consent_form(), "decision": "deny"},
                                    headers={"Remote-User": "fixture-user", "Origin": ISSUER})
        self.assertEqual(parse_qs(urlsplit(response.headers["location"]).query)["error"], ["access_denied"])
        metadata = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(metadata["issuer"], ISSUER)
        self.assertTrue(metadata["authorization_response_iss_parameter_supported"])
        self.assertEqual(metadata["token_endpoint_auth_methods_supported"], ["none"])
        self.assertEqual(metadata["code_challenge_methods_supported"], ["S256"])
        self.assertNotIn("registration_endpoint", metadata)
        self.assertEqual(self.client.post("/register", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
