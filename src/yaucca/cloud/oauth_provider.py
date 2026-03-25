"""Single-user OAuth 2.1 provider for yaucca remote MCP.

Implements the OAuthAuthorizationServerProvider protocol from the MCP SDK.
All state is persisted to SQLite so tokens survive Modal cold starts.
Authorization is auto-approved (single user, no consent screen).
"""

import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

# Token lifetimes
ACCESS_TOKEN_EXPIRY = 24 * 3600  # 24 hours
REFRESH_TOKEN_EXPIRY = 30 * 24 * 3600  # 30 days
AUTH_CODE_EXPIRY = 600  # 10 minutes


def _now() -> int:
    return int(time.time())


class OAuthStore:
    """SQLite-backed storage for OAuth state.

    Uses the same sqlite3 connection as the main Database, but manages
    its own tables. Caller must call init_schema() after connecting.
    """

    def __init__(self, conn_getter: callable) -> None:
        self._get_conn = conn_getter

    @property
    def conn(self):
        return self._get_conn()

    def init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY,
                client_data TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_codes (
                code TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                code_data TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_tokens (
                token TEXT PRIMARY KEY,
                token_type TEXT NOT NULL,
                client_id TEXT NOT NULL,
                scopes TEXT NOT NULL,
                expires_at INTEGER,
                resource TEXT
            );

            CREATE TABLE IF NOT EXISTS pending_auths (
                state TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                params_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
        """)

    # --- Clients ---

    def save_client(self, client: OAuthClientInformationFull) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO oauth_clients (client_id, client_data, created_at) VALUES (?, ?, ?)",
            (client.client_id, client.model_dump_json(), _now()),
        )
        self.conn.commit()

    def load_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = self.conn.execute(
            "SELECT client_data FROM oauth_clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        if not row:
            return None
        return OAuthClientInformationFull.model_validate_json(row[0])

    # --- Authorization codes ---

    def save_code(self, code: AuthorizationCode) -> None:
        self.conn.execute(
            "INSERT INTO oauth_codes (code, client_id, code_data, expires_at) VALUES (?, ?, ?, ?)",
            (code.code, code.client_id, code.model_dump_json(), int(code.expires_at)),
        )
        self.conn.commit()

    def load_code(self, code: str, client_id: str) -> AuthorizationCode | None:
        row = self.conn.execute(
            "SELECT code_data, expires_at FROM oauth_codes WHERE code = ? AND client_id = ?",
            (code, client_id),
        ).fetchone()
        if not row:
            return None
        if row[1] < _now():
            self.conn.execute("DELETE FROM oauth_codes WHERE code = ?", (code,))
            self.conn.commit()
            return None
        return AuthorizationCode.model_validate_json(row[0])

    def delete_code(self, code: str) -> None:
        self.conn.execute("DELETE FROM oauth_codes WHERE code = ?", (code,))
        self.conn.commit()

    # --- Tokens ---

    def save_token(
        self,
        token: str,
        token_type: str,
        client_id: str,
        scopes: list[str],
        expires_at: int | None = None,
        resource: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO oauth_tokens (token, token_type, client_id, scopes, expires_at, resource) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token, token_type, client_id, " ".join(scopes), expires_at, resource),
        )
        self.conn.commit()

    def load_access_token(self, token: str) -> AccessToken | None:
        row = self.conn.execute(
            "SELECT token, client_id, scopes, expires_at, resource FROM oauth_tokens "
            "WHERE token = ? AND token_type = 'access'",
            (token,),
        ).fetchone()
        if not row:
            return None
        if row[3] and row[3] < _now():
            self.conn.execute("DELETE FROM oauth_tokens WHERE token = ?", (token,))
            self.conn.commit()
            return None
        return AccessToken(
            token=row[0],
            client_id=row[1],
            scopes=row[2].split() if row[2] else [],
            expires_at=row[3],
            resource=row[4],
        )

    def load_refresh_token(self, token: str, client_id: str) -> RefreshToken | None:
        row = self.conn.execute(
            "SELECT token, client_id, scopes, expires_at FROM oauth_tokens "
            "WHERE token = ? AND token_type = 'refresh' AND client_id = ?",
            (token, client_id),
        ).fetchone()
        if not row:
            return None
        if row[3] and row[3] < _now():
            self.conn.execute("DELETE FROM oauth_tokens WHERE token = ?", (token,))
            self.conn.commit()
            return None
        return RefreshToken(
            token=row[0],
            client_id=row[1],
            scopes=row[2].split() if row[2] else [],
            expires_at=row[3],
        )

    def delete_tokens_for_client(self, client_id: str, token: str | None = None) -> None:
        if token:
            self.conn.execute(
                "DELETE FROM oauth_tokens WHERE client_id = ? AND token = ?",
                (client_id, token),
            )
        else:
            self.conn.execute("DELETE FROM oauth_tokens WHERE client_id = ?", (client_id,))
        self.conn.commit()

    # --- Pending authorizations (for GitHub OAuth delegation) ---

    def save_pending_auth(self, state: str, client_id: str, params_json: str) -> None:
        self.conn.execute(
            "INSERT INTO pending_auths (state, client_id, params_json, created_at) VALUES (?, ?, ?, ?)",
            (state, client_id, params_json, _now()),
        )
        self.conn.commit()

    def load_pending_auth(self, state: str) -> tuple[str, str] | None:
        """Load and delete a pending auth. Returns (client_id, params_json) or None."""
        row = self.conn.execute(
            "SELECT client_id, params_json, created_at FROM pending_auths WHERE state = ?",
            (state,),
        ).fetchone()
        if not row:
            return None
        # Expire after 10 minutes
        if row[2] + 600 < _now():
            self.conn.execute("DELETE FROM pending_auths WHERE state = ?", (state,))
            self.conn.commit()
            return None
        self.conn.execute("DELETE FROM pending_auths WHERE state = ?", (state,))
        self.conn.commit()
        return (row[0], row[1])

    def cleanup_expired(self) -> None:
        now = _now()
        self.conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (now,))
        self.conn.execute("DELETE FROM oauth_tokens WHERE expires_at IS NOT NULL AND expires_at < ?", (now,))
        self.conn.execute("DELETE FROM pending_auths WHERE created_at < ?", (now - 600,))
        self.conn.commit()


class YauccaOAuthProvider(OAuthAuthorizationServerProvider):
    """Single-user OAuth 2.1 provider backed by SQLite.

    Delegates authentication to GitHub OAuth. Only allowed GitHub users
    (configured via GITHUB_ALLOWED_USERS) can authorize.
    """

    def __init__(self, store: OAuthStore, github_client_id: str, github_callback_url: str) -> None:
        self._store = store
        self._github_client_id = github_client_id
        self._github_callback_url = github_callback_url

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._store.load_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._store.save_client(client_info)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Redirect to GitHub for authentication instead of auto-approving."""
        # Store the original MCP OAuth params so the GitHub callback can complete the flow.
        pending_state = secrets.token_urlsafe(32)
        self._store.save_pending_auth(
            state=pending_state,
            client_id=client.client_id,
            params_json=params.model_dump_json(),
        )

        # Redirect to GitHub OAuth
        from urllib.parse import urlencode
        github_params = urlencode({
            "client_id": self._github_client_id,
            "redirect_uri": self._github_callback_url,
            "scope": "read:user",
            "state": pending_state,
        })
        return f"https://github.com/login/oauth/authorize?{github_params}"

    def complete_authorization(self, pending_state: str, github_username: str) -> str | None:
        """Complete the OAuth flow after GitHub authentication.

        Called by the GitHub callback endpoint. Returns the redirect URL
        for Claude.ai, or None if the pending auth is invalid/expired.
        """
        result = self._store.load_pending_auth(pending_state)
        if not result:
            return None

        client_id, params_json = result
        params = AuthorizationParams.model_validate_json(params_json)

        code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=_now() + AUTH_CODE_EXPIRY,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        self._store.save_code(auth_code)

        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self._store.load_code(authorization_code, client.client_id)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._store.delete_code(authorization_code.code)

        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        scopes = authorization_code.scopes

        self._store.save_token(
            access, "access", client.client_id, scopes,
            expires_at=_now() + ACCESS_TOKEN_EXPIRY,
            resource=authorization_code.resource,
        )
        self._store.save_token(
            refresh, "refresh", client.client_id, scopes,
            expires_at=_now() + REFRESH_TOKEN_EXPIRY,
        )

        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_EXPIRY,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return self._store.load_refresh_token(refresh_token, client.client_id)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Revoke old tokens
        self._store.delete_tokens_for_client(client.client_id, refresh_token.token)

        # Issue new pair
        access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        effective_scopes = scopes or refresh_token.scopes

        self._store.save_token(
            access, "access", client.client_id, effective_scopes,
            expires_at=_now() + ACCESS_TOKEN_EXPIRY,
        )
        self._store.save_token(
            new_refresh, "refresh", client.client_id, effective_scopes,
            expires_at=_now() + REFRESH_TOKEN_EXPIRY,
        )

        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_EXPIRY,
            scope=" ".join(effective_scopes) if effective_scopes else None,
            refresh_token=new_refresh,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        # Allow direct bearer token auth (for Claude Code cloud sandboxes
        # where OAuth flows can't complete). The token must match
        # YAUCCA_AUTH_TOKEN from the environment.
        import os

        static_token = os.environ.get("YAUCCA_AUTH_TOKEN")
        if static_token and token == static_token:
            return AccessToken(
                token=token,
                client_id="bearer-auth",
                scopes=["memory:read", "memory:write"],
            )
        return self._store.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._store.delete_tokens_for_client(token.client_id, token.token)
