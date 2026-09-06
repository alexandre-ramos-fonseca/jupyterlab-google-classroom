"""Extensão separada Google Classroom — Professor."""

from __future__ import annotations

from typing import Any

from .handlers import __version__, load_jupyter_server_extension


def _jupyter_server_extension_points():
    return [{"module": "google_classroom_teacher.handlers"}]


def _load_jupyter_server_extension(server_app: Any) -> None:
    load_jupyter_server_extension(server_app)


def _jupyter_labextension_paths():
    return [{"src": "static", "dest": "jupyterlab-google-classroom-teacher"}]
