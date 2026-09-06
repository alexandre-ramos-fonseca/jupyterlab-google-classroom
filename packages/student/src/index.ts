import { JupyterFrontEnd, JupyterFrontEndPlugin } from '@jupyterlab/application';
import { Dialog, ICommandPalette, showDialog } from '@jupyterlab/apputils';
import { IDocumentManager } from '@jupyterlab/docmanager';
import { IMainMenu } from '@jupyterlab/mainmenu';
import { ServerConnection } from '@jupyterlab/services';
import { Menu, Widget } from '@lumino/widgets';

const PLUGIN_ID = 'jupyterlab-google-classroom-student:plugin';
const COMMAND_ID = 'jupyterlab-google-classroom-student:open';
const PANEL_ID = 'google-classroom-student-panel';
const GIS_SCRIPT_SRC = 'https://accounts.google.com/gsi/client';

const REQUIRED_SCOPES = [
  'https://www.googleapis.com/auth/classroom.courses.readonly',
  'https://www.googleapis.com/auth/classroom.student-submissions.me.readonly',
  'https://www.googleapis.com/auth/drive'
];
const OPTIONAL_SCOPES = [
  'https://www.googleapis.com/auth/classroom.coursework.me'
];
const SCOPES = [...REQUIRED_SCOPES, ...OPTIONAL_SCOPES];
const SCOPE_STR = SCOPES.join(' ');
const SCOPE_LABELS: Record<string, string> = {
  'https://www.googleapis.com/auth/classroom.courses.readonly': 'cursos do Classroom',
  'https://www.googleapis.com/auth/classroom.student-submissions.me.readonly':
    'atividades e submissões próprias',
  'https://www.googleapis.com/auth/classroom.coursework.me':
    'títulos das atividades próprias',
  'https://www.googleapis.com/auth/drive': 'arquivos do Google Drive'
};

/** Interpreta somente a lista pública de escopos retornada pelo Google. */
export function parseGrantedScopes(scope: unknown): string[] {
  if (typeof scope !== 'string') {
    return [];
  }
  return [...new Set(scope.trim().split(/\s+/).filter(Boolean))];
}

export function classifyGrantedScopes(grantedScopes: string[]): {
  missingRequired: string[];
  missingOptional: string[];
} {
  const granted = new Set(grantedScopes);
  return {
    missingRequired: REQUIRED_SCOPES.filter(scope => !granted.has(scope)),
    missingOptional: OPTIONAL_SCOPES.filter(scope => !granted.has(scope))
  };
}

// Estado do token — APENAS em memória no navegador. Nunca persistido.
let accessToken: string | null = null;
let expiresAt = 0;
let tokenClient: GsiTokenClient | null = null;
let gisPromise: Promise<void> | null = null;

const settings = ServerConnection.makeSettings();

interface ClassroomFile {
  fileId: string;
  name: string;
  courseId: string;
  courseName: string;
  courseSection: string;
  courseworkId: string;
  courseworkTitle: string;
  creationTime: string | null;
  submissionId: string;
  submissionState: string;
  localPath?: string;
  baseline?: DriveBaseline;
  canEdit?: boolean;
  syncState?: 'syncing' | 'synced' | 'conflict' | 'error';
  syncMessage?: string;
  lastSyncedAt?: string;
}

export interface ClassroomActivity {
  key: string;
  courseId: string;
  courseName: string;
  courseSection: string;
  courseworkId: string;
  courseworkTitle: string;
  creationTime: string | null;
  files: ClassroomFile[];
}

export type ActivitySort = 'newest' | 'oldest' | 'name-asc' | 'name-desc';

export function groupClassroomFiles(files: ClassroomFile[]): ClassroomActivity[] {
  const grouped = new Map<string, ClassroomActivity>();
  for (const file of files) {
    const key = `${file.courseId}|${file.courseworkId}`;
    let activity = grouped.get(key);
    if (!activity) {
      activity = {
        key,
        courseId: file.courseId,
        courseName: file.courseName,
        courseSection: file.courseSection,
        courseworkId: file.courseworkId,
        courseworkTitle: file.courseworkTitle,
        creationTime: file.creationTime,
        files: []
      };
      grouped.set(key, activity);
    }
    activity.files.push(file);
  }
  return [...grouped.values()];
}

export function filterAndSortClassroomActivities(
  files: ClassroomFile[],
  courseId: string,
  sort: ActivitySort
): ClassroomActivity[] {
  const activities = groupClassroomFiles(files).filter(
    activity => !courseId || activity.courseId === courseId
  );
  const byName = (activity: ClassroomActivity) =>
    activity.courseworkTitle.toLocaleLowerCase('pt-BR');
  const byDate = (activity: ClassroomActivity) => {
    const value = activity.creationTime ? Date.parse(activity.creationTime) : 0;
    return Number.isNaN(value) ? 0 : value;
  };
  return activities.sort((a, b) => {
    if (sort === 'name-asc') {
      return byName(a).localeCompare(byName(b), 'pt-BR') || a.key.localeCompare(b.key);
    }
    if (sort === 'name-desc') {
      return byName(b).localeCompare(byName(a), 'pt-BR') || a.key.localeCompare(b.key);
    }
    return (sort === 'newest' ? byDate(b) - byDate(a) : byDate(a) - byDate(b)) ||
      byName(a).localeCompare(byName(b), 'pt-BR') || a.key.localeCompare(b.key);
  });
}

interface DriveBaseline {
  modifiedTime: string | null;
  version: string | null;
  md5Checksum: string | null;
}

interface DriveMetadata extends DriveBaseline {
  id: string;
  name: string;
  mimeType: string;
  canDownload: boolean;
  canEdit: boolean;
}

interface LocalAssociation {
  file_id: string;
  path: string;
  modified_time: string | null;
  version: string | null;
  md5_checksum: string | null;
  can_edit: boolean | null;
}

interface CollectionResult {
  files: ClassroomFile[];
  warning: string | null;
}

