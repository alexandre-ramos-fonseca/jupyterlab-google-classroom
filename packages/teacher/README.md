# Teacher component

The teacher extension lists published Classroom notebook attachments for the signed-in teacher and opens each in a local working copy. On the first synchronization it creates a separate Drive copy; all later uploads target that copy. The original Classroom attachment is explicitly read-only and is never updated.

OAuth uses Google Identity Services in the browser. Tokens are memory-only. Scopes are `classroom.courses.readonly`, `classroom.coursework.students.readonly`, and `drive.file`. Configure `GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID` and the deployment-controlled `GOOGLE_CLASSROOM_TEACHER_TERM`.

The server extension is `google_classroom_teacher`, with endpoints under `/google-classroom-teacher/api/`. Local associations are stored with restrictive permissions under the workspace and contain no token or notebook contents.

Conflict detection, immutable target associations, atomic writes, restoration guards, and backups are retained. The extension does not submit or modify Classroom coursework.
