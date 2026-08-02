"""Web UI configuration."""

from __future__ import annotations

import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    secret_key: str = secrets.token_urlsafe(48)
    admin_password: str = "admin"
    token_expire_hours: int = 12

    # Absolute path to the project ON THE HOST (so sibling containers get correct
    # bind-mount paths through the Docker socket). Leave it empty: it is detected
    # from this container's own /project bind mount over the Docker socket, which
    # is always correct. Set it only to override that detection.
    host_project_dir: str = ""
    # Path to the project inside THIS container (mounted). Used to read output/
    # and as the `docker build` context path — build contexts are resolved by the
    # docker CLI, which runs in here, not by the daemon.
    project_dir: str = "/project"

    @property
    def output_dir(self) -> str:
        return f"{self.project_dir}/output"


settings = Settings()
