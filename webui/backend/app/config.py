"""Web UI configuration."""

from __future__ import annotations

import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    secret_key: str = secrets.token_urlsafe(48)
    # Seeds the `admin` user on the very first start (see app/users.py); once
    # output/users.json exists it is ignored, never resynced.
    admin_password: str = "admin"

    # Absolute path to the project ON THE HOST (so sibling containers get correct
    # bind-mount paths through the Docker socket). Leave it empty: it is detected
    # from this container's own /project bind mount over the Docker socket, which
    # is always correct. Set it only to override that detection.
    host_project_dir: str = ""
    # Path to the project inside THIS container (mounted). Used to read output/
    # and as the `docker build` context path — build contexts are resolved by the
    # docker CLI, which runs in here, not by the daemon.
    project_dir: str = "/project"

    # --- OIDC single sign-on (see app/oidc.py) ---------------------------------
    # Entirely off unless BOTH oidc_issuer and oidc_client_id are set; password
    # login is never affected either way. The secret is optional on purpose: a
    # public client with PKCE is a valid OIDC configuration.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    # Which ID-token claim carries the user's groups, and how those groups map
    # to local roles ("idp-group=role", comma-separated). A user in several
    # mapped groups gets the highest of their roles.
    oidc_role_claim: str = "groups"
    oidc_role_map: str = ""
    # What happens to an authenticated user whose groups map to nothing:
    # "deny" refuses them (and audits the refusal); viewer/operator/admin
    # admits everyone the IdP vouches for, at that role.
    oidc_default_role: str = "deny"
    oidc_display_name: str = "Single sign-on"
    oidc_scopes: str = "openid profile email"

    @property
    def output_dir(self) -> str:
        return f"{self.project_dir}/output"


settings = Settings()
