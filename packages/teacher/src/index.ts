import { JupyterFrontEnd, JupyterFrontEndPlugin } from '@jupyterlab/application';
import { ICommandPalette } from '@jupyterlab/apputils';
import { IDocumentManager } from '@jupyterlab/docmanager';
import { IMainMenu } from '@jupyterlab/mainmenu';
import { ServerConnection } from '@jupyterlab/services';
import { Menu, Widget } from '@lumino/widgets';

const PLUGIN_ID = 'jupyterlab-google-classroom-teacher:plugin';
const COMMAND_ID = 'jupyterlab-google-classroom-teacher:open';
const PANEL_ID = 'google-classroom-teacher-panel';
const GIS_SCRIPT = 'https://accounts.google.com/gsi/client';
export const SCOPES = [
  'https://www.googleapis.com/auth/classroom.courses.readonly',
  'https://www.googleapis.com/auth/classroom.coursework.students.readonly',
  'https://www.googleapis.com/auth/drive.readonly',
  'https://www.googleapis.com/auth/drive.file'
];

const settings = ServerConnection.makeSettings();
let accessToken: string | null = null;
let expiresAt = 0;
let tokenClient: TeacherTokenClient | null = null;
let gisPromise: Promise<void> | null = null;

export interface TeacherFile {
  associationKey: string;
  courseId: string;
  courseworkId: string;
  courseName: string;
  courseSection: string;
  courseworkTitle: string;
  courseworkDescription: string;
  creationTime: string | null;
  sourceFileId: string;
  sourceFileName: string;
  localPath?: string;
  targetFileId?: string | null;
  targetFileName?: string | null;
  baseline?: Baseline;
  canEdit?: boolean;
  state?: 'syncing' | 'synced' | 'conflict' | 'error';
  message?: string;
}

export interface TeacherActivity {
  key: string;
  courseId: string;
  courseworkId: string;
  courseName: string;
  courseSection: string;
  courseworkTitle: string;
  courseworkDescription: string;
  creationTime: string | null;
  files: TeacherFile[];
}

export type ActivitySort = 'newest' | 'oldest' | 'name-asc' | 'name-desc';

interface Baseline { modifiedTime: string | null; version: string | null; md5Checksum: string | null; }
interface DriveMetadata extends Baseline { id: string; name: string; mimeType: string; canDownload: boolean; canEdit: boolean; }
interface TeacherAssociation {
  association_key: string; course_id: string; coursework_id: string; course_name: string; course_section?: string | null;
  coursework_title: string; source_file_id: string; source_file_name: string; local_path: string;
  target_file_id: string | null; target_file_name: string | null;
  modified_time: string | null; version: string | null; md5_checksum: string | null; can_edit: boolean | null;
}

export function associationIdentity(courseId: string, courseworkId: string, sourceFileId: string): string {
  return `${courseId}|${courseworkId}|${sourceFileId}`;
}

export function parseTeacherCoursework(course: any, coursework: any): TeacherFile[] {
  const files: TeacherFile[] = [];
  const materials = Array.isArray(coursework.materials) ? coursework.materials : [];
  for (const material of materials) {
    const drive = material?.driveFile?.driveFile;
    if (!drive?.id) continue;
    const name = String(material?.driveFile?.title || drive.title || '').trim();
    if (!name.toLowerCase().endsWith('.ipynb')) continue;
    const courseId = String(course.id || '');
    const courseworkId = String(coursework.id || '');
    const sourceFileId = String(drive.id);
    const courseSection = String(course.section || '').trim();
    if (!courseId || !courseworkId) continue;
    files.push({
      associationKey: associationIdentity(courseId, courseworkId, sourceFileId),
      courseId, courseworkId, courseName: String(course.name || '(turma sem nome)'), courseSection,
      courseworkTitle: String(coursework.title || '(atividade sem título)'),
      courseworkDescription: String(coursework.description || '').trim(),
      creationTime: typeof coursework.creationTime === 'string' ? coursework.creationTime : null,
      sourceFileId, sourceFileName: name
    });
  }
  return files;
}

