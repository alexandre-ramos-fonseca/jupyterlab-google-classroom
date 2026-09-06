"""Testes da extensão google-classroom-student (unittest, sem chamadas reais ao Google)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

EXT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_SOURCE = EXT_DIR / "src" / "index.ts"
sys.path.insert(0, str(EXT_DIR))

from google_classroom_student import storage  # noqa: E402
from google_classroom_student.handlers import (  # noqa: E402
    SaveHandler,
    config_response,
    load_jupyter_server_extension,
)

TOKEN = "test-token-123"


def _nb() -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [{"cell_type": "markdown", "metadata": {}, "source": ["oi"]}],
    }


class TestSanitize(unittest.TestCase):
    def test_sanitiza_nome(self):
        self.assertEqual(storage.sanitize_name("../dir/Derivadas.ipynb"), "Derivadas.ipynb")

    def test_rejeita_extensao(self):
        with self.assertRaises(ValueError):
            storage.sanitize_name("prova.txt")
        with self.assertRaises(ValueError):
            storage.sanitize_name("sem_extensao")

    def test_rejeita_notebook_invalido(self):
        with self.assertRaises(ValueError):
            storage.validate_notebook(None)
        with self.assertRaises(ValueError):
            storage.validate_notebook({"nbformat": "4"})
        with self.assertRaises(ValueError):
            storage.validate_notebook({"nbformat": 4})


class TestStorage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _metadata(self) -> dict:
        p = self.root / ".google-classroom-student" / "files.json"
        return json.loads(p.read_text("utf-8"))

    def test_cria_diretorios_e_permissoes(self):
        path, created = storage.save_notebook(
            self.root, "id-1", "Derivadas.ipynb", _nb(), course_id="c1", coursework_id="w1"
        )
        self.assertTrue(created)
        self.assertEqual(path, "classroom/Derivadas.ipynb")
        self.assertTrue((self.root / "classroom").is_dir())
        meta_dir = self.root / ".google-classroom-student"
        self.assertTrue(meta_dir.is_dir())
        self.assertEqual(oct(meta_dir.stat().st_mode & 0o777), "0o700")
        meta_file = meta_dir / "files.json"
        self.assertEqual(oct(meta_file.stat().st_mode & 0o777), "0o600")

    def test_salvamento_atomico(self):
        path, created = storage.save_notebook(self.root, "id-1", "A.ipynb", _nb())
        self.assertTrue(created)
        target = self.root / path
        self.assertTrue(target.is_file())
        data = json.loads(target.read_text("utf-8"))
        self.assertEqual(data["nbformat"], 4)
        self.assertIn("cells", data)
        leftovers = [p for p in target.parent.iterdir() if p.name.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_reutiliza_mesmo_file_id(self):
        path1, created1 = storage.save_notebook(self.root, "id-1", "A.ipynb", _nb())
        self.assertTrue(created1)
        target = self.root / path1
        original = target.read_bytes()
        nb2 = _nb()
        nb2["cells"].append({"cell_type": "markdown", "metadata": {}, "source": ["x"]})
        path2, created2 = storage.save_notebook(self.root, "id-1", "A.ipynb", nb2)
        self.assertFalse(created2)
        self.assertEqual(path1, path2)
        self.assertEqual(target.read_bytes(), original)

    def test_colisao_de_nomes_entre_file_ids(self):
        path1, _ = storage.save_notebook(self.root, "id-1", "Prova.ipynb", _nb())
        path2, created2 = storage.save_notebook(self.root, "id-2", "Prova.ipynb", _nb())
        self.assertEqual(path1, "classroom/Prova.ipynb")
        self.assertEqual(path2, "classroom/Prova (2).ipynb")
        self.assertTrue(created2)
        path3, _ = storage.save_notebook(self.root, "id-3", "Prova.ipynb", _nb())
        self.assertEqual(path3, "classroom/Prova (3).ipynb")

    def test_nao_sobrescreve_arquivo_local_existente(self):
        classroom = self.root / "classroom"
        classroom.mkdir(parents=True)
        (classroom / "Manual.ipynb").write_text('{"nbformat": 4, "cells": []}')
        path, created = storage.save_notebook(self.root, "id-9", "Manual.ipynb", _nb())
        self.assertTrue(created)
        self.assertEqual(path, "classroom/Manual (2).ipynb")
        self.assertEqual((classroom / "Manual.ipynb").read_text(), '{"nbformat": 4, "cells": []}')

    def test_nome_com_espacos_acentos_e_hifen(self):
        path, created = storage.save_notebook(
            self.root,
            "id-acento",
            "Virgínia - Derivadas.ipynb",
            _nb(),
        )
        self.assertTrue(created)
        self.assertEqual(path, "classroom/Virgínia - Derivadas.ipynb")

    def test_conteudo_do_files_json(self):
        storage.save_notebook(
            self.root, "id-1", "Derivadas.ipynb", _nb(), course_id="c1", coursework_id="w1"
        )
        meta = self._metadata()
        self.assertEqual(meta["id-1"]["path"], "classroom/Derivadas.ipynb")
        self.assertEqual(meta["id-1"]["course_id"], "c1")
        self.assertEqual(meta["id-1"]["coursework_id"], "w1")
        self.assertNotIn("access_token", meta["id-1"])
        raw = (self.root / ".google-classroom-student" / "files.json").read_text("utf-8")
        self.assertNotIn("token", raw.lower())
        self.assertNotIn("nbformat", raw)

    def test_associacao_preserva_baseline_e_atualiza_somente_baseline(self):
        path, _ = storage.save_notebook(
            self.root,
            "id-1",
            "Derivadas.ipynb",
            _nb(),
            course_id="c1",
            coursework_id="w1",
            submission_id="s1",
            submission_state="CREATED",
            modified_time="2026-01-01T00:00:00Z",
            version="1",
            md5_checksum="abc",
            can_edit=True,
        )
        association = storage.get_association(self.root, "id-1")
        self.assertEqual(association["path"], path)
        self.assertEqual(association["submission_id"], "s1")
        updated = storage.update_association(
            self.root, "id-1", path, "2026-01-02T00:00:00Z", "2", "def", True
        )
        self.assertEqual(updated["version"], "2")
        self.assertEqual(storage.get_association(self.root, "id-1")["version"], "2")

    def test_associacao_inexistente_nao_pode_ser_atualizada(self):
        with self.assertRaises(ValueError):
            storage.update_association(
                self.root, "arbitrario", "classroom/Notebook.ipynb", None, None, None, True
            )


class TestConfig(unittest.TestCase):
    def test_config_sem_client_id(self):
        os.environ.pop("GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID", None)
        resp = config_response()
        self.assertFalse(resp["configured"])
        self.assertIsNone(resp["client_id"])
        self.assertEqual(
            resp["scopes"],
            [
                "https://www.googleapis.com/auth/classroom.courses.readonly",
                "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
                "https://www.googleapis.com/auth/classroom.coursework.me",
                "https://www.googleapis.com/auth/drive",
            ],
        )

    def test_config_com_client_id(self):
        os.environ["GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID"] = "x.apps.googleusercontent.com"
        try:
            resp = config_response()
            self.assertTrue(resp["configured"])
            self.assertEqual(resp["client_id"], "x.apps.googleusercontent.com")
        finally:
            os.environ.pop("GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID", None)


class TestFrontendSource(unittest.TestCase):
    def setUp(self):
        self.source = FRONTEND_SOURCE.read_text("utf-8")

    def test_http_regressions(self):
        source = self.source
        self.assertIn("'Content-Type': 'application/json'", source)
        self.assertIn("new URL(baseUrl)", source)
        self.assertNotIn("`${baseUrl}?${qs}`", source)

    def test_oauth_usa_verificacao_oficial_de_escopos(self):
        source = self.source
        self.assertIn("hasGrantedAllScopes(response, scope)", source)
        self.assertNotIn("response.scope || '').split", source)
        self.assertIn("classroom.courses.readonly", source)
        self.assertIn("classroom.student-submissions.me.readonly", source)
        self.assertIn("classroom.coursework.me", source)
        self.assertIn("https://www.googleapis.com/auth/drive'", source)
        self.assertNotIn("drive.readonly", source)
        self.assertNotIn("calendar", source.lower())

    def test_student_submissions_sem_coursework_list(self):
        source = self.source
        self.assertIn("courseWork/-/studentSubmissions?userId=me", source)
        self.assertNotIn("courseWork?courseWorkStates=PUBLISHED", source)
        self.assertNotIn("courseWork.list", source)
        self.assertIn("pageSize", source)
        self.assertIn("nextPageToken", source)
        self.assertIn("listAll(", source)
        self.assertIn("assignmentSubmission", source)
        self.assertIn("driveFile", source)
        self.assertIn("/courseWork/${encodeURIComponent(courseworkId)}", source)
        self.assertIn("courseworkDetails.get(courseworkId)", source)
        self.assertIn("Atividade do Google Classroom", source)

    def test_atividade_e_unidade_visual_com_filtros_locais(self):
        source = self.source
        self.assertIn("export function groupClassroomFiles", source)
        self.assertIn("export function filterAndSortClassroomActivities", source)
        self.assertIn("courseworkId}`", source)
        self.assertIn("['newest', 'Mais recente']", source)
        self.assertIn("['oldest', 'Mais antiga']", source)
        self.assertIn("['name-asc', 'Nome A→Z']", source)
        self.assertIn("['name-desc', 'Nome Z→A']", source)
        self.assertIn("creationTime", source)
        self.assertIn("for (const file of activity.files)", source)
        self.assertNotIn("courseworkDescription", source)

    def test_configura_exatamente_os_tres_escopos_concedidos(self):
        source = self.source
        required_block = source.split("const REQUIRED_SCOPES = [", 1)[1].split("];", 1)[0]
        optional_block = source.split("const OPTIONAL_SCOPES = [", 1)[1].split("];", 1)[0]
        self.assertEqual(required_block.count("https://www.googleapis.com/auth/"), 3)
        self.assertEqual(optional_block.count("https://www.googleapis.com/auth/"), 1)
        self.assertIn("classroom.coursework.me", optional_block)
        self.assertNotIn("classroom.coursework.me.readonly", optional_block)
        self.assertIn("classroom.student-submissions.me.readonly", required_block)
        self.assertIn("https://www.googleapis.com/auth/drive'", required_block)
        self.assertNotIn("drive.readonly", required_block + optional_block)

    def test_sincronizacao_atualiza_mesmo_file_id_e_bloqueia_operacoes_amplas(self):
        source = self.source
        self.assertIn("upload/drive/v3/files/${encodeURIComponent(fileId)}", source)
        self.assertIn("method: 'PATCH'", source)
        self.assertIn("uploadType=media", source)
        self.assertNotIn("files.create", source)
        self.assertNotIn("files.list", source)
        self.assertNotIn("studentSubmissions.turnIn", source)
        self.assertNotIn("modifyAttachments", source)
        self.assertNotIn("studentSubmissions.reclaim", source)

    def test_sincronizacao_salva_local_confere_baseline_e_atualiza_associacao(self):
        source = self.source
        self.assertIn("saveTargetIfOpen", source)
        self.assertIn("documentManager.findWidget(path)", source)
        self.assertIn("documentManager.contextForWidget(widget)", source)
        self.assertIn("context.model.dirty", source)
        self.assertIn("await context.save()", source)
        self.assertIn("serviceManager.contents.get", source)
        self.assertIn("remoteChanged", source)
        self.assertIn("DriveConflictError", source)
        self.assertIn("/google-classroom-student/api/association", source)
        self.assertIn("A cópia local deste notebook não foi encontrada.", source)
        self.assertIn("Não foi possível salvar o notebook local antes da sincronização.", source)
        self.assertIn("canEdit", source)

    def test_reabertura_com_local_path_abre_a_copia_local_diretamente(self):
        source = self.source
        local_exists = source.split("async function localNotebookExists", 1)[1].split(
            "async function openLocalNotebook", 1
        )[0]
        local_open = source.split("async function openLocalNotebook", 1)[1].split(
            "/** Salva somente", 1
        )[0]
        opening = source.split("private async open(file: ClassroomFile)", 1)[1].split(
            "private async sync", 1
        )[0]
        self.assertIn("serviceManager.contents.get(path)", local_exists)
        self.assertIn("docmanager:open", local_open)
        self.assertIn("file.localPath && (await localNotebookExists(this.app, file.localPath))", opening)
        self.assertIn("await openLocalNotebook(this.app, file.localPath)", opening)

    def test_reabertura_local_nao_chama_drive_download_ou_save(self):
        source = self.source
        opening = source.split("private async open(file: ClassroomFile)", 1)[1].split(
            "private async sync", 1
        )[0]
        local_branch = opening.split("if (file.localPath", 1)[1].split(
            "if (!accessToken)", 1
        )[0]
        self.assertNotIn("openNotebook", local_branch)
        self.assertNotIn("downloadDriveNotebook", local_branch)
        self.assertNotIn("serverPost", local_branch)
        self.assertIn("return;", local_branch)

    def test_primeira_abertura_sem_local_path_mantem_importacao_drive(self):
        source = self.source
        opening = source.split("private async open(file: ClassroomFile)", 1)[1].split(
            "private async sync", 1
        )[0]
        self.assertLess(opening.index("if (!accessToken)"), opening.index("openNotebook"))
        self.assertIn("const imported = await openNotebook(this.app, accessToken, file)", opening)
        self.assertIn("file.localPath = imported.path", opening)

    def test_reabertura_local_nao_altera_baseline(self):
        source = self.source
        opening = source.split("private async open(file: ClassroomFile)", 1)[1].split(
            "private async sync", 1
        )[0]
        local_branch = opening.split("if (file.localPath", 1)[1].split(
            "if (!accessToken)", 1
        )[0]
        self.assertNotIn("file.baseline", local_branch)
        self.assertIn("return;", local_branch)
        self.assertIn("file.baseline = {", opening)

    def test_sincronizacao_compara_drive_atual_ao_baseline_persistido(self):
        source = self.source
        sync = source.split("async function synchronizeNotebook", 1)[1]
        self.assertIn("baselineFromAssociation(association)", sync)
        self.assertIn("remoteChanged(baselineFromAssociation(association), current)", sync)
        self.assertIn("await updateBaseline", sync)

    def test_conflito_oferece_exatamente_tres_decisoes(self):
        source = self.source
        conflict = source.split("async function resolveDriveConflict", 1)[1].split(
            "class ClassroomPanel", 1
        )[0]
        self.assertEqual(conflict.count("Dialog.okButton") + conflict.count("Dialog.warnButton") + conflict.count("Dialog.cancelButton"), 3)
        self.assertIn("Usar versão do Jupyter", conflict)
        self.assertIn("Usar versão do Google Drive", conflict)
        self.assertIn("Cancelar", conflict)

    def test_cancelamento_nao_escreve_e_conflito_permanece(self):
        source = self.source
        cancel = source.split("if (result.button.label === 'Cancelar')", 1)[1].split(
            "if (result.button.label === 'Usar versão do Jupyter')", 1
        )[0]
        self.assertIn("return null", cancel)
        sync = source.split("private async sync", 1)[1]
        self.assertIn("file.syncState = 'conflict'", sync)
        self.assertIn("Nenhuma versão foi alterada", sync)

    def test_jupyter_faz_backup_drive_antes_upload_e_baseline(self):
        source = self.source
        jupyter = source.split("if (result.button.label === 'Usar versão do Jupyter')", 1)[1].split(
            "await saveBackup(app, file.localPath, conflict.localNotebook, 'jupyter')", 1
        )[0]
        self.assertIn("downloadDriveNotebook", jupyter)
        self.assertIn("saveBackup(app, file.localPath, remoteNotebook, 'drive')", jupyter)
        self.assertLess(jupyter.index("saveBackup"), jupyter.index("uploadDriveContent"))
        self.assertLess(jupyter.index("uploadDriveContent"), jupyter.index("updateBaseline"))

    def test_drive_faz_backup_jupyter_antes_substituicao_e_baseline(self):
        source = self.source
        drive = source.split("async function resolveDriveConflict", 1)[1].split(
            "class ClassroomPanel", 1
        )[0]
        self.assertIn("downloadDriveNotebook", drive)
        self.assertIn("replaceLocalNotebook", drive)
        self.assertLess(drive.index("saveBackup"), drive.index("replaceLocalNotebook"))
        self.assertLess(drive.index("replaceLocalNotebook"), drive.rindex("await updateBaseline"))

    def test_backup_falha_impede_a_operacao_e_nome_tem_origem_timestamp_colisao(self):
        source = self.source
        backup = source.split("async function saveBackup", 1)[1].split(
            "async function replaceLocalNotebook", 1
        )[0]
        self.assertIn("while (true)", backup)
        self.assertIn("contents.get(candidate", backup)
        self.assertIn("suffix++", backup)
        self.assertIn("backup-${origin}", source)
        self.assertIn("toISOString()", source)
        self.assertNotIn("uploadDriveContent(token, file.fileId, conflict.localNotebook);\n    await saveBackup", source)

    def test_sincronizacao_injeta_document_manager_e_nao_salva_widget_ativo(self):
        source = self.source
        self.assertIn("import { IDocumentManager } from '@jupyterlab/docmanager';", source)
        self.assertIn("requires: [ICommandPalette, IMainMenu, IDocumentManager]", source)
        self.assertIn("private documentManager: IDocumentManager", source)
        self.assertIn("new ClassroomPanel(app, documentManager)", source)
        self.assertNotIn("app.commands.execute('docmanager:save'", source)

    def test_sincronizacao_alvo_aberto_dirty_salva_contexto_especifico(self):
        source = self.source
        block = source.split("async function saveTargetIfOpen", 1)[1].split(
            "async function synchronizeNotebook", 1
        )[0]
        self.assertIn("findWidget(path)", block)
        self.assertIn("contextForWidget(widget)", block)
        self.assertIn("context.path !== path && context.localPath !== path", block)
        self.assertIn("if (context.model.dirty)", block)
        self.assertIn("await context.save()", block)

    def test_sincronizacao_alvo_fechado_usa_contents_persistido(self):
        source = self.source
        block = source.split("async function saveTargetIfOpen", 1)[1].split(
            "async function synchronizeNotebook", 1
        )[0]
        self.assertIn("if (!widget)", block)
        self.assertIn("ContentsManager já representa a versão persistida", block)
        sync = source.split("async function synchronizeNotebook", 1)[1]
        self.assertIn("serviceManager.contents.get(file.localPath", sync)

    def test_falha_ao_salvar_alvo_impede_upload_e_baseline(self):
        source = self.source
        sync = source.split("async function synchronizeNotebook", 1)[1]
        save_end = sync.index("let contents")
        before_read = sync[:save_end]
        self.assertIn("Não foi possível salvar o notebook local antes da sincronização.", before_read)
        self.assertNotIn("uploadDriveContent", before_read)
        self.assertNotIn("update_association", before_read)

    def test_outro_documento_ativo_nao_interfere_no_alvo(self):
        source = self.source
        block = source.split("async function saveTargetIfOpen", 1)[1].split(
            "async function synchronizeNotebook", 1
        )[0]
        self.assertNotIn("currentWidget", block)
        self.assertNotIn("activateById", block)
        self.assertIn("findWidget(path)", block)

    def test_associacao_e_validada_antes_do_upload(self):
        source = self.source
        sync = source.split("async function synchronizeNotebook", 1)[1]
        self.assertLess(sync.index("/google-classroom-student/api/association"), sync.index("saveTargetIfOpen"))
        self.assertLess(sync.index("/google-classroom-student/api/association"), sync.index("uploadDriveContent"))
        self.assertIn("association.path !== file.localPath || association.file_id !== file.fileId", sync)

    def test_sincronizacao_nao_e_automatica(self):
        source = self.source
        startup = source.split("const openPanel = async", 1)[1].split(
            "app.commands.addCommand", 1
        )[0]
        self.assertNotIn("synchronizeNotebook", startup)
        self.assertNotIn("uploadDriveContent", startup)
        self.assertIn("Sincronizar com Drive", source)

    def test_metadado_legado_sem_can_edit_fica_desconhecido(self):
        source = self.source
        self.assertIn("record.can_edit == null ? undefined : !!record.can_edit", source)
        self.assertIn("file.canEdit === false", source)
        self.assertNotIn("record.can_edit === null ? false", source)

    def test_can_edit_true_false_e_desconhecido(self):
        source = self.source
        hydration = source.split("async function hydrateAssociations", 1)[1].split(
            "async function listAll", 1
        )[0]
        self.assertIn("!!record.can_edit", hydration)
        self.assertIn("undefined", hydration)
        render = source.split("for (const activity of filterAndSortClassroomActivities", 1)[1].split(
            "this.rootEl.appendChild(list)", 1
        )[0]
        self.assertIn("file.canEdit === false", render)
        self.assertIn("syncBtn.disabled = file.syncState === 'syncing' || file.canEdit === false", render)

    def test_textos_de_sincronizacao_usam_drive_e_painel_continua_classroom(self):
        source = self.source
        self.assertIn("Sincronizar com Drive", source)
        self.assertIn("Sincronizado com Drive", source)
        self.assertNotIn("Sincronizar com Google", source)
        self.assertNotIn("Sincronizado com Google", source)
        self.assertIn("Google Classroom", source)
        self.assertIn("method: 'PATCH'", source)
        self.assertNotIn("studentSubmissions.turnIn", source)

    def test_reautorizacao_usa_somente_escopos_ausentes(self):
        source = self.source
        self.assertIn("private missingScopes: string[] = [];", source)
        self.assertIn("scope: isReauthorization ? this.missingScopes.join(' ') : SCOPE_STR", source)
        self.assertIn("include_granted_scopes: true", source)
        self.assertIn("prompt: isReauthorization ? 'consent' : ''", source)
        self.assertIn("this.missingScopes = missing", source)
        self.assertIn("this.missingScopes = []", source)

    def test_token_response_scope_e_fonte_primaria(self):
        source = self.source
        self.assertIn("export function parseGrantedScopes(scope: unknown): string[]", source)
        self.assertIn("scope.trim().split(/\\s+/).filter(Boolean)", source)
        self.assertIn("new Set(returnedScopes)", source)
        self.assertIn("const grantedScopes = scopeFieldPresent", source)
        self.assertIn("? returnedScopes", source)
        self.assertIn("const missingRequired = scopeFieldPresent", source)
        self.assertIn("const missingOptional = scopeFieldPresent", source)
        self.assertIn("if (missingRequired.length)", source)
        self.assertIn("this.courseworkScopeGranted = !missingOptional.length", source)
        self.assertIn("void this.refresh(scopeDiverges)", source)

    def test_scope_vazio_faz_fallback_e_divergencia_e_diagnosticada(self):
        source = self.source
        self.assertIn("const scopeFieldPresent = returnedScopes.length > 0", source)
        self.assertIn("missingFromHelper: helperAvailable ? missingFromHelper : 'indisponível'", source)
        self.assertIn("diverges: scopeDiverges", source)
        self.assertIn("source: scopeFieldPresent ? 'TokenResponse.scope' : 'hasGrantedAllScopes'", source)
        self.assertIn("A resposta OAuth não trouxe o campo scope", source)

    def test_interface_reautorizacao_e_diagnostico_sanitizado(self):
        source = self.source
        self.assertIn("Autorizar permissões restantes", source)
        self.assertIn("Solicitando as permissões restantes ao Google...", source)
        self.assertIn("requestedScopeCount", source)
        self.assertIn("missingScopeCount", source)
        self.assertNotIn("response.scope.split", source)
        for forbidden in ("console.log(response", "console.log(token", "console.log(accessToken"):
            self.assertNotIn(forbidden, source)

    def test_cancelamento_preserva_escopos_e_nao_consulta_classroom(self):
        source = self.source
        callback = source.split("error_callback: (error: GsiTokenError) =>", 1)[1].split(
            "tokenClient.requestAccessToken", 1
        )[0]
        self.assertIn("this.errorMsg = authErrorMessage(error)", callback)
        self.assertNotIn("this.missingScopes = []", callback)
        insufficient = source.split("if (missingRequired.length)", 1)[1].split(
            "this.missingScopes = []", 1
        )[0]
        self.assertNotIn("refresh()", insufficient)
        self.assertIn("const missingOptional = scopeFieldPresent", source)
        optional_only = source.split("if (missingRequired.length)", 1)[1].split(
            "this.missingScopes = missing", 1
        )[0]
        self.assertNotIn("return;", optional_only)

    def test_tipos_request_access_token_permit_scope_e_consentimento(self):
        types = (EXT_DIR / "src" / "google.d.ts").read_text("utf-8")
        self.assertIn("scope?: string", types)
        self.assertIn("include_granted_scopes?: boolean", types)
        self.assertIn("prompt?: string", types)

    def test_escopos_originais_sem_acrescimo(self):
        source = self.source
        required_block = source.split("const REQUIRED_SCOPES = [", 1)[1].split("];", 1)[0]
        optional_block = source.split("const OPTIONAL_SCOPES = [", 1)[1].split("];", 1)[0]
        self.assertEqual(required_block.count("https://www.googleapis.com/auth/"), 3)
        self.assertEqual(optional_block.count("https://www.googleapis.com/auth/"), 1)
        for scope in (
            "classroom.courses.readonly",
            "classroom.student-submissions.me.readonly",
            "https://www.googleapis.com/auth/drive",
        ):
            self.assertIn(scope, required_block)
        self.assertIn("classroom.coursework.me", optional_block)
        self.assertNotIn("classroom.coursework.me.readonly", optional_block)

    def test_erro_de_escopo_visivel_com_botao_de_reconexao(self):
        source = self.source
        disconnected = source.split("if (!this.isConnected())", 1)[1].split(
            "const actions", 1
        )[0]
        self.assertIn("this.errorMsg", disconnected)
        self.assertIn("Conectar ao Google", disconnected)
        self.assertIn("SCOPE_LABELS", source)

    def test_falhas_http_nao_sao_convertidas_em_lista_vazia(self):
        source = self.source
        self.assertIn("class GoogleApiError", source)
        self.assertIn("recordGoogleFailure", source)
        self.assertIn("successfulCourses", source)
        self.assertIn("result.warning", source)
        for status in ("401", "403", "404"):
            self.assertIn(status, source)

    def test_root_dir_e_erro_de_abertura(self):
        source = self.source
        handlers_source = (
            EXT_DIR / "google_classroom_student" / "handlers.py"
        ).read_text(encoding="utf-8")
        self.assertIn("server_app.root_dir", handlers_source)
        self.assertIn('{"root_dir": root_dir}', handlers_source)
        self.assertIn("NotebookOpenError", source)
        self.assertIn("Notebook salvo em", source)
        self.assertIn("docmanager:open", source)

    def test_painel_abre_automaticamente_apos_inicio(self):
        source = self.source
        self.assertIn("autoStart: true", source)
        self.assertIn("const ensurePanel = (): ClassroomPanel =>", source)
        self.assertIn("await app.started", source)
        self.assertIn("app.shell.add(panel, 'right')", source)
        self.assertIn("app.shell.activateById(currentPanel.id)", source)
        self.assertIn("void openPanel()", source)

    def test_menu_google_reutiliza_o_command_id(self):
        source = self.source
        self.assertIn("import { IMainMenu } from '@jupyterlab/mainmenu';", source)
        self.assertIn("requires: [ICommandPalette, IMainMenu, IDocumentManager]", source)
        self.assertIn("const googleMenu = new Menu({ commands: app.commands });", source)
        self.assertIn("googleMenu.title.label = 'Google'", source)
        self.assertIn("googleMenu.addItem({ command: COMMAND_ID })", source)
        self.assertIn("mainMenu.addMenu(googleMenu, true, { rank: 900 })", source)
        self.assertIn("label: 'Google Classroom'", source)

    def test_menu_nao_cria_outra_abertura_de_painel(self):
        source = self.source
        menu_block = source.split("const googleMenu", 1)[1]
        self.assertNotIn("new ClassroomPanel", menu_block)
        self.assertNotIn("requestAccessToken", menu_block)
        self.assertEqual(source.count("new ClassroomPanel(app, documentManager)"), 1)
        self.assertGreaterEqual(source.count("ensurePanel()"), 2)

    def test_comando_reutiliza_painel_e_start_e_idempotente(self):
        source = self.source
        self.assertEqual(source.count("new ClassroomPanel(app, documentManager)"), 1)
        self.assertGreaterEqual(source.count("ensurePanel()"), 2)
        self.assertIn("private startPromise: Promise<void> | null = null", source)
        self.assertIn("if (!this.startPromise)", source)
        self.assertIn("if (!panel || panel.isDisposed)", source)

    def test_inicializacao_nao_inicia_oauth(self):
        source = self.source
        startup = source.split("const openPanel = async", 1)[1].split(
            "app.commands.addCommand", 1
        )[0]
        self.assertNotIn("requestAccessToken", startup)
        self.assertIn("await currentPanel.start()", startup)

    def test_token_nao_e_persistido(self):
        source = self.source.lower()
        for storage_api in ("localstorage", "sessionstorage", "document.cookie"):
            self.assertNotIn(storage_api, source)
        self.assertIn("let accesstoken: string | null = null", source)

    def test_backend_nao_recebe_token_google(self):
        handlers = (EXT_DIR / "google_classroom_student" / "handlers.py").read_text("utf-8")
        self.assertNotIn("Authorization", handlers)
        self.assertNotIn("access_token", handlers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(url, token=None, data=None, no_redirect=False):
    headers = {}
    if token:
        headers["Authorization"] = "token " + token
    body = None
    method = "GET"
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener()
    if no_redirect:
        opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(req, timeout=15) as resp:
        if resp.status == 200:
            return json.loads(resp.read().decode("utf-8"))
        raise urllib.error.HTTPError(url, resp.status, "http", resp.headers, resp)


class TestEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        (cls.root / "root").mkdir()

        cfg_dir = cls.root / "config"
        cfg_dir.mkdir()
        (cfg_dir / "jupyter_server_config.json").write_text(
            json.dumps({"ServerApp": {"jpserver_extensions": {"google_classroom_student": True}}})
        )

        port = _free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(EXT_DIR)
        env["JUPYTER_CONFIG_DIR"] = str(cfg_dir)
        env["JUPYTER_DATA_DIR"] = str(cls.root / "data")
        env["JUPYTER_RUNTIME_DIR"] = str(cls.root / "runtime")
        env["GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID"] = "dummy.apps.googleusercontent.com"

        cls.base = f"http://127.0.0.1:{port}"
        cls.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "jupyter_server",
                "--IdentityProvider.token=" + TOKEN,
                f"--ServerApp.port={port}",
                f"--ServerApp.root_dir={cls.root / 'root'}",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls._wait_ready()

    @classmethod
    def _wait_ready(cls, timeout: float = 40.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError("jupyter_server terminou antes de iniciar")
            try:
                _request(cls.base + "/google-classroom-student/api/health", token=TOKEN, no_redirect=True)
                return
            except urllib.error.HTTPError as exc:
                if exc.code == 200:
                    return
                time.sleep(0.5)
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("timeout aguardando o Jupyter Server")

    @classmethod
    def tearDownClass(cls):
        if cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        cls._tmp.cleanup()

    def test_health_exige_autenticacao(self):
        resp = _request(self.base + "/google-classroom-student/api/health", token=TOKEN)
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["version"], "0.3.9")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _request(self.base + "/google-classroom-student/api/health", no_redirect=True)
        self.assertIn(ctx.exception.code, (302, 403))

    def test_config_exige_autenticacao(self):
        resp = _request(self.base + "/google-classroom-student/api/config", token=TOKEN)
        self.assertTrue(resp["configured"])
        self.assertEqual(resp["client_id"], "dummy.apps.googleusercontent.com")
        self.assertEqual(len(resp["scopes"]), 4)
        self.assertIn("https://www.googleapis.com/auth/drive", resp["scopes"])
        self.assertIn("https://www.googleapis.com/auth/classroom.coursework.me", resp["scopes"])
        self.assertNotIn("https://www.googleapis.com/auth/classroom.coursework.me.readonly", resp["scopes"])
        self.assertNotIn("https://www.googleapis.com/auth/drive.readonly", resp["scopes"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _request(self.base + "/google-classroom-student/api/config", no_redirect=True)
        self.assertIn(ctx.exception.code, (302, 403))

    def test_save_cria_arquivo_e_nao_aceita_caminho(self):
        payload = {
            "file_id": "fid-1",
            "file_name": "../../Derivadas.ipynb",
            "course_id": "c1",
            "course_name": "Curso",
            "coursework_id": "w1",
            "coursework_title": "Atividade",
            "notebook": _nb(),
        }
        resp = _request(self.base + "/google-classroom-student/api/save", token=TOKEN, data=payload)
        self.assertTrue(resp["created"])
        self.assertTrue(resp["path"].startswith("classroom/"))
        self.assertNotIn("..", resp["path"])
        expected = self.root / "root" / resp["path"]
        self.assertTrue(expected.is_file())
        self.assertFalse((Path.cwd() / resp["path"]).is_file())

    def test_save_handler_recebe_root_absoluto_e_nao_usa_cwd(self):
        handler = SaveHandler.__new__(SaveHandler)
        with self.assertRaises(RuntimeError):
            handler.initialize()
        with self.assertRaises(RuntimeError):
            handler.initialize("relative/root")
        with self.assertRaises(RuntimeError):
            handler.initialize("/definitely/missing/google-classroom-student-root")

    def test_extensao_falha_sem_root_dir_do_servidor(self):
        class FakeWebApp:
            settings = {"base_url": "/"}

        class FakeServer:
            root_dir = None
            web_app = FakeWebApp()

        with self.assertRaisesRegex(RuntimeError, "root_dir ausente"):
            load_jupyter_server_extension(FakeServer())

    def test_save_notebook_invalido_400(self):
        payload = {
            "file_id": "fid-2",
            "file_name": "Invalido.ipynb",
            "course_id": "c1",
            "course_name": "Curso",
            "coursework_id": "w1",
            "coursework_title": "Atividade",
            "notebook": {"nbformat": 4},
        }
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _request(self.base + "/google-classroom-student/api/save", token=TOKEN, data=payload)
        self.assertEqual(ctx.exception.code, 400)

    def test_save_sem_autenticacao_redireciona(self):
        payload = {
            "file_id": "fid-3",
            "file_name": "SemAuth.ipynb",
            "course_id": "c1",
            "course_name": "Curso",
            "coursework_id": "w1",
            "coursework_title": "Atividade",
            "notebook": _nb(),
        }
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            _request(self.base + "/google-classroom-student/api/save", data=payload, no_redirect=True)
        self.assertIn(ctx.exception.code, (302, 403))


if __name__ == "__main__":
    unittest.main()
