# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| < 1.0 | No |

PINE ships as a source download that you run locally, so "updating" means
pulling a newer release and running the launcher again. The in-app check under
Settings → About compares your version against the latest GitHub release.

## Reporting a vulnerability

**Please do not open a public issue.**

Use GitHub's private reporting: go to the
[Security tab](https://github.com/paul815/pine/security/advisories/new) and open
a draft advisory. It is visible only to the maintainers until a fix ships.

Useful things to include: what an attacker can reach, the steps to reproduce,
the affected version and OS, and — if you have one — a patch. You can expect a
first response within a week.

If private reporting is unavailable to you, open a normal issue that says only
that you have a security report and asks for a contact channel. Leave the
technical detail out of it.

## What is in scope

PINE is a single-user desktop application. It binds to `127.0.0.1`, and the
threat model assumes the person at the keyboard owns the machine. Reports that
matter most:

- Anything that lets a **remote or cross-origin** party reach the API, read
  transcripts, or trigger transcription — CORS gaps, DNS rebinding, a WebSocket
  path that skips the origin check
- Path traversal in upload, attachment, export, backup or restore handling —
  anything that reads or writes outside the project, model and backup folders
- Code execution through a crafted media file, transcript JSON, annotation file
  or backup archive
- Leaking the stored HuggingFace token, or any outbound request carrying user
  content to a host not listed in the README's privacy table
- Data-destroying behaviour that a normal user can hit: restore overwriting the
  wrong folder, reset removing more than it should

## Known and accepted

These are documented rather than fixed, and are tracked in
[documentation/TODO.md](../documentation/TODO.md). Reporting them again is not
necessary:

- **The backend has no authentication.** Any local process, and any page open in
  your browser that gets past the CORS list, can call the API. PINE is a local
  dev-server-class application; do not expose its port to a network you do not
  control.
- **The HuggingFace token is stored unencrypted** in the local SQLite database.
  It is a read-scoped token on your own account, sitting on a machine that
  already holds your interview recordings.
- **Backups are unencrypted ZIP archives.** If you enable "include recordings",
  the archive contains the media. Store it accordingly.
- **Restore replaces project data by design.** It writes a safety snapshot first
  and never overwrites your token or path settings.

## What PINE does to reduce exposure

- The web process imports no ML libraries; model code runs in a separate
  `ml_worker` process that can crash without taking the app down
- Transcription runs with the HuggingFace Hub in offline mode, apart from the
  one-time alignment-model download described in the README
- Model and framework telemetry (HuggingFace, pyannote, OpenTelemetry, W&B) is
  disabled in `backend/run.py` before the app is imported
- CORS is limited to `127.0.0.1` and `pine.localhost` on the app's own port
- The supervisor's control API requires a token generated fresh on each run
- Uploads are extension-filtered and names are sanitised; annotation writes are
  locked per file and atomic