export function groupTeacherFiles(files: TeacherFile[]): TeacherActivity[] {
  const grouped = new Map<string, TeacherActivity>();
  for (const file of files) {
    const key = `${file.courseId}|${file.courseworkId}`;
    let activity = grouped.get(key);
    if (!activity) {
      activity = { key, courseId: file.courseId, courseworkId: file.courseworkId,
        courseName: file.courseName, courseSection: file.courseSection,
        courseworkTitle: file.courseworkTitle, courseworkDescription: file.courseworkDescription,
        creationTime: file.creationTime, files: [] };
      grouped.set(key, activity);
    }
    activity.files.push(file);
  }
  return [...grouped.values()];
}

export function filterAndSortTeacherActivities(files: TeacherFile[], courseId: string, sort: ActivitySort): TeacherActivity[] {
  const activities = groupTeacherFiles(files).filter(activity => !courseId || activity.courseId === courseId);
  const byName = (activity: TeacherActivity) => activity.courseworkTitle.toLocaleLowerCase('pt-BR');
  const byDate = (activity: TeacherActivity) => {
    const value = activity.creationTime ? Date.parse(activity.creationTime) : 0;
    return Number.isFinite(value) ? value : 0;
  };
  return activities.sort((a, b) => {
    if (sort === 'name-asc') return byName(a).localeCompare(byName(b), 'pt-BR') || a.key.localeCompare(b.key);
    if (sort === 'name-desc') return byName(b).localeCompare(byName(a), 'pt-BR') || a.key.localeCompare(b.key);
    return (sort === 'newest' ? byDate(b) - byDate(a) : byDate(a) - byDate(b)) || byName(a).localeCompare(byName(b), 'pt-BR');
  });
}

function loadGis(): Promise<void> {
  if (!gisPromise) {
    gisPromise = new Promise((resolve, reject) => {
      if ((window as any).google?.accounts?.oauth2) { resolve(); return; }
      const script = document.createElement('script');
      script.src = GIS_SCRIPT; script.async = true;
      script.onload = () => resolve(); script.onerror = () => reject(new Error('Falha ao carregar o Google Identity Services.'));
      document.head.appendChild(script);
    });
  }
  return gisPromise;
}

async function serverGet(path: string): Promise<any> {
  const url = settings.baseUrl + path.replace(/^\//, '');
  const response = await ServerConnection.makeRequest(url, { method: 'GET' }, settings);
  if (!response.ok) throw new Error(`Falha no Jupyter Server (${response.status}).`);
  return response.json();
}

async function serverPost(path: string, body: unknown): Promise<any> {
  const url = settings.baseUrl + path.replace(/^\//, '');
  const response = await ServerConnection.makeRequest(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  }, settings);
  if (!response.ok) {
    const raw = await response.text();
    let detail = '';
    try {
      const parsed = JSON.parse(raw);
      detail = String(parsed.reason || parsed.message || parsed.body || '').trim();
    } catch (_) {
      detail = raw.trim();
    }
    throw new Error(detail ? `Falha no Jupyter Server (${response.status}): ${detail}` : `Falha no Jupyter Server (${response.status}).`);
  }
  return response.json();
}

function googleError(operation: string, status: number): Error {
  console.warn('classroom-teacher: falha Google', { operation, status });
  if (status === 401) return new Error('Autorização expirada. Conecte-se novamente.');
  if (status === 403) return new Error(`Permissão insuficiente ao consultar ${operation}.`);
  if (status === 404) return new Error(`Recurso não encontrado ao consultar ${operation}.`);
  return new Error(`Falha HTTP ${status} ao consultar ${operation}.`);
}

async function googleGet(token: string, url: string, operation: string): Promise<any> {
  const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) throw googleError(operation, response.status);
  return response.json();
}

async function listAll(token: string, url: string, key: string, operation: string): Promise<any[]> {
  const result: any[] = [];
  let pageToken: string | undefined;
  do {
    const endpoint = new URL(url);
    endpoint.searchParams.set('pageSize', '100');
    if (pageToken) endpoint.searchParams.set('pageToken', pageToken);
    const data = await googleGet(token, endpoint.toString(), operation);
    result.push(...((data[key] || []) as any[]));
    pageToken = data.nextPageToken as string | undefined;
  } while (pageToken);
  return result;
}

