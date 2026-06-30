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
    # bind-mount paths through the Docker socket).
    host_project_dir: str = "/opt/debian-ab-images"
    # Path to the project inside THIS container (mounted), used to read output/.
    project_dir: str = "/project"

    @property
    def output_dir(self) -> str:
        return f"{self.project_dir}/output"

    @property
    def host_output_dir(self) -> str:
        return f"{self.host_project_dir}/output"


settings = Settings()
