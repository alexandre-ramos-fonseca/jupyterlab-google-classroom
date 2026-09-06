"""Handlers Jupyter Server da integração Classroom para professores."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import tornado.web
from jupyter_server.base.handlers import JupyterHandler

from .storage import MAX_BODY_BYTES, association_key, get_association, sanitize_course_section, save_notebook, update_association, validate_notebook

__version__ = "0.1.5"
CLIENT_ENV = "GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID"
TERM_ENV = "GOOGLE_CLASSROOM_TEACHER_TERM"
SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def config_response() -> dict[str, Any]:
    client_id = os.environ.get(CLIENT_ENV) or None
    term = os.environ.get(TERM_ENV) or None
    return {"configured": bool(client_id and term), "client_id": client_id, "term": term, "scopes": SCOPES}


def _root(handler: Any) -> Path:
    value = getattr(handler, "root_dir", None)
    if not value or not Path(value).is_absolute():
        raise RuntimeError("root_dir ausente ou inválido")
    return Path(value)


def _body(handler: Any) -> dict[str, Any]:
    if len(handler.request.body) > MAX_BODY_BYTES:
        raise ValueError("corpo demasiado grande")
    data = json.loads(handler.request.body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("corpo inválido")
    return data


def _record_with_server_term(body: dict[str, Any]) -> dict[str, Any]:
    """Return the save record with the configured term as its authority."""
    term = os.environ.get(TERM_ENV)
    if not term:
        raise ValueError("período do professor não configurado")
    record = dict(body)
    record["term"] = term
    return record


class BaseHandler(JupyterHandler):
    def initialize(self, root_dir: str) -> None:
        if not root_dir or not Path(root_dir).is_absolute():
            raise RuntimeError("root_dir ausente ou inválido")
        self.root_dir = root_dir


class HealthHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        self.finish({"status": "ok", "version": __version__})


class ConfigHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        self.finish(config_response())


class SaveHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        try:
            body = _body(self)
            record = _record_with_server_term(body)
            result = save_notebook(_root(self), record, record["notebook"])
            self.finish(result)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc


class AssociationHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        try:
            key = association_key(self.get_argument("course_id"), self.get_argument("coursework_id"), self.get_argument("source_file_id"))
            self.finish(get_association(_root(self), key))
        except (KeyError, ValueError) as exc:
            raise tornado.web.HTTPError(404, reason=str(exc)) from exc

    @tornado.web.authenticated
    def post(self):
        try:
            body = _body(self)
            key = association_key(body["course_id"], body["coursework_id"], body["source_file_id"])
            update = dict(body)
            current = get_association(_root(self), key)
            if "course_section" in body:
                section = sanitize_course_section(body.get("course_section"), current["course_id"])
                update["course_section"] = section
            self.finish(update_association(_root(self), key, update, term=os.environ.get(TERM_ENV)))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise tornado.web.HTTPError(400, reason=str(exc)) from exc


def load_jupyter_server_extension(server_app: Any) -> None:
    root_dir = getattr(server_app, "root_dir", None)
    if not root_dir or not Path(root_dir).is_absolute():
        raise RuntimeError("root_dir ausente ou inválido")
    base = server_app.web_app.settings.get("base_url", "/").rstrip("/")
    routes = [
        (f"{base}/google-classroom-teacher/api/health", HealthHandler),
        (f"{base}/google-classroom-teacher/api/config", ConfigHandler),
        (f"{base}/google-classroom-teacher/api/save", SaveHandler),
        (f"{base}/google-classroom-teacher/api/association", AssociationHandler),
    ]
    server_app.web_app.add_handlers(".*$", [(pattern, handler, {"root_dir": str(root_dir)}) for pattern, handler in routes])
