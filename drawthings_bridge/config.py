# Copyright (c) 2026 Miklos Lekszikov
# SPDX-License-Identifier: MIT

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DRAWTHINGS_BRIDGE_")

    host: str = "0.0.0.0"
    port: int = 8787
    cli_bin: str = "draw-things-cli"
    models_dir: str | None = None
    temp_dir: str = "/tmp/drawthings_bridge"
