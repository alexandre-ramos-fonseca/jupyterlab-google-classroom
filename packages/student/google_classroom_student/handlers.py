"""Handlers do Jupyter Server para a extensão ``google-classroom-student`` (MVP 0.3.9).

Todos os endpoints exigem autenticação do Jupyter Server
(``@tornado.web.authenticated``) — "sem autenticação Google" não torna o
endpoint público. O backend nunca recebe o access token do navegador; ele
recebe apenas conteúdo e metadados do notebook para o salvamento local.
"""

from __future__ import annotations

import json
import os
from typing import Any

import tornado.web
from jupyter_server.base.handlers import JupyterHandler

from .storage import (
    MAX_BODY_BYTES,
    get_association,
    save_notebook,
    update_association,
    validate_notebook,
)

__version__ = "0.3.9"

CONFIG_ENV = "GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID"

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me",
    "https://www.googleapis.com/auth/drive",
]

HEALTH_PATH = "/google-classroom-student/api/health"
CONFIG_PATH = "/google-classroom-student/api/config"
SAVE_PATH = "/google-classroom-student/api/save"
ASSOCIATION_PATH = "/google-classroom-student/api/association"

def config_response() -> dict[str, Any]:
    """Payload do endpoint de configuração (apenas o client ID público)."""
    client_id = os.environ.get(CONFIG_ENV) or None
    return {
        "configured": client_id is not None,
        "client_id": client_id,
        "scopes": SCOPES,
    }


class HealthHandler(JupyterHandler):
    @tornado.web.authenticated
    def get(self) -> None:
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps({"status": "ok", "version": __version__}))


class ConfigHandler(JupyterHandler):
    @tornado.web.authenticated
    def get(self) -> None:
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps(config_response(), ensure_ascii=False))


class SaveHandler(JupyterHandler):
    def initialize(self, root_dir: str | None = None) -> None:
        if not isinstance(root_dir, str) or not root_dir:
            raise RuntimeError("google-classroom-student: root_dir absoluto ausente.")
        self.root_dir = os.path.abspath(root_dir)
        if not os.path.isabs(self.root_dir):
            raise RuntimeError("google-classroom-student: root_dir deve ser absoluto.")
        if not os.path.isdir(self.root_dir):
            raise RuntimeError(
                f"google-classroom-student: root_dir não existe: {self.root_dir}"
            )

    @tornado.web.authenticated
    def post(self) -> None:
        content_type = (self.request.headers.get("Content-Type") or "").lower()
        if "application/json" not in content_type:
            self._json_error(400, "Content-Type deve ser application/json.")
            return
        if len(self.request.body) > MAX_BODY_BYTES:
            self._json_error(413, "Corpo excede o limite de 20 MiB.")
            return
        try:
            body = json.loads(self.request.body)
        except json.JSONDecodeError:
            self._json_error(400, "Corpo inválido (JSON).")
            return
        if not isinstance(body, dict) or not all(
            isinstance(body.get(f), str) for f in ("file_id", "file_name", "course_id", "coursework_id")
        ):
            self._json_error(400, "Metadados incompletos.")
            return
        try:
            path, created = save_notebook(
                self.root_dir,
                file_id=body.get("file_id"),
                file_name=body.get("file_name"),
                notebook=body.get("notebook"),
                course_id=body.get("course_id"),
                coursework_id=body.get("coursework_id"),
                submission_id=body.get("submission_id"),
                submission_state=body.get("submission_state"),
                modified_time=body.get("modified_time"),
                version=body.get("version"),
                md5_checksum=body.get("md5_checksum"),
                can_edit=body.get("can_edit"),
            )
        except ValueError as exc:
            self._json_error(400, str(exc))
            return
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps({"path": path, "created": created}))

    def _json_error(self, status: int, message: str) -> None:
        self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps({"error": message}))


class AssociationHandler(JupyterHandler):
    def initialize(self, root_dir: str | None = None) -> None:
        if not isinstance(root_dir, str) or not root_dir:
            raise RuntimeError("google-classroom-student: root_dir absoluto ausente.")
        self.root_dir = os.path.abspath(root_dir)
        if not os.path.isabs(self.root_dir) or not os.path.isdir(self.root_dir):
            raise RuntimeError("google-classroom-student: root_dir inválido.")

    @tornado.web.authenticated
    def get(self) -> None:
        file_id = self.get_query_argument("file_id", default="")
        record = get_association(self.root_dir, file_id)
        if record is None:
            self.set_status(404)
            self.finish(json.dumps({"error": "Associação local não encontrada."}))
            return
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps(record, ensure_ascii=False))

    @tornado.web.authenticated
    def post(self) -> None:
        try:
            body = json.loads(self.request.body)
        except json.JSONDecodeError:
            self._json_error(400, "Corpo inválido (JSON).")
            return
        if not isinstance(body, dict):
            self._json_error(400, "Corpo inválido.")
            return
        try:
            record = update_association(
                self.root_dir,
                body.get("file_id"),
                body.get("path"),
                body.get("modified_time"),
                body.get("version"),
                body.get("md5_checksum"),
                body.get("can_edit"),
            )
        except ValueError as exc:
            self._json_error(400, str(exc))
            return
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps(record, ensure_ascii=False))

    def _json_error(self, status: int, message: str) -> None:
        self.set_status(status)
        self.set_header("Content-Type", "application/json; charset=utf-8")
        self.finish(json.dumps({"error": message}))


def load_jupyter_server_extension(server_app: Any) -> None:
    """Registra a extensão e os endpoints no Jupyter Server."""
    web_app = getattr(server_app, "web_app", None)
    if web_app is None:
        raise RuntimeError(
            "google-classroom-student: ambiente sem Jupyter Server (web_app ausente)."
        )
    settings = web_app.settings or {}
    base_url = str(settings.get("base_url", "/")).rstrip("/")
    if not base_url:
        base_url = ""

    raw_root_dir = getattr(server_app, "root_dir", None)
    if not isinstance(raw_root_dir, (str, os.PathLike)) or not os.fspath(raw_root_dir):
        raise RuntimeError("google-classroom-student: server_app.root_dir ausente ou inválido.")
    root_dir = os.path.abspath(os.fspath(raw_root_dir))
    if not os.path.isabs(root_dir) or not os.path.isdir(root_dir):
        raise RuntimeError("google-classroom-student: server_app.root_dir deve ser um diretório absoluto existente.")

    handlers = [
        (f"{base_url}{HEALTH_PATH}", HealthHandler),
        (f"{base_url}{CONFIG_PATH}", ConfigHandler),
        (f"{base_url}{SAVE_PATH}", SaveHandler, {"root_dir": root_dir}),
        (f"{base_url}{ASSOCIATION_PATH}", AssociationHandler, {"root_dir": root_dir}),
    ]
    web_app.add_handlers(".*$", handlers)

    server_app.log.info(
        "google-classroom-student: MVP 0.3.9 carregado (versão %s); endpoints %s, %s, %s e %s.",
        __version__,
        f"{base_url}{HEALTH_PATH}",
        f"{base_url}{CONFIG_PATH}",
        f"{base_url}{SAVE_PATH}",
        f"{base_url}{ASSOCIATION_PATH}",
    )


# Nome oficial usado pela descoberta do Jupyter Server.
_load_jupyter_server_extension = load_jupyter_server_extension

__all__ = [
    "HealthHandler",
    "ConfigHandler",
    "SaveHandler",
    "AssociationHandler",
    "config_response",
    "load_jupyter_server_extension",
    "_load_jupyter_server_extension",
]
