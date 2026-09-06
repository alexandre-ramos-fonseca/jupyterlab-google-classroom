# JupyterLab Google Classroom

JupyterLab/Jupyter Server extensions for working with Google Classroom notebook attachments while keeping a local Jupyter workspace in sync with Google Drive.

The repository contains two independent components:

| Component | Intended user | Behavior |
| --- | --- | --- |
| [`packages/student`](packages/student) | Student | Imports the student's Classroom notebook attachment into JupyterLab and explicitly synchronizes changes back to the same Drive file. |
| [`packages/teacher`](packages/teacher) | Teacher | Opens a local working copy of a published notebook and synchronizes only a separate Drive copy. The original Classroom attachment is never modified. |

The components intentionally share no runtime code. Each package contains its own TypeScript frontend, Python Jupyter Server extension, tests, and package metadata.

## Requirements

- Python 3.9+;
- JupyterLab 4 / Jupyter Server 2;
- Node.js 20 recommended for building from source;
- a Google Workspace for Education account with Google Classroom enabled;
- a Google Cloud project with the Classroom and Drive APIs enabled and a Web OAuth client configured for the JupyterLab origin.

See [Google Cloud and OAuth setup](docs/google-cloud-setup.md) for the complete Google-side configuration.

## Quick start from source

Clone the repository and create a Python environment with JupyterLab:

```sh
git clone https://github.com/alexandre-ramos-fonseca/jupyterlab-google-classroom.git
cd jupyterlab-google-classroom
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip 'jupyterlab>=4.5,<5'
corepack enable
```

Build and install the component you need.

### Student

```sh
cd packages/student
yarn install --immutable
yarn build
python -m pip install -e .
cd ../..
```

### Teacher

```sh
cd packages/teacher
yarn install --immutable
yarn build
python -m pip install -e .
cd ../..
```

Both components may be installed in the same Jupyter environment.

Configure the Google OAuth client ID before starting JupyterLab:

```sh
export GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
```

The teacher component additionally requires a deployment-defined term label:

```sh
export GOOGLE_CLASSROOM_TEACHER_TERM="2026-2"
```

Then start JupyterLab:

```sh
jupyter lab
```

Useful installation checks:

```sh
jupyter server extension list
jupyter labextension list
```

## Google OAuth model

Google Identity Services uses the browser token flow. The access token exists only in browser memory: it is not sent to the Jupyter Server and is not written to localStorage, sessionStorage, cookies, files, or a database. Users must authorize again after a page reload, token expiry, or JupyterLab restart. There is no client secret or refresh-token storage.

The student component requests `classroom.courses.readonly`, `classroom.student-submissions.me.readonly`, and `drive`, plus optional `classroom.coursework.me` for activity titles.

The teacher component requests `classroom.courses.readonly`, `classroom.coursework.students.readonly`, and `drive.file`.

The extensions perform Classroom read/list operations and Drive notebook content operations only. They do not submit student work or create/edit/delete Classroom coursework.

## Student workflow

1. Authorize the Google account in the extension.
2. Select a Classroom notebook attachment.
3. The notebook is imported once into the Jupyter workspace; later opens reuse the stored association.
4. Edit normally in JupyterLab.
5. Use **Synchronize with Drive** explicitly when you want to update the associated Drive file.

If both the local file and Drive file changed since the stored baseline, synchronization stops and requires the user to choose which version to keep. Backups are made before destructive replacement.

See [`packages/student/README.md`](packages/student/README.md) for component details.

## Teacher workflow

1. Authorize the Google account in the extension.
2. Select a published Classroom notebook attachment.
3. Open its local working copy.
4. On the first synchronization, the extension creates a separate Drive copy.
5. All later uploads target that copy.

The original Classroom attachment is explicitly protected and is never an upload target.

See [`packages/teacher/README.md`](packages/teacher/README.md) for component details.

## Safeguards and limitations

- local notebook writes and association metadata use restrictive permissions and atomic updates;
- existing local imports are not silently overwritten;
- synchronization uses stored Drive baselines and stops on conflicts;
- backups are created before destructive replacement;
- there is no automatic merge or background synchronization;
- there is no automatic Classroom submission;
- there is no refresh-token persistence;
- multi-account operation within one loaded JupyterLab page is not supported.

## Development and tests

Each component is built and tested independently:

```sh
cd packages/student   # or packages/teacher
corepack enable
yarn install --immutable
yarn build
python -m unittest discover -s tests -v
```

CI runs the TypeScript/JupyterLab build, Python tests, and wheel build for both components.

The repository does not currently publish packages to npm or PyPI; installation is from source.

## License

MIT © Alexandre Ramos Fonseca. See [LICENSE](LICENSE).
