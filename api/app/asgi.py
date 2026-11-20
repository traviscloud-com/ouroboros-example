"""Uvicorn entrypoint: builds the app from environment configuration."""

from .main import create_app

app = create_app()
