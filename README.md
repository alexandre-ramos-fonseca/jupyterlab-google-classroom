# JupyterLab Google Classroom

This repository contains two independent JupyterLab/Jupyter Server extensions for working with Google Classroom notebooks:

- `packages/student` — a student workflow that lists the student's Classroom notebook attachments, opens a local copy, and explicitly synchronizes changes with the same Drive file.
- `packages/teacher` — a teacher workflow that opens a local working copy of a published notebook and synchronizes only a separate Drive copy. The original Classroom attachment is never modified.

The components share no runtime code and are intentionally not merged. Each package contains its TypeScript frontend, Python server extension, tests, and package metadata.

## Security and OAuth model

Google Identity Services uses the OAuth authorization-code-free token flow in the browser. The access token exists only in browser memory: it is not sent to the Jupyter Server and is not written to localStorage, sessionStorage, cookies, files, or a database. Users must authorize again after a page reload, token expiry, or JupyterLab restart. There is no client secret or refresh-token storage.

Create a Google OAuth web client ID in Google Cloud Console, configure the authorized JavaScript origins for the JupyterLab deployment, and set the public client ID in the server environment:

```sh
export GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
```

The teacher component additionally requires a deployment-defined term label:

```sh
export GOOGLE_CLASSROOM_TEACHER_TERM="2026-2"
```

The student scopes are `classroom.courses.readonly`, `classroom.student-submissions.me.readonly`, and `drive`, plus optional `classroom.coursework.me` for activity titles. The teacher scopes are `classroom.courses.readonly`, `classroom.coursework.students.readonly`, and `drive.file`. The extensions perform read/list operations and Drive notebook content operations only; they do not submit work or create/edit/delete Classroom coursework.

## Requirements and installation

Requires Python 3.9+, JupyterLab 4, Jupyter Server 2, Node.js, and a Google OAuth web client configured for the deployment. Install either component from its directory:

```sh
cd packages/student   # or packages/teacher
corepack enable
yarn install
python -m pip install -e .
```

For development, run `yarn build` and `python -m unittest discover -s tests -v`. The build produces the JupyterLab prebuilt assets inside the Python package. This repository does not publish to npm or PyPI.

## Safeguards and limitations

Local notebook writes and association metadata use restrictive permissions and atomic updates. Existing local imports are not silently overwritten. Synchronization checks the stored Drive baseline and stops for conflicts; the user must choose which version to keep, with backups made before destructive replacement. There is no automatic merge, background synchronization, multi-account support, or automatic Classroom submission.

The teacher flow copies the original Drive file before its first upload and rejects any upload whose target is the original file. The original attachment therefore remains read-only from this extension.

See the component READMEs for endpoint details and workflow-specific behavior.

## License

MIT © Alexandre Ramos Fonseca. See [LICENSE](LICENSE).