async function collectFiles(token: string): Promise<TeacherFile[]> {
  const courses = await listAll(token, 'https://classroom.googleapis.com/v1/courses?teacherId=me&courseStates=ACTIVE', 'courses', 'turmas do Classroom');
  const files: TeacherFile[] = [];
  for (const course of courses) {
    const courseId = String(course.id || '');
    if (!courseId) continue;
    let work: any[] = [];
    try {
      work = await listAll(token, `https://classroom.googleapis.com/v1/courses/${encodeURIComponent(courseId)}/courseWork?courseWorkStates=PUBLISHED`, 'courseWork', 'atividades do Classroom');
    } catch (_) { continue; }
    for (const coursework of work) files.push(...parseTeacherCoursework(course, coursework));
  }
  return files;
}

function validateNotebook(data: any): any {
  if (!data || typeof data !== 'object' || !Number.isInteger(data.nbformat) || !Array.isArray(data.cells)) {
    throw new Error('O arquivo não é um notebook válido.');
  }
  return data;
}

async function metadata(token: string, fileId: string): Promise<DriveMetadata> {
  const data = await googleGet(token, `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(fileId)}?fields=id,name,mimeType,modifiedTime,version,md5Checksum,capabilities(canDownload,canEdit)`, 'metadados do Drive');
  return { id: String(data.id || fileId), name: String(data.name || ''), mimeType: String(data.mimeType || ''),
    modifiedTime: typeof data.modifiedTime === 'string' ? data.modifiedTime : null,
    version: typeof data.version === 'string' ? data.version : null,
    md5Checksum: typeof data.md5Checksum === 'string' ? data.md5Checksum : null,
    canDownload: !!data.capabilities?.canDownload, canEdit: !!data.capabilities?.canEdit };
}

function changed(baseline: Baseline, current: DriveMetadata): boolean {
  return (baseline.version !== null && current.version !== null && baseline.version !== current.version) ||
    (baseline.modifiedTime !== null && current.modifiedTime !== null && baseline.modifiedTime !== current.modifiedTime) ||
    (baseline.md5Checksum !== null && current.md5Checksum !== null && baseline.md5Checksum !== current.md5Checksum);
}

async function copySourceFile(token: string, sourceFileId: string, name: string): Promise<DriveMetadata> {
  const response = await fetch(`https://www.googleapis.com/drive/v3/files/${encodeURIComponent(sourceFileId)}/copy?fields=id,name,modifiedTime,version,md5Checksum,capabilities(canDownload,canEdit)`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ name })
  });
  if (!response.ok) throw googleError('cópia do arquivo original', response.status);
  const data = await response.json();
  if (!data.id || data.id === sourceFileId) throw new Error('A cópia do Drive não gerou um destino distinto.');
  return { id: String(data.id), name: String(data.name || name), mimeType: '', modifiedTime: data.modifiedTime || null,
    version: data.version || null, md5Checksum: data.md5Checksum || null,
    canDownload: !!data.capabilities?.canDownload, canEdit: !!data.capabilities?.canEdit };
}

/** Única função de upload: o destino é obrigatório e nunca pode ser o original. */
export async function uploadTargetContent(token: string, sourceFileId: string, targetFileId: string, notebook: any): Promise<void> {
  if (!targetFileId || sourceFileId === targetFileId) throw new Error('O arquivo original é somente leitura.');
  const response = await fetch(`https://www.googleapis.com/upload/drive/v3/files/${encodeURIComponent(targetFileId)}?uploadType=media`, {
    method: 'PATCH', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(notebook)
  });
  if (!response.ok) throw googleError('atualização da cópia no Drive', response.status);
}

