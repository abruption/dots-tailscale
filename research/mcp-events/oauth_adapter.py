"""Research OAuth adapter; Caddy must authenticate ONLY /oauth/consent.

Bind the enclosing server to loopback with proxy_headers=False. Caddy must
remove client-supplied Remote-* headers before copying Authelia's Remote-User.
Local processes are inside this trust boundary. There is no development bypass.
"""

from contextlib import contextmanager
import hashlib
import html
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    OAuthAuthorizationServerProvider, RefreshToken, TokenError,
)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

SCOPE = "events:observe"
RECOVERY_SCOPE = "recovery:execute"
MAX_PENDING_TICKETS = 64
COOKIE = "__Host-dots-mcp-consent"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def append_query(uri, **fields):
    p = urlsplit(uri)
    query = parse_qsl(p.query, keep_blank_values=True)
    query += [(k, v) for k, v in fields.items() if v is not None]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), p.fragment))


class RegisteredClient(OAuthClientInformationFull):
    def validate_scope(self, scope):
        # The registered capabilities include optional recovery, but omitted
        # scope must never silently request recovery permission.
        return super().validate_scope(SCOPE if scope is None else scope)


class OAuthAdapter(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, db_path, issuer, resource, allowed_user, client_id, redirect_uri,
                 enable_recovery=False, refresh_lifetime_seconds=86400):
        if type(enable_recovery) is not bool or type(refresh_lifetime_seconds) is not int:
            raise ValueError("recovery flag must be boolean and lifetime an integer")
        self.allowed_scopes = [SCOPE] + ([RECOVERY_SCOPE] if enable_recovery else [])
        # This is an absolute grant lifetime, preserved on every rotation.
        # At most 90 days after login, the user must authenticate again.
        self.refresh_lifetime_seconds = max(86400, min(refresh_lifetime_seconds, 90 * 86400))
        self.issuer = issuer.rstrip("/")
        p = urlsplit(self.issuer)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.path or p.query or p.fragment:
            raise ValueError("issuer must be an HTTPS origin")
        if resource != self.issuer + "/mcp":
            raise ValueError("resource must be the exact issuer /mcp URL")
        r = urlsplit(redirect_uri)
        if r.scheme != "https" or r.hostname != "chatgpt.com" or r.username or r.password or r.fragment:
            raise ValueError("configure the exact HTTPS ChatGPT redirect URI")
        if not allowed_user or not client_id:
            raise ValueError("explicit user and client ID required")
        self.resource, self.allowed_user = resource, allowed_user
        self.client_id, self.redirect_uri, self.host = client_id, redirect_uri, p.netloc
        self.client = RegisteredClient(
            client_id=client_id, redirect_uris=[redirect_uri], scope=" ".join(self.allowed_scopes),
            token_endpoint_auth_method="none", grant_types=["authorization_code", "refresh_token"],
            response_types=["code"], client_name="Dots recovery research",
        )
        if str(self.client.redirect_uris[0]) != redirect_uri:
            raise ValueError("redirect URI must use its exact canonical spelling")
        self.db_path = Path(db_path)
        parent = self.db_path.parent.stat()
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
            raise ValueError("OAuth state directory must be private and user-owned")
        fd = os.open(self.db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("OAuth state file must be private and user-owned")
        finally:
            os.close(fd)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS oauth_items (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, expires REAL NOT NULL, family TEXT NOT NULL, data TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, db, kind, secret, expires, data, family=""):
        db.execute("INSERT INTO oauth_items VALUES (?,?,?,?,?)",
                   (digest(secret), kind, expires, family, json.dumps(data)))

    def load(self, kind, secret):
        with self.connect() as db:
            row = db.execute("SELECT expires,family,data FROM oauth_items WHERE hash=? AND kind=?", (digest(secret), kind)).fetchone()
        if row is None or row[0] <= time.time():
            return None
        data = json.loads(row[2])
        data.update(expires_at=row[0], family=row[1])
        return data

    async def get_client(self, client_id):
        return self.client.model_copy(deep=True) if client_id == self.client_id else None

    async def register_client(self, client_info):
        raise NotImplementedError("Dynamic registration disabled")

    def valid_scopes(self, scopes):
        return bool(scopes) and SCOPE in scopes and len(scopes) == len(set(scopes)) and set(scopes).issubset(self.allowed_scopes)

    async def authorize(self, client, params: AuthorizationParams):
        if client.client_id != self.client_id or str(params.redirect_uri) != self.redirect_uri or not params.redirect_uri_provided_explicitly:
            raise AuthorizeError(error="invalid_request", error_description="Explicit registered redirect required")
        if params.resource != self.resource:
            raise AuthorizeError(error="invalid_request", error_description="Exact resource required")
        if not self.valid_scopes(params.scopes):
            raise AuthorizeError(error="invalid_scope", error_description="Unsupported scope")
        if len(params.code_challenge) != 43 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in params.code_challenge):
            raise AuthorizeError(error="invalid_request", error_description="Invalid S256 challenge")
        ticket = secrets.token_urlsafe(32)
        data = params.model_dump(mode="json")
        data["client_id"] = self.client_id
        # Serialize admission so concurrent requests cannot exceed the cap.
        # Expired access/refresh records can be purged; live credentials remain.
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM oauth_items WHERE expires<=?", (now,))
            full = db.execute("SELECT count(*) FROM oauth_items WHERE kind='ticket'").fetchone()[0] >= MAX_PENDING_TICKETS
            if not full:
                self.put(db, "ticket", ticket, now + 300, data)
        # Raise after committing cleanup, even when admission was denied.
        if full:
            raise AuthorizeError(error="temporarily_unavailable", error_description="Pending authorization limit reached; retry later")
        return self.issuer + "/oauth/consent?" + urlencode({"ticket": ticket})

    def trusted_identity(self, request):
        users = request.headers.getlist("remote-user")
        return (
            request.client is not None and request.client.host in ("127.0.0.1", "::1")
            and request.headers.get("host") == self.host
            and len(users) == 1 and secrets.compare_digest(users[0].encode(), self.allowed_user.encode())
        )

    async def consent(self, request):
        if not self.trusted_identity(request):
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=NO_STORE)
        if request.method == "GET":
            ticket = request.query_params.get("ticket", "")
            pending = self.load("ticket", ticket) if len(ticket) <= 128 else None
            if pending is None or not self.valid_scopes(pending["scopes"]):
                return JSONResponse({"error": "invalid_request"}, status_code=400, headers=NO_STORE)
            csrf = secrets.token_urlsafe(32)
            recovery_notice = (
                '<p>Recovery permission lets ChatGPT reserve a recovery attempt for the fixed start.sh '
                'command and report its result. This server does not run shell commands.</p>'
                if RECOVERY_SCOPE in pending["scopes"] else ''
            )
            requested = html.escape(" ".join(pending["scopes"]))
            body = ('<!doctype html><title>Dots recovery research</title><h1>Allow these permissions?</h1>'
                    '<p>This permits the connected ChatGPT account to observe recovery research events.</p>'
                    f'<p>Requested permissions: <code>{requested}</code></p>'
                    + recovery_notice +
                    '<form method="post" action="/oauth/consent">'
                    f'<input type="hidden" name="ticket" value="{html.escape(ticket, quote=True)}">'
                    f'<input type="hidden" name="csrf" value="{csrf}">'
                    '<button name="decision" value="allow">Allow</button>'
                    '<button name="decision" value="deny">Deny</button></form>')
            # Chromium applies form-action to the POST's redirect chain too.
            # Permit only our registered OAuth callback, not arbitrary origins.
            response = HTMLResponse(body, headers={**NO_STORE, "Content-Security-Policy":
                f"default-src 'none'; form-action 'self' {self.redirect_uri}; frame-ancestors 'none'; base-uri 'none'"})
            response.set_cookie(COOKIE, csrf, max_age=300, secure=True, httponly=True, samesite="strict", path="/")
            return response
        if request.headers.get("origin") != self.issuer:
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=NO_STORE)
        # Bound input before form parsing; no files or multipart are accepted.
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            return JSONResponse({"error": "invalid_request"}, status_code=400, headers=NO_STORE)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                return JSONResponse({"error": "invalid_request"}, status_code=413, headers=NO_STORE)
        try:
            pairs = parse_qsl(body.decode("utf-8"), max_num_fields=4, strict_parsing=True)
        except (ValueError, UnicodeError):
            pairs = []
        form = dict(pairs)
        if len(form) != len(pairs) or set(form) != {"ticket", "csrf", "decision"}:
            return JSONResponse({"error": "invalid_request"}, status_code=400, headers=NO_STORE)
        cookie = request.cookies.get(COOKIE, "")
        if not cookie or not secrets.compare_digest(cookie.encode(), form["csrf"].encode()):
            return JSONResponse({"error": "access_denied"}, status_code=403, headers=NO_STORE)
        if form["decision"] not in ("allow", "deny"):
            return JSONResponse({"error": "invalid_request"}, status_code=400, headers=NO_STORE)
        ticket = form["ticket"]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM oauth_items WHERE hash=? AND kind='ticket' AND expires>?", (digest(ticket), time.time())).fetchone()
            if row is None:
                return JSONResponse({"error": "invalid_request"}, status_code=400, headers=NO_STORE)
            data = json.loads(row[0])
            if not self.valid_scopes(data["scopes"]):
                return JSONResponse({"error": "invalid_scope"}, status_code=400, headers=NO_STORE)
            db.execute("DELETE FROM oauth_items WHERE hash=?", (digest(ticket),))
            fields = {"state": data["state"], "iss": self.issuer}
            if form["decision"] == "allow":
                code = secrets.token_urlsafe(32)
                data["subject"] = self.allowed_user
                self.put(db, "code", code, time.time() + 60, data)
                fields["code"] = code
            else:
                fields["error"] = "access_denied"
        response = RedirectResponse(append_query(self.redirect_uri, **fields), status_code=303, headers=NO_STORE)
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response

    async def load_authorization_code(self, client, authorization_code):
        data = self.load("code", authorization_code)
        if not data or data["client_id"] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, **{k: v for k, v in data.items() if k not in ("family", "state")})

    def issue(self, db, subject, scopes, family, refresh_expires):
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        data = {"client_id": self.client_id, "subject": subject, "scopes": scopes, "resource": self.resource}
        self.put(db, "access", access, now + 600, data, family)
        self.put(db, "refresh", refresh, refresh_expires, data, family)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=600, refresh_token=refresh, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM oauth_items WHERE hash=? AND kind='code' AND expires>?", (digest(authorization_code.code), time.time())).fetchone()
            if row is None:
                raise TokenError(error="invalid_grant", error_description="Code unavailable")
            data = json.loads(row[0])
            if data["client_id"] != client.client_id or data["resource"] != self.resource or data["subject"] != self.allowed_user:
                raise TokenError(error="invalid_grant", error_description="Code unavailable")
            if not self.valid_scopes(data["scopes"]):
                raise TokenError(error="invalid_scope", error_description="Unsupported scope")
            db.execute("DELETE FROM oauth_items WHERE hash=?", (digest(authorization_code.code),))
            return self.issue(db, data["subject"], data["scopes"], secrets.token_hex(16), int(time.time()) + self.refresh_lifetime_seconds)

    async def load_refresh_token(self, client, refresh_token):
        data = self.load("refresh", refresh_token)
        if not data or data["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, **{k: v for k, v in data.items() if k != "family"})

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data,family,expires FROM oauth_items WHERE hash=? AND kind='refresh' AND expires>?", (digest(refresh_token.token), time.time())).fetchone()
            if row is None:
                raise TokenError(error="invalid_grant", error_description="Refresh token unavailable")
            data = json.loads(row[0])
            if data["client_id"] != client.client_id or data["resource"] != self.resource or data["subject"] != self.allowed_user:
                raise TokenError(error="invalid_grant", error_description="Refresh token unavailable")
            if not self.valid_scopes(scopes) or not set(scopes).issubset(data["scopes"]):
                raise TokenError(error="invalid_scope", error_description="Unsupported scope")
            db.execute("DELETE FROM oauth_items WHERE family=?", (row[1],))
            return self.issue(db, data["subject"], scopes, row[1], row[2])

    async def load_access_token(self, token):
        data = self.load("access", token)
        if not data or data["resource"] != self.resource or data["subject"] != self.allowed_user or not self.valid_scopes(data["scopes"]):
            return None
        return AccessToken(token=token, claims={"iss": self.issuer}, **{k: v for k, v in data.items() if k != "family"})

    async def revoke_token(self, token):
        with self.connect() as db:
            row = db.execute("SELECT family FROM oauth_items WHERE hash=?", (digest(token.token),)).fetchone()
            if row:
                db.execute("DELETE FROM oauth_items WHERE family=?", (row[0],))

    def build_auth_routes(self):
        routes = create_auth_routes(
            self, AnyHttpUrl(self.issuer),
            client_registration_options=ClientRegistrationOptions(enabled=False, valid_scopes=self.allowed_scopes, default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True),
        )
        # Wrap the SDK ASGI apps, retaining body limits, PKCE validation and CORS.
        for route in routes:
            original, path = route.app, route.path
            async def wrapped(scope, receive, send, original=original, path=path):
                if path in ("/token", "/revoke") and scope["method"] == "POST":
                    chunks = bytearray()
                    while True:
                        message = await receive()
                        if message["type"] != "http.request":
                            return
                        chunks.extend(message.get("body", b""))
                        if len(chunks) > 16384:
                            await JSONResponse({"error": "invalid_request"}, status_code=413, headers=NO_STORE)(scope, receive, send)
                            return
                        if not message.get("more_body"):
                            break
                    try:
                        pairs = parse_qsl(chunks.decode("utf-8"), max_num_fields=20)
                        params = dict(pairs)
                    except (ValueError, UnicodeError):
                        params, pairs = {}, [("invalid", "")]
                    if len(params) != len(pairs) or ("resource" in params and params["resource"] != self.resource):
                        await JSONResponse({"error": "invalid_target"}, status_code=400, headers=NO_STORE)(scope, receive, send)
                        return
                    # SDK 2.3.0 RevocationRequest marks nullable client_secret as required.
                    # This public PKCE client has no secret; supply an empty form value.
                    if path == "/revoke" and "client_secret" not in params:
                        chunks.extend(b"&client_secret=")
                    async def replay_receive():
                        return {"type": "http.request", "body": bytes(chunks), "more_body": False}
                    receive = replay_receive
                captured = []
                async def capture(message):
                    captured.append(message)
                if path == "/.well-known/oauth-authorization-server" and scope["method"] == "GET":
                    await original(scope, receive, capture)
                    metadata = json.loads(b"".join(m.get("body", b"") for m in captured))
                    metadata.update(issuer=self.issuer, authorization_response_iss_parameter_supported=True,
                                    token_endpoint_auth_methods_supported=["none"], revocation_endpoint_auth_methods_supported=["none"])
                    await JSONResponse(metadata, headers=NO_STORE)(scope, receive, send)
                elif path == "/authorize":
                    async def with_issuer(message):
                        if message["type"] == "http.response.start":
                            headers = []
                            for key, value in message["headers"]:
                                if key.lower() == b"location":
                                    loc = value.decode("latin-1")
                                    # SDK errors targeting our registered callback also need RFC9207 iss.
                                    if loc.split("?", 1)[0] == self.redirect_uri.split("?", 1)[0]:
                                        value = append_query(loc, iss=self.issuer).encode("latin-1")
                                headers.append((key, value))
                            message = {**message, "headers": headers}
                        await send(message)
                    await original(scope, receive, with_issuer)
                else:
                    await original(scope, receive, send)
            route.app = wrapped
        return routes + [Route("/oauth/consent", self.consent, methods=["GET", "POST"])]
