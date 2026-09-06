# Student component

The student extension lists the signed-in student's published Classroom submissions and `.ipynb` attachments. Opening an attachment imports it once into the Jupyter workspace; later opens reuse that local association. “Synchronize with Drive” explicitly updates the associated Drive file.

OAuth uses Google Identity Services in the browser. Tokens are memory-only and the server receives notebook content/metadata only for local persistence. Required scopes are `classroom.courses.readonly`, `classroom.student-submissions.me.readonly`, and `drive`; `classroom.coursework.me` is optional and is used only to display activity titles.

The server extension is `google_classroom_student`, with endpoints under `/google-classroom-student/api/`. Configure `GOOGLE_CLASSROOM_GOOGLE_CLIENT_ID` with a web OAuth client ID. No client secret, refresh token, or token persistence is used.

The implementation preserves baseline comparison, conflict confirmation, atomic local writes, non-overwriting imports, and pre-replacement backups. It never submits Classroom work automatically.