async function saveTargetIfOpen(documentManager: IDocumentManager, path: string): Promise<void> {
  const widget = documentManager.findWidget(path);
  if (!widget) return;
  const context = documentManager.contextForWidget(widget);
  if (!context || (context.path !== path && context.localPath !== path)) throw new Error('Documento Classroom inválido.');
  await context.ready;
  if (context.model.dirty) await context.save();
}

async function getAssociation(file: TeacherFile): Promise<TeacherAssociation> {
  return serverGet(`/google-classroom-teacher/api/association?course_id=${encodeURIComponent(file.courseId)}&coursework_id=${encodeURIComponent(file.courseworkId)}&source_file_id=${encodeURIComponent(file.sourceFileId)}`);
}

async function existingAssociation(file: TeacherFile): Promise<TeacherAssociation | null> {
  try {
    return await getAssociation(file);
  } catch (error) {
    if (error instanceof Error && error.message === 'Falha no Jupyter Server (404).') return null;
    throw error;
  }
}

function applyAssociation(file: TeacherFile, association: TeacherAssociation): void {
  file.localPath = association.local_path;
  file.targetFileId = association.target_file_id;
  file.targetFileName = association.target_file_name;
  file.baseline = { modifiedTime: association.modified_time, version: association.version, md5Checksum: association.md5_checksum };
}

async function openSource(app: JupyterFrontEnd, token: string, file: TeacherFile, term: string): Promise<TeacherAssociation> {
  const existing = await existingAssociation(file);
  if (existing) {
    try {
      await app.serviceManager.contents.get(existing.local_path, { content: false });
      applyAssociation(file, existing);
      await app.commands.execute('docmanager:open', { path: existing.local_path });
      return existing;
    } catch (error) {
      if (!(error instanceof Error && error.message.includes('404'))) throw error;
      if (existing.target_file_id) throw new Error('A cópia local desapareceu, mas já existe uma cópia de destino; restauração bloqueada.');
    }
  }
  const source = await metadata(token, file.sourceFileId);
  const downloaded = await fetch(`https://www.googleapis.com/drive/v3/files/${encodeURIComponent(file.sourceFileId)}?alt=media`, { headers: { Authorization: `Bearer ${token}` } });
  if (!downloaded.ok) throw googleError('download do arquivo original', downloaded.status);
  const notebook = validateNotebook(await downloaded.json());
  const saved = await serverPost('/google-classroom-teacher/api/save', {
    term, course_id: file.courseId, course_name: file.courseName, coursework_id: file.courseworkId,
    course_section: file.courseSection,
    coursework_title: file.courseworkTitle, coursework_description: file.courseworkDescription,
    creation_time: file.creationTime, source_file_id: file.sourceFileId, source_file_name: source.name || file.sourceFileName,
    modified_time: source.modifiedTime, version: source.version, md5_checksum: source.md5Checksum, can_edit: false, notebook
  });
  applyAssociation(file, saved.association);
  await app.commands.execute('docmanager:open', { path: file.localPath });
  return saved.association;
}

