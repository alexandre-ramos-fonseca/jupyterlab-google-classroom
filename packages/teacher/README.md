# Teacher component

The teacher extension lists Google Classroom notebook attachments for the signed-in teacher, opens each in a local Jupyter working copy, and synchronizes only a separate Drive copy. The original Classroom attachment is never modified.

## Install from source

From the repository root:

```sh
cd packages/teacher
corepack enable
yarn install --immutable
yarn build
python -m pip install -e .
```

Configure the Google OAuth client ID and the deployment term label before starting JupyterLab:

```sh
export GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
export GOOGLE_CLASSROOM_TEACHER_TERM="2026-2"
```

Replace the term example with the value used by your deployment.

See [`../../docs/google-cloud-setup.md`](../../docs/google-cloud-setup.md) for the required Classroom/Drive APIs, OAuth scopes, and Authorized JavaScript origins.

## Workflow

1. Open the teacher Classroom panel in JupyterLab.
2. Authorize the Google account.
3. Select a published `.ipynb` attachment.
4. Open its local working copy.
5. On the first synchronization, the extension creates a separate Drive copy.
6. All later uploads target that copy.

The source attachment published in Classroom is treated as read-only. The extension rejects uploads whose target would be the original file.

## OAuth and scopes

Google Identity Services runs in the browser. Tokens are memory-only and are never sent to the Jupyter Server.

Required scopes:

```text
classroom.courses.readonly
classroom.coursework.students.readonly
drive.file
```

No client secret, refresh token, or token persistence is used.

## Safeguards

The implementation provides:

- immutable target associations after the synchronization copy is created;
- explicit protection against uploading to the original Classroom attachment;
- baseline-based conflict detection;
- atomic local writes;
- restrictive permissions for association metadata;
- backups and restoration guards before destructive replacement.

The extension does not create, edit, delete, or submit Classroom coursework.

## Server extension

The Python server extension is `google_classroom_teacher` and exposes endpoints under:

```text
/google-classroom-teacher/api/
```

After installation, useful checks are:

```sh
jupyter server extension list
jupyter labextension list
```

## Development

```sh
yarn typecheck
yarn build
python -m unittest discover -s tests -v
```

The component is currently distributed from source rather than PyPI/npm.
