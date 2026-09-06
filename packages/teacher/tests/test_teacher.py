"""Testes do MVP de professores sem chamadas reais ao Google."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from google_classroom_teacher import storage
from google_classroom_teacher.handlers import SCOPES, _record_with_server_term, config_response


def notebook():
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": []}


def record(course="c1", work="w1", source="src1", name="derivadas.ipynb"):
    return {
        "term": "2026-2", "course_id": course, "coursework_id": work,
        "course_name": "Cálculo Numérico", "course_section": "TurmaA", "coursework_title": "Derivadas",
        "source_file_id": source, "source_file_name": name,
        "modified_time": "2026-01-01T00:00:00Z", "version": "1", "md5_checksum": "abc", "can_edit": False,
    }


class TestNaming(unittest.TestCase):
    def test_nome_exato(self):
        self.assertEqual(storage.destination_name("2026-2", "CALCULO NUMERICO", "TurmaA", "Derivadas.ipynb", "c1"), "2026-2_CALCULO-NUMERICO_TurmaA_Derivadas.ipynb")
        self.assertEqual(storage.destination_name("2026-2", "CALCULO NUMERICO", "TurmaB", "Derivadas.ipynb", "c1"), "2026-2_CALCULO-NUMERICO_TurmaB_Derivadas.ipynb")
        self.assertEqual(storage.destination_name("2026-2", "CALCULO NUMERICO", "Matriz", "Derivadas.ipynb", "c1"), "2026-2_CALCULO-NUMERICO_Matriz_Derivadas.ipynb")

    def test_sanitiza_periodo_turma_arquivo(self):
        self.assertEqual(storage.sanitize_term("2026/2"), "2026-2")
        self.assertEqual(storage.sanitize_course_name("Cálculo Numérico"), "Calculo-Numerico")
        self.assertEqual(storage.sanitize_course_section("TurmaA", "c1"), "TurmaA")
        self.assertEqual(storage.sanitize_notebook_name("derivadas.ipynb"), "derivadas.ipynb")

    def test_section_vazia_tem_fallback_deterministico(self):
        self.assertEqual(storage.sanitize_course_section("", "course-123"), "Curso-course-123")

    def test_secoes_reais_com_barra_sao_componentes_seguros(self):
        self.assertEqual(storage.sanitize_course_section("2026/2 - CTD204 - A", "c1"), "2026-2-CTD204-A")
        self.assertEqual(storage.sanitize_course_section("2026/2 - CTD204 - B", "c1"), "2026-2-CTD204-B")
        for value in ("2026/2/../A", "2026/2\\A", "2026/2\x00A"):
            with self.assertRaises(ValueError): storage.sanitize_course_section(value, "c1")

    def test_rejeita_traversal(self):
        for value in ("../x.ipynb", "a/b.ipynb", "a\\b.ipynb"):
            with self.assertRaises(ValueError): storage.sanitize_notebook_name(value)
        with self.assertRaises(ValueError): storage.association_key("../c", "w", "s")


class TestAssociations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_associacao_composta_e_metadados_seguros(self):
        saved = storage.save_notebook(self.root, record(), notebook())
        self.assertEqual(saved["path"], "work/classroom-professor/c1/w1/2026-2_Calculo-Numerico_TurmaA_derivadas.ipynb")
        meta = self.root / "work" / ".google-classroom-teacher" / "associations.json"
        self.assertEqual(meta.stat().st_mode & 0o777, 0o600)
        self.assertEqual(meta.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("token", meta.read_text().lower())
        self.assertNotIn("nbformat", meta.read_text())
        self.assertEqual(saved["association"]["course_section"], "TurmaA")
        self.assertFalse((self.root / ".google-classroom-teacher").exists())
        self.assertFalse((self.root / "classroom-professor").exists())

    def test_data_da_atividade_e_descricao_sao_persistidas(self):
        saved = storage.save_notebook(self.root, {**record(), "coursework_description": "Lista 1", "creation_time": "2026-08-20T12:00:00Z"}, notebook())
        self.assertEqual(saved["association"]["coursework_description"], "Lista 1")
        self.assertEqual(saved["association"]["creation_time"], "2026-08-20T12:00:00Z")

    def test_persistencia_no_work_e_releitura_por_nova_instancia(self):
        saved = storage.save_notebook(self.root, record(), notebook())
        key = saved["association"]["association_key"]
        storage.update_association(self.root, key, {"target_file_id": "target1"})
        reopened = storage.get_association(self.root, key)
        self.assertEqual(reopened["local_path"], saved["path"])
        self.assertEqual(reopened["target_file_id"], "target1")
        self.assertTrue((self.root / saved["path"]).exists())

    def test_mesmo_source_em_duas_turmas_e_independente(self):
        a = storage.save_notebook(self.root, record(course="a", work="1", source="same"), notebook())
        b = storage.save_notebook(self.root, record(course="b", work="2", source="same"), notebook())
        self.assertNotEqual(a["path"], b["path"])
        self.assertNotEqual(a["association"]["association_key"], b["association"]["association_key"])

    def test_target_persistido_e_imutavel(self):
        saved = storage.save_notebook(self.root, record(), notebook())
        key = saved["association"]["association_key"]
        updated = storage.update_association(self.root, key, {"target_file_id": "target1", "target_file_name": "final.ipynb"})
        self.assertEqual(updated["target_file_id"], "target1")
        with self.assertRaises(ValueError): storage.update_association(self.root, key, {"target_file_id": "target2"})
        with self.assertRaises(ValueError): storage.update_association(self.root, key, {"target_file_id": "src1"})

    def test_associacao_legada_sem_section_continua_legivel(self):
        saved = storage.save_notebook(self.root, record(), notebook())
        key = saved["association"]["association_key"]
        metadata = self.root / "work" / ".google-classroom-teacher" / "associations.json"
        data = json.loads(metadata.read_text(encoding="utf-8"))
        data[key].pop("course_section")
        data[key]["target_file_id"] = "legacy-target"
        metadata.write_text(json.dumps(data), encoding="utf-8")
        legacy = storage.get_association(self.root, key)
        self.assertEqual(legacy["target_file_id"], "legacy-target")
        self.assertNotIn("course_section", legacy)

    def test_associacao_legada_sem_section_e_sem_target_recalcula_nome(self):
        legacy_record = {**record(), "course_name": "CALCULO NUMERICO", "course_section": None, "source_file_name": "Derivadas.ipynb"}
        saved = storage.save_notebook(self.root, legacy_record, notebook())
        key = saved["association"]["association_key"]
        metadata = self.root / "work" / ".google-classroom-teacher" / "associations.json"
        data = json.loads(metadata.read_text(encoding="utf-8"))
        data[key].pop("course_section")
        data[key].pop("term")
        data[key]["target_file_name"] = "2026-2_CALCULO-NUMERICO_Curso-c1_Derivadas.ipynb"
        metadata.write_text(json.dumps(data), encoding="utf-8")

        updated = storage.update_association(self.root, key, {"course_section": "TurmaA"}, term="2026-2")

        self.assertEqual(updated["target_file_name"], "2026-2_CALCULO-NUMERICO_TurmaA_Derivadas.ipynb")
        self.assertIsNone(updated["target_file_id"])

    def test_legado_com_target_existente_nao_renomeia_nem_recria(self):
        saved = storage.save_notebook(self.root, record(), notebook())
        key = saved["association"]["association_key"]
        metadata = self.root / "work" / ".google-classroom-teacher" / "associations.json"
        data = json.loads(metadata.read_text(encoding="utf-8"))
        data[key].pop("course_section")
        data[key]["target_file_id"] = "legacy-target"
        data[key]["target_file_name"] = "legacy-name.ipynb"
        metadata.write_text(json.dumps(data), encoding="utf-8")
        local_path = self.root / data[key]["local_path"]
        before = local_path.read_bytes()

        updated = storage.update_association(self.root, key, {"course_section": "TurmaA"})

        self.assertEqual(updated["target_file_id"], "legacy-target")
        self.assertEqual(updated["target_file_name"], "legacy-name.ipynb")
        self.assertEqual(local_path.read_bytes(), before)

    def test_primeira_abertura_cria_e_segunda_nao_sobrescreve(self):
        first = storage.save_notebook(self.root, record(), notebook())
        path = self.root / first["path"]
        path.write_text(json.dumps({"nbformat": 4, "cells": [{"source": ["alteração local"]}]}), encoding="utf-8")
        self.assertRaises(ValueError, storage.save_notebook, self.root, record(), notebook())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["cells"][0]["source"][0], "alteração local")

    def test_save_notebook_aceita_secoes_reais_a_e_b(self):
        a = storage.save_notebook(self.root, {**record(course="course-a", work="work-a", source="source-a"), "course_section": "2026/2 - CTD204 - A"}, notebook())
        b = storage.save_notebook(self.root, {**record(course="course-b", work="work-b", source="source-b"), "course_section": "2026/2 - CTD204 - B"}, notebook())
        self.assertIn("2026-2-CTD204-A", a["path"])
        self.assertIn("2026-2-CTD204-B", b["path"])
        self.assertEqual(a["association"]["association_key"], "course-a|work-a|source-a")
        self.assertEqual(b["association"]["association_key"], "course-b|work-b|source-b")


class TestTeacherContract(unittest.TestCase):
    def test_periodo_do_backend_substitui_periodo_do_frontend(self):
        os.environ["GOOGLE_CLASSROOM_TEACHER_TERM"] = "2026-2"
        try:
            record_with_arbitrary_term = {**record(), "term": "periodo-controlado-pelo-browser"}
            self.assertEqual(_record_with_server_term(record_with_arbitrary_term)["term"], "2026-2")
        finally:
            os.environ.pop("GOOGLE_CLASSROOM_TEACHER_TERM", None)

    def test_config_scopes_and_term(self):
        os.environ["GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID"] = "public-dummy"
        os.environ["GOOGLE_CLASSROOM_TEACHER_TERM"] = "2026-2"
        try:
            config = config_response()
            self.assertTrue(config["configured"])
            self.assertEqual(config["term"], "2026-2")
            self.assertEqual(config["scopes"], SCOPES)
            self.assertIn("drive.file", " ".join(SCOPES))
            self.assertNotIn("auth/drive\"", SCOPES)
        finally:
            os.environ.pop("GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID", None)
            os.environ.pop("GOOGLE_CLASSROOM_TEACHER_TERM", None)

    def test_frontend_separado_e_source_readonly(self):
        source = (Path(__file__).parents[1] / "src" / "index.ts").read_text()
        self.assertIn("uploadTargetContent", source)
        self.assertIn("sourceFileId === targetFileId", source)
        self.assertIn("/copy?", source)
        self.assertNotIn("uploadDriveContent(token, file.sourceFileId", source)
        self.assertIn("courseWork?courseWorkStates=PUBLISHED", source)
        self.assertIn("teacherId=me", source)
        self.assertIn("courseSection", source)
        self.assertIn("String(course.section || '').trim()", source)
        self.assertIn("course_section: file.courseSection", source)
        self.assertIn("groupTeacherFiles", source)
        self.assertIn("filterAndSortTeacherActivities", source)
        self.assertIn("coursework.creationTime", source)
        self.assertIn("courseworkDescription", source)
        self.assertNotIn("if (activity.courseworkDescription)", source)
        self.assertIn("courseFilter", source)
        self.assertIn("activitySort", source)
        self.assertIn("document.createElement('section')", source)
        self.assertIn("document.createElement('div'); notebook.className", source)
        self.assertIn("if (file.state === 'syncing' || !accessToken) return", source)
        self.assertIn("sync.disabled = file.state === 'syncing'", source)
        self.assertIn("existingAssociation", source)
        self.assertIn("restauração bloqueada", source)
        self.assertIn("Google Classroom — Professor", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)