async function synchronize(app: JupyterFrontEnd, documentManager: IDocumentManager, token: string, file: TeacherFile): Promise<TeacherAssociation> {
  if (!file.localPath) throw new Error('Abra o notebook antes de sincronizar.');
  let association = await getAssociation(file);
  if (association.source_file_id === association.target_file_id) throw new Error('O arquivo original é somente leitura.');
  await saveTargetIfOpen(documentManager, file.localPath);
  const contents = await app.serviceManager.contents.get(file.localPath, { content: true });
  const notebook = validateNotebook(contents.content);
  let targetId = association.target_file_id;
  let current: DriveMetadata;
  if (!targetId) {
    association = await serverPost('/google-classroom-teacher/api/association', {
      course_id: file.courseId, coursework_id: file.courseworkId, source_file_id: file.sourceFileId,
      course_section: file.courseSection
    });
    const copied = await copySourceFile(token, file.sourceFileId, association.target_file_name || file.sourceFileName);
    targetId = copied.id;
    if (targetId === file.sourceFileId) throw new Error('O arquivo original é somente leitura.');
    // A resposta de files.copy pode não incluir capabilities; consultar o destino
    // antes de persistir o baseline garante que canEdit seja efetivo.
    current = await metadata(token, targetId);
    association = await serverPost('/google-classroom-teacher/api/association', {
      course_id: file.courseId, coursework_id: file.courseworkId, source_file_id: file.sourceFileId,
      target_file_id: targetId, target_file_name: current.name, modified_time: current.modifiedTime,
      version: current.version, md5_checksum: current.md5Checksum, can_edit: current.canEdit
    });
  } else {
    current = await metadata(token, targetId);
    if (changed({ modifiedTime: association.modified_time, version: association.version, md5Checksum: association.md5_checksum }, current)) {
      throw new Error('O arquivo de destino no Drive foi alterado desde a última sincronização.');
    }
  }
  if (!current.canEdit) throw new Error('A cópia de destino não pode ser editada.');
  await uploadTargetContent(token, file.sourceFileId, targetId, notebook);
  const updated = await metadata(token, targetId);
  association = await serverPost('/google-classroom-teacher/api/association', {
    course_id: file.courseId, coursework_id: file.courseworkId, source_file_id: file.sourceFileId,
    target_file_id: targetId, target_file_name: updated.name, modified_time: updated.modifiedTime,
    version: updated.version, md5_checksum: updated.md5Checksum, can_edit: updated.canEdit
  });
  return association;
}

