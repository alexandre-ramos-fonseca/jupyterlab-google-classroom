"""Regressões do escopo opcional usado para títulos das atividades."""
from pathlib import Path
import unittest

EXT_DIR = Path(__file__).resolve().parents[1]
SOURCE = (EXT_DIR / "src" / "index.ts").read_text("utf-8")


class TestOptionalCourseworkScope(unittest.TestCase):
    def test_coursework_scope_nao_e_obrigatorio(self):
        required = SOURCE.split("const REQUIRED_SCOPES = [", 1)[1].split("];", 1)[0]
        optional = SOURCE.split("const OPTIONAL_SCOPES = [", 1)[1].split("];", 1)[0]

        self.assertIn("classroom.courses.readonly", required)
        self.assertIn("classroom.student-submissions.me.readonly", required)
        self.assertIn("https://www.googleapis.com/auth/drive'", required)
        self.assertNotIn("classroom.coursework.me", required)
        self.assertEqual(optional.count("https://www.googleapis.com/auth/"), 1)
        self.assertIn("https://www.googleapis.com/auth/classroom.coursework.me'", optional)
        self.assertNotIn("classroom.coursework.me.readonly", optional)

    def test_ausencia_do_escopo_opcional_nao_bloqueia_token(self):
        on_token = SOURCE.split("private onToken", 1)[1].split(
            "private async refresh", 1
        )[0]
        self.assertIn("if (missingRequired.length)", on_token)
        self.assertIn("this.courseworkScopeGranted = !missingOptional.length", on_token)
        self.assertIn("accessToken = response.access_token", on_token)
        self.assertLess(
            on_token.index("if (missingRequired.length)"),
            on_token.index("accessToken = response.access_token"),
        )
        self.assertNotIn("if (missing.length) {", on_token)

    def test_sem_escopo_opcional_usa_fallback_sem_chamar_coursework_get(self):
        collect = SOURCE.split("async function collectClassroomFiles", 1)[1].split(
            "function validateNotebook", 1
        )[0]
        self.assertIn("courseworkScopeGranted: boolean", collect)
        self.assertIn("if (courseworkScopeGranted)", collect)
        self.assertIn("/courseWork/${encodeURIComponent(courseworkId)}", collect)
        self.assertIn("coursework?.title || 'Atividade do Google Classroom'", collect)

    def test_coursework_e_classroom_sao_somente_leitura_no_frontend(self):
        self.assertNotIn("studentSubmissions.turnIn", SOURCE)
        self.assertNotIn("studentSubmissions.reclaim", SOURCE)
        self.assertNotRegex(SOURCE, r"courseWork\.(create|patch|delete)")
        self.assertNotRegex(
            SOURCE,
            r"classroom\.googleapis\.com[\s\S]{0,500}method: ['\"](?:POST|PATCH|DELETE)",
        )

    def test_painel_conectado_nao_exibe_botao_permanente_de_titulos(self):
        render = SOURCE.split("private render", 1)[1].split("private async connect", 1)[0]
        self.assertNotIn("Autorizar títulos das atividades", render)
        self.assertIn("refresh.textContent = 'Atualizar'", render)
        self.assertIn("disconnectBtn.textContent = 'Desconectar'", render)

    def test_painel_reserva_area_restante_para_lista_rolavel(self):
        constructor = SOURCE.split("constructor(", 1)[1].split("async start", 1)[0]
        render = SOURCE.split("private render", 1)[1].split("private async connect", 1)[0]
        self.assertIn("this.rootEl.style.display = 'flex'", constructor)
        self.assertIn("this.rootEl.style.flexDirection = 'column'", constructor)
        self.assertIn("this.rootEl.style.height = '100%'", constructor)
        self.assertIn("this.rootEl.style.minHeight = '0'", constructor)
        self.assertIn("this.rootEl.style.overflow = 'hidden'", constructor)
        self.assertIn("flex: '1 1 auto'", render)
        self.assertIn("minHeight: '0'", render)
        self.assertIn("overflowY: 'auto'", render)
        self.assertIn("this.rootEl.appendChild(list)", render)

    def test_painel_reutiliza_hierarquia_visual_do_professor(self):
        render = SOURCE.split("private render", 1)[1].split("private async connect", 1)[0]
        self.assertIn("Google Classroom — Aluno", render)
        self.assertIn("document.createElement('section')", render)
        self.assertIn("border: '1px solid var(--jp-border-color2)'", render)
        self.assertIn("document.createElement('h3')", render)
        self.assertIn("document.createElement('strong')", render)
        self.assertNotIn("document.createElement('ul')", render)
        self.assertNotIn("document.createElement('li')", render)
        self.assertNotIn("courseworkDescription", render)

    def test_refresh_recebe_capacidade_do_escopo_opcional(self):
        refresh = SOURCE.split("private async refresh", 1)[1].split("private async open", 1)[0]
        self.assertIn(
            "collectClassroomFiles(token, this.courseworkScopeGranted)",
            refresh,
        )


if __name__ == "__main__":
    unittest.main()
