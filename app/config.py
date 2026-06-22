import socket

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    app_version: str = Field(default="v1", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    failure_rate: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    instance_id: str = Field(default_factory=socket.gethostname, min_length=1, max_length=253)