class TeacherPanel extends Widget {
  private root: HTMLElement; private app: JupyterFrontEnd; private documentManager: IDocumentManager;
  private clientId: string | null = null; private term: string | null = null; private files: TeacherFile[] = [];
  private courseFilter = ''; private activitySort: ActivitySort = 'newest';
  private error: string | null = null; private info: string | null = null; private busy = false;
  constructor(app: JupyterFrontEnd, documentManager: IDocumentManager) {
    super(); this.app = app; this.documentManager = documentManager; this.id = PANEL_ID;
    this.title.label = 'Google Classroom — Professor'; this.title.closable = true;
    this.root = document.createElement('div'); this.root.className = 'google-classroom-teacher-panel'; this.node.appendChild(this.root);
  }
  async start(): Promise<void> {
    try { const config = await serverGet('/google-classroom-teacher/api/config'); this.clientId = config.client_id || null; this.term = config.term || null; if (this.clientId) await loadGis(); this.render(); }
    catch (_) { this.error = 'Falha ao carregar a configuração do Classroom para professores.'; this.render(); }
  }
  private connected(): boolean { return !!accessToken && Date.now() < expiresAt; }
  private render(): void {
    this.root.textContent = '';
    Object.assign(this.root.style, { display: 'flex', flexDirection: 'column', minHeight: '0', height: '100%' });
    const header = document.createElement('div');
    Object.assign(header.style, { flex: '0 0 auto' });
    const title = document.createElement('h2'); title.textContent = 'Google Classroom — Professor'; header.appendChild(title);
    this.root.appendChild(header);
    const list = document.createElement('div');
    Object.assign(list.style, { flex: '1 1 auto', minHeight: '0', overflowY: 'auto' });
    if (!this.term) { const p = document.createElement('p'); p.textContent = 'Período do professor não configurado.'; header.appendChild(p); return; }
    if (!this.clientId) { const p = document.createElement('p'); p.textContent = 'Integração ainda não configurada pelo administrador.'; header.appendChild(p); return; }
    if (!this.connected()) { const button = document.createElement('button'); button.textContent = 'Conectar ao Google'; button.onclick = () => void this.connect(); header.appendChild(button); if (this.error) this.addTo(header, this.error, true); return; }
    const refresh = document.createElement('button'); refresh.textContent = 'Atualizar'; refresh.onclick = () => void this.refresh();
    header.appendChild(refresh);
    const controls = document.createElement('div'); controls.className = 'google-classroom-teacher-controls';
    Object.assign(controls.style, { display: 'flex', flexWrap: 'wrap', gap: '6px', margin: '8px 0' });
    const courseLabel = document.createElement('label'); courseLabel.textContent = 'Turma: ';
    const courseSelect = document.createElement('select'); courseSelect.appendChild(new Option('Todas as turmas', ''));
    const courses = [...new Map(this.files.map(file => [file.courseId, file])).values()]
      .sort((a, b) => a.courseName.localeCompare(b.courseName, 'pt-BR'));
    if (this.courseFilter && !courses.some(course => course.courseId === this.courseFilter)) this.courseFilter = '';
    for (const course of courses) courseSelect.appendChild(new Option(`${course.courseName}${course.courseSection ? ` — ${course.courseSection}` : ''}`, course.courseId));
    courseSelect.value = this.courseFilter; courseSelect.onchange = () => { this.courseFilter = courseSelect.value; this.render(); };
    courseLabel.appendChild(courseSelect); controls.appendChild(courseLabel);
    const sortLabel = document.createElement('label'); sortLabel.textContent = ' Ordenar: ';
    const sortSelect = document.createElement('select');
    for (const [value, label] of [['newest', 'Mais recente'], ['oldest', 'Mais antiga'], ['name-asc', 'Nome A→Z'], ['name-desc', 'Nome Z→A']] as const) sortSelect.appendChild(new Option(label, value));
    sortSelect.value = this.activitySort; sortSelect.onchange = () => { this.activitySort = sortSelect.value as ActivitySort; this.render(); };
    sortLabel.appendChild(sortSelect); controls.appendChild(sortLabel); header.appendChild(controls);
    if (this.info) this.addTo(header, this.info, false); if (this.error) this.addTo(header, this.error, true);
    if (this.busy) { this.addTo(header, 'Carregando atividades...', false); return; }
    for (const activity of filterAndSortTeacherActivities(this.files, this.courseFilter, this.activitySort)) {
      const item = document.createElement('section'); item.className = 'google-classroom-teacher-activity';
      Object.assign(item.style, { border: '1px solid var(--jp-border-color2)', borderRadius: '4px', margin: '8px 4px', padding: '8px' });
      const heading = document.createElement('div'); heading.className = 'google-classroom-teacher-activity-heading';
      const title = document.createElement('h3'); title.textContent = activity.courseworkTitle; heading.appendChild(title);
      if (!this.courseFilter) { const course = document.createElement('small'); course.textContent = `${activity.courseName}${activity.courseSection ? ` — ${activity.courseSection}` : ''}`; heading.appendChild(course); }
      if (activity.creationTime) { const date = document.createElement('time'); date.dateTime = activity.creationTime; date.textContent = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short' }).format(new Date(activity.creationTime)); Object.assign(date.style, { color: 'var(--jp-ui-font-color2)', fontSize: 'var(--jp-ui-font-size0)', marginLeft: '8px' }); heading.appendChild(date); }
      item.appendChild(heading);
      for (const file of activity.files) {
        const notebook = document.createElement('div'); notebook.className = 'google-classroom-teacher-notebook';
        Object.assign(notebook.style, { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px', borderTop: '1px solid var(--jp-border-color2)', padding: '8px 0 0', marginTop: '8px' });
        const name = document.createElement('strong'); name.textContent = file.sourceFileName; notebook.appendChild(name);
        const open = document.createElement('button'); open.textContent = 'Abrir'; open.onclick = () => void this.open(file); notebook.appendChild(open);
        if (file.localPath) { const sync = document.createElement('button'); sync.textContent = file.state === 'syncing' ? 'Sincronizando…' : 'Sincronizar com Drive'; sync.disabled = file.state === 'syncing'; sync.onclick = () => void this.sync(file); notebook.appendChild(sync); }
        if (file.targetFileName) { const dest = document.createElement('small'); dest.textContent = `Destino: ${file.targetFileName}`; notebook.appendChild(dest); }
        if (file.state === 'synced') this.addTo(notebook, 'Sincronizado com Drive', false);
        if (file.state === 'conflict' || file.state === 'error') this.addTo(notebook, file.message || 'Erro ao sincronizar.', true);
        item.appendChild(notebook);
      }
      list.appendChild(item);
    }
    this.root.appendChild(list);
  }
  private addTo(parent: HTMLElement, text: string, error: boolean): void { const p = document.createElement('p'); p.textContent = text; p.className = error ? 'google-classroom-teacher-error' : 'google-classroom-teacher-info'; parent.appendChild(p); }
  private addMessage(text: string, error: boolean): void { this.addTo(this.root, text, error); }
  private async connect(): Promise<void> {
    if (!this.clientId) return; this.error = null; this.render(); await loadGis();
    tokenClient = tokenClient || window.google.accounts.oauth2.initTokenClient({ client_id: this.clientId, scope: SCOPES.join(' '), callback: response => this.onToken(response), error_callback: () => { this.error = 'Autorização cancelada ou falhou.'; this.render(); } });
    tokenClient.requestAccessToken({ scope: SCOPES.join(' '), include_granted_scopes: true, prompt: '' });
  }
  private onToken(response: TeacherTokenResponse): void { if (!response.access_token) { this.error = 'Não foi possível obter autorização Google.'; this.render(); return; } accessToken = response.access_token; expiresAt = Date.now() + response.expires_in * 1000; void this.refresh(); }
  private async refresh(): Promise<void> { if (!accessToken) return; this.busy = true; this.error = null; this.render(); try { this.files = await collectFiles(accessToken); for (const file of this.files) { try { const a = await getAssociation(file); file.localPath = a.local_path; file.targetFileId = a.target_file_id; file.targetFileName = a.target_file_name; file.baseline = { modifiedTime: a.modified_time, version: a.version, md5Checksum: a.md5_checksum }; } catch (_) {} } this.info = 'Consulta ao Classroom concluída.'; } catch (e) { this.error = e instanceof Error ? e.message : 'Falha ao consultar o Classroom.'; } finally { this.busy = false; this.render(); } }
  private async open(file: TeacherFile): Promise<void> { if (!accessToken || !this.term) return; this.busy = true; this.render(); try { await openSource(this.app, accessToken, file, this.term); this.info = 'Arquivo original aberto como cópia local somente leitura na origem.'; } catch (e) { this.error = e instanceof Error ? e.message : 'Falha ao abrir o notebook.'; } finally { this.busy = false; this.render(); } }
  private async sync(file: TeacherFile): Promise<void> { if (file.state === 'syncing' || !accessToken) return; file.state = 'syncing'; file.message = undefined; this.render(); try { const association = await synchronize(this.app, this.documentManager, accessToken, file); file.targetFileId = association.target_file_id; file.targetFileName = association.target_file_name; file.baseline = { modifiedTime: association.modified_time, version: association.version, md5Checksum: association.md5_checksum }; file.state = 'synced'; } catch (e) { file.state = e instanceof Error && e.message.includes('alterado') ? 'conflict' : 'error'; file.message = e instanceof Error ? e.message : 'Falha ao sincronizar.'; } finally { this.render(); } }
}

const plugin: JupyterFrontEndPlugin<void> = {
  id: PLUGIN_ID, description: 'Google Classroom — Professor (0.1.5)', autoStart: true,
  requires: [ICommandPalette, IMainMenu, IDocumentManager],
  activate: (app, palette, mainMenu, documentManager) => {
    let panel: TeacherPanel | null = null;
    const ensurePanel = () => { if (!panel || panel.isDisposed) panel = new TeacherPanel(app, documentManager); if (!panel.isAttached) app.shell.add(panel, 'right'); return panel; };
    app.commands.addCommand(COMMAND_ID, { label: 'Google Classroom — Professor', caption: 'Abrir o painel do Google Classroom para professores.', execute: async () => { await app.started; const current = ensurePanel(); app.shell.activateById(current.id); await current.start(); } });
    palette.addItem({ command: COMMAND_ID, category: 'Google Classroom' });
    const menu = new Menu({ commands: app.commands }); menu.title.label = 'Google'; menu.addItem({ command: COMMAND_ID }); mainMenu.addMenu(menu, true, { rank: 901 });
    void (async () => { await app.started; const current = ensurePanel(); app.shell.activateById(current.id); await current.start(); })();
  }
};

export default plugin;
