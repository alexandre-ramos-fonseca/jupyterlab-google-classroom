# Google Cloud and OAuth setup

The extensions call Google Classroom and Google Drive directly from the browser using Google Identity Services. The access token remains in browser memory; the Jupyter Server never receives it.

## 1. Prerequisites

You need:

- a Google Cloud project;
- a Google Workspace for Education account with Google Classroom enabled;
- permission to configure OAuth credentials for the project;
- the public origin where JupyterLab will run, for example `https://jupyter.example.edu`.

For local testing, an origin such as `http://localhost:8888` can be used.

## 2. Enable APIs

In the Google Cloud project, enable:

- **Google Classroom API**;
- **Google Drive API**.

Both extensions use Classroom to discover courses/coursework and Drive to read or synchronize notebook files.

## 3. Configure Google Auth Platform

In **Google Auth Platform**, configure the application information under **Branding**, the intended users under **Audience**, and the required permissions under **Data Access**.

For a limited test deployment, add the accounts that will test the application if the OAuth project is in testing mode. Production or external deployments may require Google OAuth verification depending on the audience and scopes used.

### Student scopes

The student component requests:

```text
https://www.googleapis.com/auth/classroom.courses.readonly
https://www.googleapis.com/auth/classroom.student-submissions.me.readonly
https://www.googleapis.com/auth/drive
```

It can additionally request the following scope to display coursework/activity titles:

```text
https://www.googleapis.com/auth/classroom.coursework.me
```

### Teacher scopes

The teacher component requests:

```text
https://www.googleapis.com/auth/classroom.courses.readonly
https://www.googleapis.com/auth/classroom.coursework.students.readonly
https://www.googleapis.com/auth/drive.file
```

If both components are deployed with the same Google Cloud project, configure the consent screen for the union of the scopes that the deployment will use.

## 4. Create a Web OAuth client

In **Google Auth Platform → Clients**, create an OAuth client with application type **Web application**.

Add every JupyterLab origin that will use the extension to **Authorized JavaScript origins**. Use the origin only: scheme, hostname and port when applicable, with no JupyterLab path.

Examples:

```text
http://localhost:8888
https://jupyter.example.edu
```

The extension uses the browser token flow (`google.accounts.oauth2.initTokenClient`). It requires the OAuth **client ID**, not a client secret, and does not store refresh tokens.

Copy the generated client ID. It normally ends in:

```text
.apps.googleusercontent.com
```

## 5. Configure the Jupyter deployment

Set the client ID in the environment before starting JupyterLab:

```sh
export GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
```

The teacher component also requires the deployment term label used to filter/display the active academic term:

```sh
export GOOGLE_CLASSROOM_TEACHER_TERM="2026-2"
```

The term label is deployment-defined; replace the example with the value used by your institution.

## 6. Verify OAuth

Start JupyterLab and open the Classroom panel.

A successful setup should allow the user to:

1. start Google authorization from the extension;
2. select/authorize the appropriate Google account;
3. see Classroom courses and notebook attachments available to that account.

If authorization fails immediately, verify the client ID and make sure the exact browser origin is listed under **Authorized JavaScript origins**.

If authorization succeeds but Classroom or Drive calls fail, verify that both APIs are enabled and that the consent configuration includes the scopes requested by the installed component.

## Security model

- OAuth access tokens exist only in browser memory.
- Tokens are not sent to the Jupyter Server.
- No client secret or refresh token is used by the extension.
- Reloading the page, token expiry, or restarting JupyterLab requires authorization again.
- Synchronization is explicit; there is no background upload.

For production deployments, review Google's current OAuth verification and data-access requirements for the selected audience and scopes.
