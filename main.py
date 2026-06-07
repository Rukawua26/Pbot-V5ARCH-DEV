#!/usr/bin/env python3
"""Entrypoint minimalista de Sniper AI."""

from dotenv import load_dotenv

load_dotenv()

from core.bot_app import run_entrypoint


if __name__ == "__main__":
    run_entrypoint()
