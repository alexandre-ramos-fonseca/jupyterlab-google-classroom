# Student component

The student extension lists the signed-in student's Google Classroom notebook attachments, imports a selected notebook into the Jupyter workspace, and explicitly synchronizes local changes with the same Drive file.

## Install from source

From the repository root:

```sh
cd packages/student
corepack enable
yarn install --immutable
yarn build
python -m pip install -e .
```

Set the Google OAuth client ID before starting JupyterLab:

```sh
export GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
```

See [`../../docs/google-cloud-setup.md`](../../docs/google-cloud-setup.md) for the required Classroom/Drive APIs, OAuth scopes, and Authorized JavaScript origins.

## Workflow

1. Open the student Classroom panel in JupyterLab.
2. Authorize the Google account.
3. Select a published `.ipynb` attachment.
4. On first open, the notebook is imported into the Jupyter workspace and associated with its Drive file.
5. Later opens reuse that local association.
6. Edit the local notebook normally.
7. Use **Synchronize with Drive** explicitly to update the associated Drive file.

The extension never submits Classroom work automatically.

## OAuth and scopes

Google Identity Services runs in the browser. Tokens are memory-only and are never sent to the Jupyter Server.

Required scopes:

```text
classroom.courses.readonly
classroom.student-submissions.me.readonly
drive
```

Optional scope used to display activity titles:

```text
classroom.coursework.me
```

No client secret, refresh token, or token persistence is used.

## Conflict protection

The extension stores a baseline for the local/Drive association. If both sides changed since that baseline, synchronization stops instead of silently overwriting one version.

The implementation also provides:

- atomic local writes;
- restrictive permissions for local association metadata;
- non-overwriting first imports;
- backups before destructive replacement.

## Server extension

The Python server extension is `google_classroom_student` and exposes endpoints under:

```text
/google-classroom-student/api/
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
