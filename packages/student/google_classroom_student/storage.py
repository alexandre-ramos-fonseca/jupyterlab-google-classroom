"""Persistência local dos notebooks abertos do Google Classroom (MVP 0.3.9).

Mantém uma cópia local sob ``<root_dir>/classroom/`` e a associação mínima
``file_id -> caminho local/baseline remoto`` em
``<root_dir>/.google-classroom-student/files.json``. Nunca armazena token, e-mail ou o
conteúdo do notebook nos metadados.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

CLASSROOM_DIRNAME = "classroom"
META_DIRNAME = ".google-classroom-student"
FILES_JSON = "files.json"

# Limite razoável do corpo do POST /api/save.
MAX_BODY_BYTES = 20 * 1024 * 1024


def validate_notebook(notebook: Any) -> None:
    """Valida que o payload é um notebook JSON com ``nbformat`` e ``cells``."""
    if not isinstance(notebook, dict):
        raise ValueError("Notebook deve ser um objeto JSON.")
    if "nbformat" not in notebook or not isinstance(notebook["nbformat"], int):
        raise ValueError("Notebook deve conter um campo 'nbformat' inteiro.")
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ValueError("Notebook deve conter 'cells' como lista.")


def sanitize_name(name: str) -> str:
    """Reduz a um basename seguro terminado em ``.ipynb``."""
    if not isinstance(name, str) or not name:
        raise ValueError("Nome do arquivo ausente.")
    name = Path(name).name  # remove qualquer diretório/path fornecido
    if not name or not name.endswith(".ipynb"):
        raise ValueError("Apenas arquivos .ipynb são aceitos.")
    if name in (".", ".."):
        raise ValueError("Nome inválido.")
    return name


def _next_available(base_name: str, used: set[str]) -> str:
    """Devolve um nome que não colide (``Nome (2).ipynb``, ``Nome (3).ipynb``…)."""
    stem, ext = os.path.splitext(base_name)
    candidate = base_name
    i = 2
    while candidate in used:
        candidate = f"{stem} ({i}){ext}"
        i += 1
    return candidate


def _atomic_write_bytes(path: Path, data: bytes, mode: int | None = None) -> None:
    """Escrita atômica (arquivo temporário + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_metadata(meta_file: Path) -> dict[str, Any]:
    if not meta_file.exists():
        return {}
    try:
        data = json.loads(meta_file.read_text("utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_notebook(
    root_dir: str | Path,
    file_id: str,
    file_name: str,
    notebook: dict[str, Any],
    course_id: str | None = None,
    coursework_id: str | None = None,
    submission_id: str | None = None,
    submission_state: str | None = None,
    modified_time: str | None = None,
    version: str | None = None,
    md5_checksum: str | None = None,
    can_edit: bool | None = None,
) -> tuple[str, bool]:
    """Salva ``notebook`` localmente e devolve ``(caminho_relativo, criado)``."""
    validate_notebook(notebook)
    base = sanitize_name(file_name)
    if not file_id:
        raise ValueError("file_id ausente.")

    root = Path(root_dir)
    classroom = root / CLASSROOM_DIRNAME
    meta_dir = root / META_DIRNAME
    meta_file = meta_dir / FILES_JSON

    classroom.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(meta_dir, 0o700)
    except OSError:
        pass

    metadata = _load_metadata(meta_file)

    # Nomes ocupados por outros file_ids e arquivos presentes no diretório.
    used: set[str] = {
        rec.get("path", "").rsplit("/", 1)[-1]
        for fid, rec in metadata.items()
        if fid != file_id and isinstance(rec, dict)
    } | {p.name for p in classroom.iterdir() if p.is_file()}

    record = {
        "name": base,
        "course_id": course_id,
        "coursework_id": coursework_id,
        "submission_id": submission_id,
        "submission_state": submission_state,
        "modified_time": modified_time,
        "version": version,
        "md5_checksum": md5_checksum,
        "can_edit": can_edit,
    }

    existing = metadata.get(file_id)
    if existing is not None and isinstance(existing, dict):
        rel = existing.get("path", "")
        abs_path = root / rel
        if rel and abs_path.exists():
            # Reutiliza o arquivo local sem sobrescrever, atualizando o
            # baseline remoto da importação mais recente.
            metadata[file_id] = {"path": rel, **record}
            _write_metadata(meta_file, metadata)
            return rel, False
        # Arquivo mapeado não existe mais no disco: cria um novo nome.
    target = _next_available(base, used)
    rel = f"{CLASSROOM_DIRNAME}/{target}"
    data = json.dumps(notebook, ensure_ascii=False).encode("utf-8")
    _atomic_write_bytes(classroom / target, data)

    metadata[file_id] = {"path": rel, **record}
    _write_metadata(meta_file, metadata)
    return rel, True


def get_association(root_dir: str | Path, file_id: str) -> dict[str, Any] | None:
    """Retorna a associação de um arquivo previamente importado."""
    if not isinstance(file_id, str) or not file_id:
        return None
    metadata = _load_metadata(Path(root_dir) / META_DIRNAME / FILES_JSON)
    record = metadata.get(file_id)
    if not isinstance(record, dict):
        return None
    rel = record.get("path")
    if not isinstance(rel, str) or not rel.startswith(f"{CLASSROOM_DIRNAME}/"):
        return None
    if not (Path(root_dir) / rel).is_file():
        return None
    return {"file_id": file_id, **record}


def update_association(
    root_dir: str | Path,
    file_id: str,
    path: str,
    modified_time: str | None,
    version: str | None,
    md5_checksum: str | None,
    can_edit: bool | None,
) -> dict[str, Any]:
    """Atualiza somente o baseline de uma associação já importada."""
    root = Path(root_dir)
    if not isinstance(file_id, str) or not file_id:
        raise ValueError("file_id ausente.")
    if not isinstance(path, str) or not path.startswith(f"{CLASSROOM_DIRNAME}/"):
        raise ValueError("Caminho local inválido.")
    metadata_file = root / META_DIRNAME / FILES_JSON
    metadata = _load_metadata(metadata_file)
    record = metadata.get(file_id)
    if not isinstance(record, dict) or record.get("path") != path:
        raise ValueError("Notebook não está associado a uma importação válida.")
    if not (root / path).is_file():
        raise ValueError("A cópia local deste notebook não foi encontrada.")
    record.update(
        modified_time=modified_time,
        version=version,
        md5_checksum=md5_checksum,
        can_edit=can_edit,
    )
    metadata[file_id] = record
    _write_metadata(metadata_file, metadata)
    return {"file_id": file_id, **record}


def _write_metadata(meta_file, metadata) -> None:
    data = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    _atomic_write_bytes(meta_file, data, mode=0o600)
    try:
        os.chmod(meta_file, 0o600)
    except OSError:
        pass


__all__ = [
    "validate_notebook",
    "sanitize_name",
    "save_notebook",
    "get_association",
    "update_association",
    "MAX_BODY_BYTES",
    "CLASSROOM_DIRNAME",
    "META_DIRNAME",
    "FILES_JSON",
]
