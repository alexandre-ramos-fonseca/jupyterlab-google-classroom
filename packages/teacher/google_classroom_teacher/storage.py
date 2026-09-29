"""Persistência mínima e separada para cópias de trabalho de professores."""

from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

META_DIR = ".google-classroom-teacher"
META_FILE = "associations.json"
PERSISTENT_DIR = "work"
MAX_BODY_BYTES = 8 * 1024 * 1024


def _component(value: str, *, allow_dot: bool = False, allow_slash: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("componente vazio")
    if "\x00" in value or ".." in value or "\\" in value or ("/" in value and not (allow_dot or allow_slash)):
        raise ValueError("caminho inválido")
    if allow_dot or allow_slash:
        value = value.replace("/", "-")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    pattern = r"[^A-Za-z0-9._-]+" if allow_dot else r"[^A-Za-z0-9-]+"
    value = re.sub(pattern, "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    if not value:
        raise ValueError("componente inválido")
    return value


def sanitize_term(term: str) -> str:
    return _component(term, allow_dot=True)


def sanitize_course_name(name: str) -> str:
    return _component(name)


def sanitize_course_section(section: str | None, course_id: str) -> str:
    value = str(section or "").strip() or f"Curso-{course_id}"
    return _component(value, allow_slash=True)


def sanitize_notebook_name(name: str) -> str:
    if not name.lower().endswith(".ipynb"):
        raise ValueError("arquivo não é notebook")
    stem = _component(name[:-6])
    return stem + ".ipynb"


def destination_name(term: str, course_name: str, course_section: str | None, original_name: str, course_id: str) -> str:
    return (
        f"{sanitize_term(term)}_{sanitize_course_name(course_name)}_"
        f"{sanitize_course_section(course_section, course_id)}_"
        f"{sanitize_notebook_name(original_name)}"
    )


def association_key(course_id: str, coursework_id: str, source_file_id: str) -> str:
    parts = (course_id, coursework_id, source_file_id)
    if any(not isinstance(p, str) or not p or "/" in p or "\\" in p for p in parts):
        raise ValueError("identidade da associação inválida")
    return "|".join(parts)


def validate_notebook(data: Any) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("nbformat"), int) or not isinstance(data.get("cells"), list):
        raise ValueError("notebook inválido")


def _meta_path(root: Path) -> Path:
    directory = root / PERSISTENT_DIR / META_DIR
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory / META_FILE


def _read(root: Path) -> dict[str, dict[str, Any]]:
    path = _meta_path(root)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write(root: Path, data: dict[str, dict[str, Any]]) -> None:
    path = _meta_path(root)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        Path(temporary).replace(path)
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_notebook(root: Path, record: dict[str, Any], notebook: dict[str, Any]) -> dict[str, Any]:
    validate_notebook(notebook)
    term = sanitize_term(record["term"])
    course = sanitize_course_name(record["course_name"])
    section = sanitize_course_section(record.get("course_section"), record["course_id"])
    name = sanitize_notebook_name(record["source_file_name"])
    key = association_key(record["course_id"], record["coursework_id"], record["source_file_id"])
    course_path = _component(record["course_id"], allow_dot=True)
    coursework_path = _component(record["coursework_id"], allow_dot=True)
    final_name = destination_name(term, course, section, name, record["course_id"])
    relative = f"{PERSISTENT_DIR}/classroom-professor/{course_path}/{coursework_path}/{final_name}"
    target = root / relative
    if target.exists():
        raise ValueError("cópia local já existe")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(json.dumps(notebook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.chmod(0o600)
    data = _read(root)
    old = data.get(key)
    if old and old.get("target_file_id"):
        raise ValueError("associação já possui destino")
    data[key] = {
        "association_key": key,
        "course_id": record["course_id"], "coursework_id": record["coursework_id"],
        "course_name": record["course_name"], "coursework_title": record["coursework_title"],
        "coursework_description": record.get("coursework_description", ""), "creation_time": record.get("creation_time"),
        "source_file_id": record["source_file_id"], "source_file_name": name,
        "local_path": relative, "target_file_id": None, "target_file_name": final_name,
        "course_section": section, "term": term,
        "modified_time": record.get("modified_time"), "version": record.get("version"),
        "md5_checksum": record.get("md5_checksum"), "can_edit": record.get("can_edit"),
    }
    _write(root, data)
    return {"path": relative, "created": True, "association": data[key]}


def get_association(root: Path, key: str) -> dict[str, Any]:
    data = _read(root)
    if key not in data:
        raise KeyError("associação não encontrada")
    return data[key]


def update_association(root: Path, key: str, update: dict[str, Any], *, term: str | None = None) -> dict[str, Any]:
    data = _read(root)
    if key not in data:
        raise KeyError("associação não encontrada")
    current = data[key]
    source = current["source_file_id"]
    target = update.get("target_file_id", current.get("target_file_id"))
    if target == source:
        raise ValueError("sourceFileId não pode ser targetFileId")
    if current.get("target_file_id") and target != current["target_file_id"]:
        if not update.get("replace_missing_target") or update.get("expected_target_file_id") != current["target_file_id"]:
            raise ValueError("targetFileId não pode ser substituído sem recuperação explícita de destino ausente")
        if not isinstance(target, str) or not target:
            raise ValueError("novo targetFileId inválido")
        if target == current["target_file_id"]:
            raise ValueError("o novo targetFileId deve ser distinto do destino ausente")
    if "course_section" in update:
        section = sanitize_course_section(update["course_section"], current["course_id"])
        update = {**update, "course_section": section}
        if not current.get("target_file_id"):
            configured_term = term or current.get("term")
            if not configured_term:
                raise ValueError("período do professor não configurado")
            update["target_file_name"] = destination_name(
                configured_term, current["course_name"], section,
                current["source_file_name"], current["course_id"]
            )
    current.update({k: update[k] for k in ("course_section", "target_file_id", "target_file_name", "modified_time", "version", "md5_checksum", "can_edit", "creation_time") if k in update})
    _write(root, data)
    return current