class GoogleApiError extends Error {
  readonly status: number;

  constructor(operation: string, status: number) {
    super(googleHttpMessage(operation, status));
    this.name = 'GoogleApiError';
    this.status = status;
  }
}

class NotebookOpenError extends Error {
  constructor(relativePath: string) {
    super(`Notebook salvo em ${relativePath}, mas não foi possível abri-lo.`);
    this.name = 'NotebookOpenError';
  }
}

class DriveConflictError extends Error {
  readonly current: DriveMetadata;
  readonly localNotebook: any;

  constructor(current: DriveMetadata, localNotebook: any) {
    super('O arquivo no Google Drive foi alterado desde a última sincronização. A sincronização foi bloqueada para evitar perda de alterações.');
    this.name = 'DriveConflictError';
    this.current = current;
    this.localNotebook = localNotebook;
  }
}

/** Carrega o Google Identity Services uma única vez. */
function loadGis(): Promise<void> {
  if (!gisPromise) {
    gisPromise = new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[src="${GIS_SCRIPT_SRC}"]`);
      if (existing && (window as any).google?.accounts?.oauth2) {
        resolve();
        return;
      }
      if ((window as any).google?.accounts?.oauth2) {
        resolve();
        return;
      }
      const script = document.createElement('script');
      script.src = GIS_SCRIPT_SRC;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error('Falha ao carregar o Google Identity Services.'));
      document.head.appendChild(script);
    });
  }
  return gisPromise;
}

async function serverGet(path: string): Promise<any> {
  const url = settings.baseUrl + path.replace(/^\//, '');
  const response = await ServerConnection.makeRequest(url, { method: 'GET' }, settings);
  if (!response.ok) {
    throw new Error('Falha ao consultar o Jupyter Server (' + response.status + ').');
  }
  return response.json();
}

async function serverPost(path: string, body: unknown): Promise<any> {
  const url = settings.baseUrl + path.replace(/^\//, '');
  const response = await ServerConnection.makeRequest(
    url,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    },
    settings
  );
  if (!response.ok) {
    let message = 'Falha ao salvar o notebook (' + response.status + ').';
    try {
      const data = await response.json();
      if (data && typeof data.error === 'string') {
        message = data.error;
      }
    } catch (_) {
      /* sem corpo legível */
    }
    throw new Error(message);
  }
  return response.json();
}

function googleHttpMessage(operation: string, status: number): string {
  if (status === 401) {
    return 'Autorização expirada. Conecte-se novamente.';
  }
  if (status === 403) {
    if (operation.includes('editar')) {
      return 'Não foi possível editar este arquivo no Google Drive.';
    }
    return `Permissão insuficiente ou API/controle administrativo bloqueado ao consultar ${operation}.`;
  }
  if (status === 404) {
    return `Recurso não encontrado ao consultar ${operation}.`;
  }
  return `Falha HTTP ${status} ao consultar ${operation}.`;
}

function recordGoogleFailure(operation: string, status: number): GoogleApiError {
  console.warn('google-classroom-student: falha na API Google', { operation, status });
  return new GoogleApiError(operation, status);
}

async function googleGet(token: string, url: string, operation: string): Promise<any> {
  const res = await fetch(url, { headers: { Authorization: 'Bearer ' + token } });
  if (!res.ok) {
    throw recordGoogleFailure(operation, res.status);
  }
  return res.json();
}

async function getDriveMetadata(token: string, fileId: string): Promise<DriveMetadata> {
  const data = await googleGet(
    token,
    `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(fileId)}` +
      '?fields=id,name,mimeType,modifiedTime,version,md5Checksum,capabilities(canDownload,canEdit)&supportsAllDrives=true',
    'metadados do arquivo no Google Drive'
  );
  return {
    id: String(data.id || fileId),
    name: String(data.name || ''),
    mimeType: String(data.mimeType || ''),
    modifiedTime: typeof data.modifiedTime === 'string' ? data.modifiedTime : null,
    version: typeof data.version === 'string' ? data.version : null,
    md5Checksum: typeof data.md5Checksum === 'string' ? data.md5Checksum : null,
    canDownload: !!data.capabilities?.canDownload,
    canEdit: !!data.capabilities?.canEdit
  };
}

async function uploadDriveContent(
  token: string,
  fileId: string,
  notebook: any
): Promise<void> {
  const url =
    `https://www.googleapis.com/upload/drive/v3/files/${encodeURIComponent(fileId)}` +
    '?uploadType=media&supportsAllDrives=true';
  const response = await fetch(url, {
    method: 'PATCH',
    headers: {
      Authorization: 'Bearer ' + token,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(notebook)
  });
  if (!response.ok) {
    throw recordGoogleFailure('editar arquivo no Google Drive', response.status);
  }
}

async function downloadDriveNotebook(token: string, fileId: string): Promise<any> {
  const response = await fetch(
    `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(fileId)}?alt=media&supportsAllDrives=true`,
    { headers: { Authorization: 'Bearer ' + token } }
  );
  if (!response.ok) {
    throw recordGoogleFailure('arquivos do Google Drive', response.status);
  }
  return validateNotebook(await response.text());
}

function baselineFromAssociation(record: LocalAssociation): DriveBaseline {
  return {
    modifiedTime: record.modified_time,
    version: record.version,
    md5Checksum: record.md5_checksum
  };
}

function remoteChanged(baseline: DriveBaseline, current: DriveMetadata): boolean {
  return (
    (baseline.version !== null && current.version !== null && baseline.version !== current.version) ||
    (baseline.modifiedTime !== null &&
      current.modifiedTime !== null &&
      baseline.modifiedTime !== current.modifiedTime) ||
    (baseline.md5Checksum !== null &&
      current.md5Checksum !== null &&
      baseline.md5Checksum !== current.md5Checksum)
  );
}

async function hydrateAssociations(files: ClassroomFile[]): Promise<void> {
  await Promise.all(
    files.map(async file => {
      try {
        const record = (await serverGet(
          `/google-classroom-student/api/association?file_id=${encodeURIComponent(file.fileId)}`
        )) as LocalAssociation;
        if (record.path && record.file_id === file.fileId) {
          file.localPath = record.path;
          file.baseline = baselineFromAssociation(record);
          file.canEdit = record.can_edit == null ? undefined : !!record.can_edit;
        }
      } catch (_) {
        // O arquivo ainda não foi importado nesta sessão/localidade.
      }
    })
  );
}

async function listAll<T>(
  token: string,
  baseUrl: string,
  key: string,
  operation: string
): Promise<T[]> {
  const out: T[] = [];
  let pageToken: string | undefined;
  for (;;) {
    const url = new URL(baseUrl);
    url.searchParams.set('pageSize', '100');

    if (pageToken) {
      url.searchParams.set('pageToken', pageToken);
    } else {
      url.searchParams.delete('pageToken');
    }
    const data = await googleGet(token, url.toString(), operation);
    const items = (data[key] || []) as T[];
    out.push(...items);
    pageToken = data.nextPageToken as string | undefined;
    if (!pageToken) {
      break;
    }
  }
  return out;
}

async function collectClassroomFiles(
  token: string,
  courseworkScopeGranted: boolean
): Promise<CollectionResult> {
  const courses: any[] = await listAll(
    token,
    'https://classroom.googleapis.com/v1/courses?courseStates=ACTIVE',
    'courses',
    'cursos do Classroom'
  );
  const files: ClassroomFile[] = [];
  const seen = new Set<string>();
  const failures: GoogleApiError[] = [];
  let successfulCourses = 0;

  for (const course of courses) {
    const courseId = String(course.id || '');
    if (!courseId) {
      continue;
    }
    const courseName = String(course.name || '(curso sem nome)');

    let courseFailed = false;
    let submissions: any[] = [];
    try {
      submissions = await listAll(
        token,
        `https://classroom.googleapis.com/v1/courses/${encodeURIComponent(courseId)}/courseWork/-/studentSubmissions?userId=me`,
        'studentSubmissions',
        'submissões próprias do Classroom'
      );
    } catch (error) {
      courseFailed = true;
      if (error instanceof GoogleApiError) {
        failures.push(error);
      }
    }

    if (!courseFailed) {
      successfulCourses += 1;
    }

    const courseworkDetails = new Map<string, { title: string; creationTime: string | null }>();
    const courseworkIds = [
      ...new Set(
        submissions
          .map(sub => String(sub.courseWorkId || ''))
          .filter(Boolean)
      )
    ];
    if (courseworkScopeGranted) {
      await Promise.all(
        courseworkIds.map(async courseworkId => {
        try {
          const coursework = await googleGet(
            token,
            `https://classroom.googleapis.com/v1/courses/${encodeURIComponent(courseId)}/courseWork/${encodeURIComponent(courseworkId)}`,
            'título da atividade do Classroom'
          );
          const title = String(coursework.title || '').trim();
          courseworkDetails.set(courseworkId, {
            title: title || 'Atividade do Google Classroom',
            creationTime: typeof coursework.creationTime === 'string' ? coursework.creationTime : null
          });
        } catch (error) {
          if (error instanceof GoogleApiError) {
            failures.push(error);
          }
        }
        })
      );
    }

    for (const sub of submissions) {
      const submissionCourseId = String(sub.courseId || courseId);
      const submissionId = String(sub.id || '');
      const courseworkId = String(sub.courseWorkId || '');
      if (!courseworkId) {
        continue;
      }
      const coursework = courseworkDetails.get(courseworkId);
      const courseworkTitle = coursework?.title || 'Atividade do Google Classroom';
      const assign = sub.assignmentSubmission;
      const attachments = assign && Array.isArray(assign.attachments) ? assign.attachments : [];
      for (const att of attachments) {
        const drive = att && att.driveFile;
        if (!drive || !drive.id) {
          continue;
        }
        const name = String(att.title || drive.title || '').trim();
        if (!name.toLowerCase().endsWith('.ipynb')) {
          continue;
        }
        const fileId = String(drive.id);
        if (seen.has(fileId)) {
          continue;
        }
        seen.add(fileId);
        files.push({
          fileId,
          name,
          courseId: submissionCourseId,
          courseName,
          courseSection: String(course.section || '').trim(),
          courseworkId,
          courseworkTitle,
          creationTime: coursework?.creationTime || null,
          submissionId,
          submissionState: String(sub.state || '')
        });
      }
    }
  }

  files.sort(
    (a, b) =>
      a.courseName.localeCompare(b.courseName) ||
      a.courseworkTitle.localeCompare(b.courseworkTitle) ||
      a.name.localeCompare(b.name)
  );
  if (failures.length && successfulCourses === 0) {
    throw failures[0];
  }
  const warning = [
    !courseworkScopeGranted
      ? 'Títulos reais indisponíveis sem a permissão opcional do Classroom; usando “Atividade do Google Classroom”.'
      : null,
    failures.length
      ? 'Algumas consultas ao Classroom falharam; os resultados podem estar incompletos.'
      : null
  ].filter(Boolean).join(' ') || null;
  return { files, warning };
}

function validateNotebook(text: string): any {
  let data: any;
  try {
    data = JSON.parse(text);
  } catch (_) {
    throw new Error('O arquivo baixado não é um JSON válido.');
  }
  if (data === null || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error('JSON inválido: a raiz não é um objeto.');
  }
  if (!Number.isInteger(data.nbformat)) {
    throw new Error('nbformat ausente ou inválido.');
  }
  if (!Array.isArray(data.cells)) {
    throw new Error('cells ausente; não é um notebook válido.');
  }
  return data;
}

function shortError(error: unknown): string {
  const message =
    error instanceof Error ? error.message : 'Erro desconhecido no Google Classroom.';
  return message.slice(0, 160);
}

function authErrorMessage(error: GsiTokenError): string {
  const code = `${error.error || ''} ${error.type || ''}`.toLowerCase();
  if (code.includes('access_denied') || code.includes('cancel') || code.includes('closed')) {
    return 'Autorização cancelada. Você pode tentar novamente.';
  }
  if (code.includes('popup_failed')) {
    return 'Não foi possível abrir a janela de autorização Google.';
  }
  return 'Falha na autorização Google. Tente novamente.';
}

async function openNotebook(
  app: JupyterFrontEnd,
  token: string,
  file: ClassroomFile
): Promise<{ path: string; metadata: DriveMetadata }> {
  const meta = await getDriveMetadata(token, file.fileId);
  const name = meta.name || file.name;
  const mime = meta.mimeType;
  const canDownload = meta.canDownload;

  if (!name.toLowerCase().endsWith('.ipynb') && mime !== 'application/vnd.google.colaboratory') {
    throw new Error('Arquivo não é um notebook aceito (.ipynb/Colab).');
  }
  if (!canDownload) {
    throw new Error('Arquivo sem permissão de download (canDownload=false).');
  }

  const notebook = await downloadDriveNotebook(token, file.fileId);

  const resp = await serverPost('/google-classroom-student/api/save', {
    file_id: file.fileId,
    file_name: name,
    course_id: file.courseId,
    course_name: file.courseName,
    coursework_id: file.courseworkId,
    coursework_title: file.courseworkTitle,
    submission_id: file.submissionId,
    submission_state: file.submissionState,
    modified_time: meta.modifiedTime,
    version: meta.version,
    md5_checksum: meta.md5Checksum,
    can_edit: meta.canEdit,
    notebook
  });

  const relativePath =
    typeof resp.path === 'string' && resp.path.startsWith('classroom/')
      ? resp.path
      : 'classroom/<caminho não disponível>';
  try {
    await app.commands.execute('docmanager:open', { path: relativePath });
  } catch (_) {
    throw new NotebookOpenError(relativePath);
  }
  return { path: relativePath, metadata: meta };
}

async function localNotebookExists(app: JupyterFrontEnd, path: string): Promise<boolean> {
  try {
    await app.serviceManager.contents.get(path);
    return true;
  } catch (_) {
    return false;
  }
}

async function openLocalNotebook(app: JupyterFrontEnd, path: string): Promise<void> {
  try {
    await app.commands.execute('docmanager:open', { path });
  } catch (_) {
    throw new NotebookOpenError(path);
  }
}

/** Salva somente o documento Classroom aberto para o caminho canônico informado. */
async function saveTargetIfOpen(
  documentManager: IDocumentManager,
  path: string
): Promise<void> {
  const widget = documentManager.findWidget(path);
  if (!widget) {
    // Documento fechado: o ContentsManager já representa a versão persistida.
    return;
  }

  const context = documentManager.contextForWidget(widget);
  if (!context || (context.path !== path && context.localPath !== path)) {
    throw new Error('Não foi possível localizar o notebook Classroom associado.');
  }

  await context.ready;
  if (context.model.dirty) {
    await context.save();
  }
}

function backupName(localPath: string, origin: 'drive' | 'jupyter', now = new Date()): string {
  const base = localPath.split('/').pop() || 'notebook.ipynb';
  const stem = base.replace(/\.ipynb$/i, '');
  const stamp = now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  return `classroom/${stem}--backup-${origin}--${stamp}.ipynb`;
}

async function saveBackup(
  app: JupyterFrontEnd,
  localPath: string,
  notebook: any,
  origin: 'drive' | 'jupyter'
): Promise<string> {
  const contents = app.serviceManager.contents;
  const base = backupName(localPath, origin);
  let candidate = base;
  let suffix = 2;
  while (true) {
    try {
      await contents.get(candidate, { content: false });
      candidate = base.replace(/\.ipynb$/, ` (${suffix++}).ipynb`);
    } catch (_) {
      break;
    }
  }
  await contents.save(candidate, { type: 'notebook', format: 'json', content: notebook });
  return candidate;
}

async function replaceLocalNotebook(
  app: JupyterFrontEnd,
  documentManager: IDocumentManager,
  path: string,
  notebook: any
): Promise<void> {
  await app.serviceManager.contents.save(path, {
    type: 'notebook',
    format: 'json',
    content: notebook
  });
  const widget = documentManager.findWidget(path);
  if (widget) {
    const context = documentManager.contextForWidget(widget);
    if (!context) {
      throw new Error('Não foi possível recarregar o notebook local.');
    }
    await context.revert();
  }
}

async function updateBaseline(
  file: ClassroomFile,
  metadata: DriveMetadata
): Promise<void> {
  await serverPost('/google-classroom-student/api/association', {
    file_id: file.fileId,
    path: file.localPath,
    modified_time: metadata.modifiedTime,
    version: metadata.version,
    md5_checksum: metadata.md5Checksum,
    can_edit: metadata.canEdit
  });
}

async function synchronizeNotebook(
  app: JupyterFrontEnd,
  documentManager: IDocumentManager,
  token: string,
  file: ClassroomFile
): Promise<DriveMetadata> {
  if (!file.localPath) {
    throw new Error('A cópia local deste notebook não foi encontrada.');
  }

  let association: LocalAssociation;
  try {
    association = (await serverGet(
      `/google-classroom-student/api/association?file_id=${encodeURIComponent(file.fileId)}`
    )) as LocalAssociation;
  } catch (_) {
    throw new Error('A cópia local deste notebook não foi encontrada.');
  }
  if (association.path !== file.localPath || association.file_id !== file.fileId) {
    throw new Error('A cópia local deste notebook não foi encontrada.');
  }

  try {
    await saveTargetIfOpen(documentManager, file.localPath);
  } catch (_) {
    throw new Error('Não foi possível salvar o notebook local antes da sincronização.');
  }

  let contents: any;
  try {
    contents = await app.serviceManager.contents.get(file.localPath, { content: true });
  } catch (_) {
    throw new Error('A cópia local deste notebook não foi encontrada.');
  }
  const notebook = validateNotebook(JSON.stringify(contents.content));

  const current = await getDriveMetadata(token, file.fileId);
  if (!current.canEdit) {
    throw new Error('Não foi possível editar este arquivo no Google Drive.');
  }
  if (remoteChanged(baselineFromAssociation(association), current)) {
    throw new DriveConflictError(current, notebook);
  }

  await uploadDriveContent(token, file.fileId, notebook);
  const updated = await getDriveMetadata(token, file.fileId);
  await updateBaseline(file, updated);
  return updated;
}

async function resolveDriveConflict(
  app: JupyterFrontEnd,
  documentManager: IDocumentManager,
  token: string,
  file: ClassroomFile,
  conflict: DriveConflictError
): Promise<DriveMetadata | null> {
  const result = await showDialog({
    title: 'Conflito de versões',
    body: 'O notebook foi alterado no Jupyter e no Google Drive. Escolha qual versão preservar no arquivo principal. A versão descartada será salva automaticamente como backup local.',
    buttons: [
      Dialog.okButton({ label: 'Usar versão do Jupyter' }),
      Dialog.warnButton({ label: 'Usar versão do Google Drive' }),
      Dialog.cancelButton({ label: 'Cancelar' })
    ]
  });
  if (result.button.label === 'Cancelar') {
    return null;
  }
  if (!file.localPath) {
    throw new Error('A cópia local deste notebook não foi encontrada.');
  }

  if (result.button.label === 'Usar versão do Jupyter') {
    const remoteNotebook = await downloadDriveNotebook(token, file.fileId);
    await saveBackup(app, file.localPath, remoteNotebook, 'drive');
    const latest = await getDriveMetadata(token, file.fileId);
    if (remoteChanged(conflict.current, latest) || remoteChanged(latest, conflict.current)) {
      throw new Error('O Google Drive mudou novamente durante a resolução; nenhuma versão foi sobrescrita.');
    }
    await uploadDriveContent(token, file.fileId, conflict.localNotebook);
    const updated = await getDriveMetadata(token, file.fileId);
    await updateBaseline(file, updated);
    return updated;
  }

  await saveBackup(app, file.localPath, conflict.localNotebook, 'jupyter');
  const remoteNotebook = await downloadDriveNotebook(token, file.fileId);
  const latest = await getDriveMetadata(token, file.fileId);
  if (remoteChanged(conflict.current, latest) || remoteChanged(latest, conflict.current)) {
    throw new Error('O Google Drive mudou novamente durante a resolução; o notebook local não foi substituído.');
  }
  await replaceLocalNotebook(app, documentManager, file.localPath, remoteNotebook);
  await updateBaseline(file, latest);
  return latest;
}

function clearAccessToken(): void {
  accessToken = null;
  expiresAt = 0;
  tokenClient = null;
}

class ClassroomPanel extends Widget {
  private app: JupyterFrontEnd;
  private documentManager: IDocumentManager;
  private rootEl: HTMLElement;
  private configured = false;
  private clientId: string | null = null;
  private files: ClassroomFile[] = [];
  private busy = false;
  private errorMsg: string | null = null;
  private infoMsg: string | null = null;
  private missingScopes: string[] = [];
  private courseworkScopeGranted = false;
  private courseFilter = '';
  private activitySort: ActivitySort = 'newest';
  private startPromise: Promise<void> | null = null;

  constructor(app: JupyterFrontEnd, documentManager: IDocumentManager) {
    super();
    this.app = app;
    this.documentManager = documentManager;
    this.id = PANEL_ID;
    this.title.label = 'Google Classroom — Aluno';
    this.title.closable = true;
    this.addClass('google-classroom-student-root');
    this.rootEl = document.createElement('div');
    this.rootEl.className = 'google-classroom-student-panel';
    this.rootEl.style.display = 'flex';
    this.rootEl.style.flexDirection = 'column';
    this.rootEl.style.height = '100%';
    this.rootEl.style.minHeight = '0';
    this.rootEl.style.overflow = 'hidden';
    this.node.appendChild(this.rootEl);
  }

  async start(): Promise<void> {
    if (!this.startPromise) {
      this.startPromise = (async () => {
        await this.loadConfig();
        this.render();
      })();
    }
    try {
      await this.startPromise;
    } catch (error) {
      this.startPromise = null;
      throw error;
    }
  }

  private async loadConfig(): Promise<void> {
    try {
      const cfg = await serverGet('/google-classroom-student/api/config');
      this.configured = !!cfg.configured;
      this.clientId = typeof cfg.client_id === 'string' && cfg.client_id ? cfg.client_id : null;
      this.errorMsg = null;
      if (this.configured && this.clientId) {
        await loadGis();
      }
    } catch (_) {
      this.configured = false;
      this.clientId = null;
      this.errorMsg = 'Falha ao carregar a configuração do Classroom.';
    }
  }

  private isConnected(): boolean {
    if (!accessToken) {
      return false;
    }
    if (Date.now() >= expiresAt) {
      clearAccessToken();
      this.files = [];
      return false;
    }
    return true;
  }

  private render(): void {
    this.rootEl.textContent = '';
    Object.assign(this.rootEl.style, { display: 'flex', flexDirection: 'column', minHeight: '0', height: '100%' });
    const header = document.createElement('div');
    Object.assign(header.style, { flex: '0 0 auto' });
    const title = document.createElement('h2');
    title.textContent = 'Google Classroom — Aluno';
    header.appendChild(title);
    this.rootEl.appendChild(header);

    if (!this.configured || !this.clientId) {
      if (this.errorMsg) {
        const error = document.createElement('p');
        error.className = 'google-classroom-student-error';
        error.textContent = this.errorMsg;
        header.appendChild(error);
      }
      const p = document.createElement('p');
      p.textContent = 'Integração ainda não configurada pelo administrador.';
      header.appendChild(p);
      return;
    }

    if (!this.isConnected()) {
      if (this.errorMsg) {
        const error = document.createElement('p');
        error.className = 'google-classroom-student-error';
        error.textContent = this.errorMsg;
        header.appendChild(error);
      }
      if (this.infoMsg) {
        const info = document.createElement('p');
        info.className = 'google-classroom-student-info';
        info.textContent = this.infoMsg;
        header.appendChild(info);
      }
      const connect = document.createElement('button');
      connect.textContent = this.missingScopes.length
        ? 'Autorizar permissões restantes'
        : 'Conectar ao Google';
      connect.addEventListener('click', () => void this.connect());
      header.appendChild(connect);
      return;
    }

    const actions = document.createElement('div');
    actions.className = 'google-classroom-student-actions';
    Object.assign(actions.style, { display: 'flex', flexWrap: 'wrap', gap: '6px' });
    const refresh = document.createElement('button');
    refresh.textContent = 'Atualizar';
    refresh.addEventListener('click', () => void this.refresh());
    const disconnectBtn = document.createElement('button');
    disconnectBtn.textContent = 'Desconectar';
    disconnectBtn.addEventListener('click', () => {
      this.clearAuthorization();
      this.files = [];
      this.render();
    });
    actions.appendChild(refresh);
    actions.appendChild(disconnectBtn);
    header.appendChild(actions);

    if (this.infoMsg) {
      const info = document.createElement('p');
      info.className = 'google-classroom-student-info';
      info.textContent = this.infoMsg;
      header.appendChild(info);
    }

    if (this.busy) {
      const p = document.createElement('p');
      p.textContent = 'Carregando atividades...';
      header.appendChild(p);
      return;
    }

    if (this.errorMsg) {
      const p = document.createElement('p');
      p.className = 'google-classroom-student-error';
      p.textContent = this.errorMsg;
      header.appendChild(p);
      if (!this.files.length) {
        return;
      }
    }

    if (!this.files.length) {
      const p = document.createElement('p');
      p.textContent = 'Nenhuma atividade com notebook foi encontrada.';
      header.appendChild(p);
      return;
    }

    const controls = document.createElement('div');
    controls.className = 'google-classroom-student-controls';
    Object.assign(controls.style, { display: 'flex', flexWrap: 'wrap', gap: '6px', margin: '8px 0' });
    const courseLabel = document.createElement('label');
    courseLabel.textContent = 'Turma: ';
    const courseSelect = document.createElement('select');
    courseSelect.appendChild(new Option('Todas as turmas', ''));
    const courses = [...new Map(this.files.map(file => [file.courseId, file])).values()]
      .sort((a, b) => a.courseName.localeCompare(b.courseName, 'pt-BR'));
    if (this.courseFilter && !courses.some(course => course.courseId === this.courseFilter)) {
      this.courseFilter = '';
    }
    for (const course of courses) {
      courseSelect.appendChild(new Option(
        `${course.courseName}${course.courseSection ? ` — ${course.courseSection}` : ''}`,
        course.courseId
      ));
    }
    courseSelect.value = this.courseFilter;
    courseSelect.onchange = () => { this.courseFilter = courseSelect.value; this.render(); };
    courseLabel.appendChild(courseSelect);
    controls.appendChild(courseLabel);
    const sortLabel = document.createElement('label');
    sortLabel.textContent = ' Ordenar: ';
    const sortSelect = document.createElement('select');
    for (const [value, label] of [
      ['newest', 'Mais recente'], ['oldest', 'Mais antiga'],
      ['name-asc', 'Nome A→Z'], ['name-desc', 'Nome Z→A']
    ] as const) sortSelect.appendChild(new Option(label, value));
    sortSelect.value = this.activitySort;
    sortSelect.onchange = () => { this.activitySort = sortSelect.value as ActivitySort; this.render(); };
    sortLabel.appendChild(sortSelect);
    controls.appendChild(sortLabel);
    header.appendChild(controls);

    const list = document.createElement('div');
    Object.assign(list.style, { flex: '1 1 auto', minHeight: '0', overflowY: 'auto', overflowX: 'hidden' });
    for (const activity of filterAndSortClassroomActivities(this.files, this.courseFilter, this.activitySort)) {
      const item = document.createElement('section');
      item.className = 'google-classroom-student-activity';
      Object.assign(item.style, { border: '1px solid var(--jp-border-color2)', borderRadius: '4px', margin: '8px 4px', padding: '8px' });

      const heading = document.createElement('div');
      heading.className = 'google-classroom-student-activity-heading';
      const activityTitle = document.createElement('h3');
      activityTitle.textContent = activity.courseworkTitle;
      heading.appendChild(activityTitle);
      if (!this.courseFilter) {
        const course = document.createElement('small');
        course.textContent = activity.courseName + (activity.courseSection ? ` — ${activity.courseSection}` : '');
        heading.appendChild(course);
      }
      if (activity.creationTime) {
        const date = document.createElement('time');
        date.dateTime = activity.creationTime;
        date.textContent = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short' }).format(new Date(activity.creationTime));
        Object.assign(date.style, { color: 'var(--jp-ui-font-color2)', fontSize: 'var(--jp-ui-font-size0)', marginLeft: '8px' });
        heading.appendChild(date);
      }
      item.appendChild(heading);
      for (const file of activity.files) {
        const notebook = document.createElement('div');
        notebook.className = 'google-classroom-student-notebook';
        Object.assign(notebook.style, { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px', borderTop: '1px solid var(--jp-border-color2)', padding: '8px 0 0', marginTop: '8px' });
        const fileName = document.createElement('strong');
        fileName.className = 'google-classroom-student-filename';
        fileName.textContent = file.name;
        notebook.appendChild(fileName);
        const openBtn = document.createElement('button');
        openBtn.textContent = 'Abrir';
        openBtn.addEventListener('click', () => void this.open(file));
        notebook.appendChild(openBtn);
        if (file.localPath) {
          const syncBtn = document.createElement('button');
          syncBtn.textContent = file.syncState === 'syncing' ? 'Sincronizando…' : 'Sincronizar com Drive';
          syncBtn.disabled = file.syncState === 'syncing' || file.canEdit === false;
          syncBtn.addEventListener('click', () => void this.sync(file));
          notebook.appendChild(syncBtn);
        }
        if (file.canEdit === false) {
          const permission = document.createElement('small');
          permission.className = 'google-classroom-student-error';
          permission.textContent = 'Sem permissão de edição';
          notebook.appendChild(permission);
        } else if (file.syncState === 'conflict' || file.syncState === 'error') {
          const status = document.createElement('small');
          status.className = 'google-classroom-student-error';
          status.textContent = file.syncMessage || 'Erro ao sincronizar com o Google Drive.';
          notebook.appendChild(status);
        } else if (file.syncState === 'synced') {
          const status = document.createElement('small');
          status.className = 'google-classroom-student-info';
          status.textContent = file.lastSyncedAt ? `Sincronizado com Drive — ${file.lastSyncedAt}` : 'Sincronizado com Drive';
          notebook.appendChild(status);
        }
        item.appendChild(notebook);
      }
      list.appendChild(item);
    }
    this.rootEl.appendChild(list);
  }

  private async connect(): Promise<void> {
    if (!this.clientId) {
      return;
    }
    this.errorMsg = null;
    const isReauthorization = this.missingScopes.length > 0;
    this.infoMsg = isReauthorization
      ? 'Solicitando as permissões restantes ao Google...'
      : null;
    this.render();
    try {
      await loadGis();
      if (!tokenClient) {
        tokenClient = window.google.accounts.oauth2.initTokenClient({
          client_id: this.clientId,
          scope: SCOPE_STR,
          callback: (response: GsiTokenResponse) => this.onToken(response),
          error_callback: (error: GsiTokenError) => {
            console.info('google-classroom-student: autorização não concluída', {
              requestedScopeCount: this.missingScopes.length || SCOPES.length,
              missingScopeCount: this.missingScopes.length,
              result: 'cancelada_ou_falha'
            });
            this.errorMsg = authErrorMessage(error);
            this.infoMsg = null;
            this.render();
          }
        });
      }
      tokenClient.requestAccessToken({
        scope: isReauthorization ? this.missingScopes.join(' ') : SCOPE_STR,
        include_granted_scopes: true,
        prompt: isReauthorization ? 'consent' : ''
      });
    } catch (error) {
      this.errorMsg = shortError(error);
      this.render();
    }
  }

  private onToken(response: GsiTokenResponse): void {
    const requestedScopeCount = this.missingScopes.length || SCOPES.length;
    if (!response || !response.access_token) {
      console.info('google-classroom-student: reautorização sem token', {
        requestedScopeCount,
        missingScopeCount: this.missingScopes.length,
        result: 'sem_token'
      });
      this.errorMsg = 'Não foi possível obter um access_token.';
      this.infoMsg = null;
      this.render();
      return;
    }
    const returnedScopes = parseGrantedScopes(response.scope);
    const returnedScopeSet = new Set(returnedScopes);
    const missingFromToken = SCOPES.filter((scope) => !returnedScopeSet.has(scope));
    let missingFromHelper: string[] = [];
    let helperAvailable = true;
    try {
      missingFromHelper = SCOPES.filter(
        (scope) => !window.google.accounts.oauth2.hasGrantedAllScopes(response, scope)
      );
    } catch (_) {
      helperAvailable = false;
    }
    const recognizedScopeCount = returnedScopes.filter((scope) => SCOPES.includes(scope)).length;
    const scopeDiverges =
      JSON.stringify(missingFromToken) !== JSON.stringify(missingFromHelper);
    const labelsFor = (scopes: string[]): string[] =>
      scopes.map((scope) => SCOPE_LABELS[scope] || 'permissão necessária');
    console.info('google-classroom-student: diagnóstico de escopos OAuth', {
      returnedScopeCount: returnedScopes.length,
      returnedScopes,
      recognizedScopeCount,
      missingFromToken,
      missingCategoriesFromToken: labelsFor(missingFromToken),
      missingFromHelper: helperAvailable ? missingFromHelper : 'indisponível',
      missingCategoriesFromHelper: helperAvailable ? labelsFor(missingFromHelper) : 'indisponível',
      diverges: scopeDiverges,
      result: missingFromToken.length ? 'escopos_insuficientes' : 'escopos_confirmados'
    });

    const scopeFieldPresent = returnedScopes.length > 0;
    const grantedScopes = scopeFieldPresent
      ? returnedScopes
      : SCOPES.filter(scope => !missingFromHelper.includes(scope));
    const classified = classifyGrantedScopes(grantedScopes);
    const missingRequired = scopeFieldPresent
      ? classified.missingRequired
      : missingFromHelper.filter(scope => REQUIRED_SCOPES.includes(scope));
    const missingOptional = scopeFieldPresent
      ? classified.missingOptional
      : missingFromHelper.filter(scope => OPTIONAL_SCOPES.includes(scope));
    const missing = [...missingRequired, ...missingOptional];
    if (!scopeFieldPresent && !helperAvailable) {
      this.errorMsg = 'A resposta OAuth não trouxe o campo scope; não foi possível confirmar as permissões.';
      this.infoMsg = null;
      this.render();
      return;
    }
    if (missingRequired.length) {
      this.missingScopes = missing;
      console.info('google-classroom-student: permissões insuficientes', {
        requestedScopeCount,
        missingScopeCount: missing.length,
        source: scopeFieldPresent ? 'TokenResponse.scope' : 'hasGrantedAllScopes',
        result: 'escopos_insuficientes'
      });
      const labels = missing.map((scope) => SCOPE_LABELS[scope] || 'permissão necessária');
      const prefix = scopeFieldPresent ? '' : 'A resposta OAuth não trouxe o campo scope. ';
      this.errorMsg = `${prefix}Permissões insuficientes: faltam ${labels.join(', ')}.`;
      this.infoMsg = null;
      this.render();
      return;
    }
    this.missingScopes = missing;
    this.courseworkScopeGranted = !missingOptional.length;
    accessToken = response.access_token;
    expiresAt = Date.now() + response.expires_in * 1000;
    console.info('google-classroom-student: autorização concluída', {
      requestedScopeCount,
      missingScopeCount: 0,
      result: 'autorizado'
    });
    this.errorMsg = null;
    this.infoMsg = missingOptional.length
      ? 'Conectado. Títulos reais indisponíveis sem a permissão opcional; usando “Atividade do Google Classroom”.'
      : scopeDiverges
        ? 'Permissões confirmadas; consultando o Classroom.'
        : 'Autorização recebida. Todos os escopos foram concedidos; consultando o Classroom...';
    this.files = [];
    void this.refresh(scopeDiverges);
  }

  private async refresh(preserveInfo = false): Promise<void> {
    if (!accessToken) {
      return;
    }
    this.busy = true;
    this.errorMsg = null;
    if (!preserveInfo) {
      this.infoMsg = 'Iniciando consulta ao Classroom...';
    }
    this.render();
    const token = accessToken;
    try {
      const result = await collectClassroomFiles(token, this.courseworkScopeGranted);
      this.files = result.files;
      await hydrateAssociations(this.files);
      this.errorMsg = null;
      this.infoMsg = result.warning || 'Consulta ao Classroom concluída.';
    } catch (error) {
      this.errorMsg = shortError(error);
      this.infoMsg = null;
      if (error instanceof GoogleApiError && error.status === 401) {
        clearAccessToken();
        this.files = [];
      }
    } finally {
      this.busy = false;
      this.render();
    }
  }

  private async open(file: ClassroomFile): Promise<void> {
    this.busy = true;
    this.errorMsg = null;
    this.render();
    try {
      if (file.localPath && (await localNotebookExists(this.app, file.localPath))) {
        await openLocalNotebook(this.app, file.localPath);
        return;
      }
      if (!accessToken) {
        return;
      }
      const imported = await openNotebook(this.app, accessToken, file);
      file.localPath = imported.path;
      file.baseline = {
        modifiedTime: imported.metadata.modifiedTime,
        version: imported.metadata.version,
        md5Checksum: imported.metadata.md5Checksum
      };
      file.canEdit = imported.metadata.canEdit;
      file.syncState = undefined;
      file.syncMessage = undefined;
    } catch (error) {
      this.errorMsg = shortError(error);
    } finally {
      this.busy = false;
      this.render();
    }
  }

  private async sync(file: ClassroomFile): Promise<void> {
    if (!accessToken || !file.localPath || file.canEdit === false) {
      return;
    }
    file.syncState = 'syncing';
    file.syncMessage = undefined;
    this.render();
    const token = accessToken;
    try {
      let metadata: DriveMetadata;
      try {
        metadata = await synchronizeNotebook(this.app, this.documentManager, token, file);
      } catch (error) {
        if (!(error instanceof DriveConflictError)) {
          throw error;
        }
        const resolved = await resolveDriveConflict(
          this.app,
          this.documentManager,
          token,
          file,
          error
        );
        if (!resolved) {
          file.syncState = 'conflict';
          file.syncMessage = 'Conflito mantido. Nenhuma versão foi alterada.';
          return;
        }
        metadata = resolved;
      }
      file.baseline = {
        modifiedTime: metadata.modifiedTime,
        version: metadata.version,
        md5Checksum: metadata.md5Checksum
      };
      file.canEdit = metadata.canEdit;
      file.syncState = 'synced';
      file.lastSyncedAt = new Date().toLocaleString();
    } catch (error) {
      file.syncState = error instanceof DriveConflictError ? 'conflict' : 'error';
      file.syncMessage = shortError(error);
      this.errorMsg = file.syncMessage;
      if (error instanceof GoogleApiError && error.status === 401) {
        clearAccessToken();
      }
    } finally {
      this.render();
    }
  }

  private clearAuthorization(): void {
    clearAccessToken();
    this.missingScopes = [];
    this.courseworkScopeGranted = false;
    this.errorMsg = null;
    this.infoMsg = null;
  }
}

const plugin: JupyterFrontEndPlugin<void> = {
  id: PLUGIN_ID,
  description: 'Extensão Google Classroom para o JupyterLab Google Classroom (MVP 0.3.9).',
  autoStart: true,
  requires: [ICommandPalette, IMainMenu, IDocumentManager],

  activate: (
    app: JupyterFrontEnd,
    palette: ICommandPalette,
    mainMenu: IMainMenu,
    documentManager: IDocumentManager
  ): void => {
    let panel: ClassroomPanel | null = null;

    const ensurePanel = (): ClassroomPanel => {
      if (!panel || panel.isDisposed) {
        panel = new ClassroomPanel(app, documentManager);
      }
      if (!panel.isAttached) {
        app.shell.add(panel, 'right');
      }
      return panel;
    };

    const openPanel = async (): Promise<void> => {
      await app.started;
      const currentPanel = ensurePanel();
      app.shell.activateById(currentPanel.id);
      await currentPanel.start();
    };

    app.commands.addCommand(COMMAND_ID, {
      label: 'Google Classroom',
      caption: 'Abrir o painel lateral do Google Classroom.',
      execute: async (): Promise<void> => {
        const currentPanel = ensurePanel();
        app.shell.activateById(currentPanel.id);
        await currentPanel.start();
      }
    });

    palette.addItem({
      command: COMMAND_ID,
      category: 'Google Classroom'
    });

    const googleMenu = new Menu({ commands: app.commands });
    googleMenu.title.label = 'Google';
    googleMenu.addItem({ command: COMMAND_ID });
    mainMenu.addMenu(googleMenu, true, { rank: 900 });

    void openPanel();
  }
};

export default plugin;
