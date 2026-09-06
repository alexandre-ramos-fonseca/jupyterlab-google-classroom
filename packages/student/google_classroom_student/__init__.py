"""Pacote Python da extensão Google Classroom para o JupyterLab Google Classroom (MVP 0.3.9).

Este pacote registra a extensão do Jupyter Server e expõe os endpoints
``/google-classroom-student/api/health``, ``/google-classroom-student/api/config`` e
``/google-classroom-student/api/save``. O frontend recebe o token do Google somente em
memória e chama as APIs do Google diretamente; o backend salva apenas o
conteúdo e os metadados do notebook localmente.

Não há client_secret, refresh token ou persistência de token nesta etapa. A
sincronização é iniciada explicitamente pelo frontend e o backend só mantém a
cópia local e o baseline mínimo da associação.
"""

from __future__ import annotations

from typing import Any

from .handlers import __version__, load_jupyter_server_extension

# Re-export para garantir ``google_classroom_student.__version__``.
# (Definido em ``handlers`` para evitar import circular.)

def _jupyter_server_extension_points():
    """Ponto de extensão oficial do Jupyter Server (mecanismo do módulo)."""
    return [{"module": "google_classroom_student.handlers"}]


def _load_jupyter_server_extension(server_app: Any) -> None:
    """Função oficial (fallback do ponto de extensão) quando o ponto é a
    própria extensão do módulo-pai."""
    return load_jupyter_server_extension(server_app)


def _jupyter_labextension_paths():
    """Retorna o diretório do bundle prebuilt dentro do pacote Python."""
    return [{"src": "static", "dest": "jupyterlab-google-classroom-student"}]


__all__ = [
    "__version__",
    "_jupyter_server_extension_points",
    "_load_jupyter_server_extension",
    "_jupyter_labextension_paths",
]
