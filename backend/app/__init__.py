import logging
import os
import sqlite3
import sys
import types
import json
import urllib.request
from datetime import datetime
from flask import Flask, render_template, redirect, request, jsonify
from flask_cors import CORS
from .config import Config
from .extensions import db, socketio

__version__ = "1.0.0"
log = logging.getLogger(__name__)


def _disable_model_telemetry():
    """Disable telemetry/analytics toggles for model libraries before imports."""
    os.environ['PYANNOTE_METRICS_ENABLED'] = 'false'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['DISABLE_TELEMETRY'] = '1'
    os.environ['DO_NOT_TRACK'] = '1'
    os.environ['WANDB_DISABLED'] = 'true'
    os.environ['OTEL_SDK_DISABLED'] = 'true'
    os.environ['OTEL_METRICS_EXPORTER'] = 'none'
    os.environ['OTEL_TRACES_EXPORTER'] = 'none'
    os.environ['OTEL_LOGS_EXPORTER'] = 'none'

    # pyannote.audio 4.x creates its OTLP telemetry exporter during module
    # import. Replace that module up front so no outbound telemetry can start.
    telemetry_stub = types.ModuleType('pyannote.audio.telemetry')

    def _noop(*args, **kwargs):
        return None

    telemetry_stub.set_opentelemetry_log_level = _noop
    telemetry_stub.set_telemetry_metrics = _noop
    telemetry_stub.track_model_init = _noop
    telemetry_stub.track_pipeline_init = _noop
    telemetry_stub.track_pipeline_apply = _noop
    sys.modules['pyannote.audio.telemetry'] = telemetry_stub
    sys.modules['pyannote.audio.telemetry.metrics'] = telemetry_stub

    try:
        import onnxruntime as ort
        ort.disable_telemetry_events()
    except Exception:
        pass


_disable_model_telemetry()


# TODO: remove or narrow this once Mac STT perf is understood — avoid noisy default
# console in production if we settle on opt-in logging only (PINE_LOG_LEVEL / file).
def _configure_logging_legacy():
    """Send app INFO logs (e.g. PERF: lines in transcription) to stderr.

    Level: env PINE_LOG_LEVEL (DEBUG/INFO/WARNING/ERROR), else INFO for normal
    runs, WARNING when PINE_TESTING=1 to keep pytest output quiet.
    """
    explicit = os.environ.get('PINE_LOG_LEVEL', '').strip()
    if explicit:
        level_name = explicit.upper()
    elif os.environ.get('PINE_TESTING'):
        level_name = 'WARNING'
    else:
        level_name = 'INFO'
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    # PINE is a local desktop app — werkzeug's "use a production server" warning is noise
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    )
    root.addHandler(handler)


def _configure_logging(log_dir):
    """Send app logs to stderr and a per-process file under backend/logs.

    Level: env PINE_LOG_LEVEL (DEBUG/INFO/WARNING/ERROR), else DEBUG for normal
    runs, WARNING when PINE_TESTING=1 to keep pytest output quiet.
    """
    explicit = os.environ.get('PINE_LOG_LEVEL', '').strip()
    if explicit:
        level_name = explicit.upper()
    elif os.environ.get('PINE_TESTING'):
        level_name = 'WARNING'
    else:
        level_name = 'DEBUG'
    level = getattr(logging, level_name, logging.DEBUG)

    today = datetime.now()
    daily_dir = os.path.join(log_dir, today.strftime('%Y-%m-%d'))
    os.makedirs(daily_dir, exist_ok=True)
    session_stamp = today.strftime('%H%M%S')
    session_log = os.path.join(daily_dir, f'app-{session_stamp}-{os.getpid()}.log')

    root = logging.getLogger()
    root.setLevel(level)
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    for handler in list(root.handlers):
        if getattr(handler, '_pine_managed', False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    stream_handler._pine_managed = True  # type: ignore[attr-defined]
    root.addHandler(stream_handler)

    file_handler = logging.FileHandler(session_log, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler._pine_managed = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    root.debug('App log file initialised at %s', session_log)


def _migrate_db(db_path):
    """Add missing columns to existing tables (lightweight schema migration)."""
    if not os.path.isfile(db_path):
        return
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    migrations = [
        ('recordings', 'language', 'TEXT DEFAULT ""'),
        ('recordings', 'transcript_path', 'TEXT DEFAULT ""'),
        ('recordings', 'error_message', 'TEXT DEFAULT ""'),
        ('recordings', 'segment_id', 'INTEGER'),
        ('recordings', 'participant_notes', 'TEXT DEFAULT ""'),
        ('recordings', 'is_linked', 'INTEGER DEFAULT 0'),
        ('recordings', 'num_speakers', 'INTEGER'),
        ('projects', 'results_recommendations', 'TEXT DEFAULT ""'),
        ('projects', 'further_steps', 'TEXT DEFAULT ""'),
        ('projects', 'stakeholders', 'TEXT DEFAULT "[]"'),
        ('projects', 'enabled_sections', 'TEXT DEFAULT "[]"'),
        ('projects', 'methodology', 'TEXT DEFAULT ""'),
        ('projects', 'interview_guide', 'TEXT DEFAULT ""'),
        ('projects', 'key_findings', 'TEXT DEFAULT ""'),
        ('projects', 'recommendations', 'TEXT DEFAULT ""'),
        ('projects', 'enabled_summary_sections', 'TEXT DEFAULT \'["key_findings"]\''),
        ('projects', 'custom_sections', 'TEXT DEFAULT "[]"'),
        ('projects', 'custom_summary_sections', 'TEXT DEFAULT "[]"'),
        ('projects', 'is_system', 'INTEGER DEFAULT 0'),
        ('projects', 'archived_at', 'DATETIME'),
        ('projects', 'default_transcription_language', 'TEXT DEFAULT ""'),
    ]
    for table, column, col_type in migrations:
        try:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}')
        except sqlite3.OperationalError:
            pass
    # Migrate existing summary to results_recommendations
    try:
        cursor.execute(
            "UPDATE projects SET results_recommendations = summary WHERE results_recommendations = '' AND summary != ''"
        )
    except sqlite3.OperationalError:
        pass
    # Migrate results_recommendations to key_findings where key_findings is empty
    try:
        cursor.execute(
            "UPDATE projects SET key_findings = results_recommendations "
            "WHERE (key_findings = '' OR key_findings IS NULL) AND results_recommendations != ''"
        )
    except sqlite3.OperationalError:
        pass
    # Set enabled_summary_sections for projects that don't have it
    try:
        cursor.execute(
            "UPDATE projects SET enabled_summary_sections = '[\"key_findings\"]' "
            "WHERE enabled_summary_sections = '' OR enabled_summary_sections IS NULL"
        )
    except sqlite3.OperationalError:
        pass
    # Indexes on hot foreign keys / status columns. Idempotent and named to match
    # SQLAlchemy's index=True convention so db.create_all() won't duplicate them.
    for stmt in (
        'CREATE INDEX IF NOT EXISTS ix_recordings_project_id ON recordings (project_id)',
        'CREATE INDEX IF NOT EXISTS ix_recordings_transcription_status ON recordings (transcription_status)',
        'CREATE INDEX IF NOT EXISTS ix_segments_project_id ON segments (project_id)',
    ):
        try:
            cursor.execute(stmt)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def _ensure_sqlite_db_path(data_dir, filename, legacy_filename):
    """Use pine.db; one-time rename from legacy port.db if present."""
    path = os.path.join(data_dir, filename)
    legacy = os.path.join(data_dir, legacy_filename)
    if not os.path.isfile(path) and os.path.isfile(legacy):
        try:
            os.rename(legacy, path)
            log.info('Renamed legacy %s to %s', legacy_filename, filename)
        except OSError as e:
            log.error('Could not rename %s to %s: %s', legacy_filename, filename, e)


def _run_startup_checks(app):
    """Run lightweight integrity checks on startup."""
    # 1. SQLite quick_check
    db_path = os.path.join(
        app.config['DATA_DIR'], app.config['SQLITE_DB_FILENAME']
    )
    if os.path.isfile(db_path):
        try:
            conn = sqlite3.connect(db_path)
            result = conn.execute('PRAGMA quick_check;').fetchone()
            conn.close()
            if result and result[0] != 'ok':
                log.critical('SQLite integrity check FAILED: %s', result[0])
            else:
                log.info('SQLite integrity check passed')
        except Exception:
            log.critical('SQLite integrity check error', exc_info=True)

    # 2. Orphan detection — recordings marked transcribed but transcript file missing
    from .models.recording import Recording
    from .models.project import Project
    from .models.setting import Setting
    projects_path = Setting.get('projects_path', app.config['DEFAULT_PROJECTS_PATH'])
    orphans = 0
    for rec in Recording.query.filter_by(transcription_status='transcribed').all():
        if not rec.transcript_path:
            continue
        project = db.session.get(Project, rec.project_id)
        if not project:
            continue
        transcript_file = os.path.join(projects_path, project.folder_name, rec.transcript_path)
        if not os.path.isfile(transcript_file):
            rec.transcription_status = 'error'
            rec.error_message = 'Transcript file missing (detected on startup)'
            orphans += 1
    if orphans:
        db.session.commit()
        log.warning('Found %d recording(s) with missing transcript files', orphans)

def _launch_page_html():
    """Return a launcher page that avoids popup flows on Windows browsers."""
    return (
        '<!DOCTYPE html><html><head><title>PINE</title>'
        '<style>body{margin:0;display:flex;align-items:center;justify-content:center;'
        'height:100vh;font-family:sans-serif;color:#888;background:#1a1a1a}'
        '@media(prefers-color-scheme:light){body{background:#fff;color:#555}}'
        '</style></head><body><p>Starting PINE\u2026</p>'
        '<script>'
        'var isWindows=/Windows|Win32|Win64/i.test(navigator.userAgent||"");'
        'if(isWindows){window.location.replace("/");}'
        'else{var w=window.open("/","pine_app");'
        'if(w){window.close();}else{window.location.replace("/");}}'
        '</script></body></html>'
    )


def create_app(config_class=Config):
    _disable_model_telemetry()
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(backend_dir, 'templates'),
    )
    app.config.from_object(config_class)
    log_dir = os.environ.get('PINE_LOG_DIR') or os.path.join(
        app.config['ROOT_DIR'],
        'backend',
        'logs',
    )
    _configure_logging(log_dir)
    log.info(
        'Starting PINE app pid=%s root_dir=%s data_dir=%s log_dir=%s',
        os.getpid(),
        app.config['ROOT_DIR'],
        app.config['DATA_DIR'],
        log_dir,
    )

    os.makedirs(app.config['DATA_DIR'], exist_ok=True)

    _ensure_sqlite_db_path(
        app.config['DATA_DIR'],
        app.config['SQLITE_DB_FILENAME'],
        app.config['SQLITE_LEGACY_DB_FILENAME'],
    )
    _migrate_db(
        os.path.join(app.config['DATA_DIR'], app.config['SQLITE_DB_FILENAME'])
    )

    from .extensions import ALLOWED_ORIGINS
    CORS(app, origins=ALLOWED_ORIGINS)
    db.init_app(app)
    socketio.init_app(app)

    from .api import onboarding_bp, projects_bp, settings_bp, utils_bp, backup_bp
    app.register_blueprint(onboarding_bp, url_prefix='/api/onboarding')
    app.register_blueprint(projects_bp, url_prefix='/api/projects')
    app.register_blueprint(settings_bp, url_prefix='/api/settings')
    app.register_blueprint(utils_bp, url_prefix='/api/utils')
    app.register_blueprint(backup_bp, url_prefix='/api/backup')

    # ── JSON error handling + error-response logging ──
    # The frontend talks to /api/* and parses JSON; without these handlers an
    # unhandled error returns Flask's HTML error page and the client breaks. We
    # also give the previously-silent API layer a log trail for every 4xx/5xx.
    from werkzeug.exceptions import HTTPException

    @app.errorhandler(HTTPException)
    def _handle_http_exception(e):
        response = jsonify({'error': e.description})
        response.status_code = e.code or 500
        return response

    @app.errorhandler(Exception)
    def _handle_unexpected_exception(e):
        log.exception('Unhandled exception on %s %s', request.method, request.path)
        return jsonify({'error': 'Internal server error'}), 500

    @app.after_request
    def _log_error_responses(response):
        if response.status_code >= 400:
            log.warning('%s %s -> %s', request.method, request.path,
                        response.status_code)
        return response

    with app.app_context():
        from . import models  # noqa: F401 — register all models with SQLAlchemy
        try:
            db.create_all()
        except Exception as e:
            if "already exists" in str(e):
                log.warning("db.create_all() hit 'already exists'; disposing pool and retrying: %s", e)
                db.engine.dispose()
                db.create_all()
            else:
                raise
        from .services.model_manager import (
            init_model_registry,
            reconcile_model_statuses,
            _sync_platform_launcher_layout,
            _refresh_windows_launcher_shortcuts,
        )
        from .models.setting import Setting as _Setting
        from .services.launcher_state import clear_onboarding_complete, mark_onboarding_complete
        init_model_registry()
        _models_path = _Setting.get('models_path', app.config['DEFAULT_MODELS_PATH']) or app.config['DEFAULT_MODELS_PATH']
        reconcile_model_statuses(_models_path)
        if _Setting.get('onboarding_complete', 'false') == 'true':
            mark_onboarding_complete(app.config['ROOT_DIR'])
            _sync_platform_launcher_layout(app.config['ROOT_DIR'])
            _refresh_windows_launcher_shortcuts()
        else:
            clear_onboarding_complete(app.config['ROOT_DIR'])

        from .services.transcription import start_worker, start_watchdog, requeue_interrupted
        start_worker(app)
        start_watchdog(app)
        requeue_interrupted(app)

        from .services.backup_service import start_auto_backup
        start_auto_backup(app)

        # ── Startup integrity checks ──
        _run_startup_checks(app)

        # ── Notify supervisor that the backend is ready ──
        def _notify_supervisor_ready():
            try:
                sup_port = os.environ.get('PINE_SUPERVISOR_PORT', '5001')
                token = os.environ.get('PINE_SUPERVISOR_TOKEN', '')
                req = urllib.request.Request(
                    f'http://127.0.0.1:{sup_port}/backend-ready',
                    method='POST',
                    headers={
                        'Content-Type': 'application/json',
                        'X-Pine-Supervisor-Token': token,
                    },
                )
                urllib.request.urlopen(req, timeout=2)
            except Exception:
                pass  # Supervisor may not be running (e.g. during testing)

        import threading as _thr
        _thr.Thread(target=_notify_supervisor_ready, daemon=True).start()

    @app.context_processor
    def inject_appearance():
        from .models.setting import Setting
        s = Setting.get_many({
            'font_size': '13',
            'font_family': 'inter',
            'theme': 'system',
            'last_open_project_id': '',
        })
        raw_lp = (s.get('last_open_project_id') or '').strip()
        if raw_lp.isdigit():
            init_last_project_id = int(raw_lp)
        else:
            init_last_project_id = None
        return {
            'init_font_size': s['font_size'],
            'init_font_family': s['font_family'],
            'init_theme': s['theme'],
            'init_last_project_id': init_last_project_id,
        }

    @app.context_processor
    def inject_supervisor_config():
        return {
            'supervisor_token': os.environ.get('PINE_SUPERVISOR_TOKEN', ''),
            'supervisor_port': os.environ.get('PINE_SUPERVISOR_PORT', '5001'),
            'backend_port': os.environ.get('PINE_BACKEND_PORT', '5000'),
        }

    @app.route('/')
    def index():
        from .models.setting import Setting
        if Setting.get('onboarding_complete') == 'true':
            return render_template('main.html')
        return render_template('onboarding.html')

    @app.route('/launch')
    def launch():
        """Launcher entry point — opens app via window.open() so the tab can be closed on quit."""
        return _launch_page_html()

    @app.route('/api/health')
    def health():
        """Lightweight readiness probe for the local supervisor/launcher."""
        return {'ok': True, 'version': __version__}

    @app.route('/project/<int:project_id>/tags')
    def tags_page(project_id):
        from .models.setting import Setting
        if Setting.get('onboarding_complete') != 'true':
            return redirect('/')
        return render_template('tags.html', project_id=project_id)

    @app.route('/project/<int:project_id>/tags/manage')
    def manage_tags_page(project_id):
        # Tag/theme editing is now integrated into the Tags screen (inspector).
        # Keep this route as a redirect so old links / bookmarks still work.
        return redirect(f'/project/{project_id}/tags?edit=1')

    @app.route('/project/<int:project_id>/recording/<int:recording_id>')
    def recording_page(project_id, recording_id):
        from .models.setting import Setting
        if Setting.get('onboarding_complete') != 'true':
            return redirect('/')
        from .models.recording import Recording
        rec = Recording.query.get(recording_id)
        file_format = (rec.file_format or '').lower() if rec else ''
        return render_template('recording.html', project_id=project_id, recording_id=recording_id, file_format=file_format)

    @app.route('/api/quit', methods=['POST'])
    def quit_app():
        """Gracefully shut down the server. Call from Quit button."""
        import threading
        payload = request.get_json(silent=True) or {}
        reason = str(payload.get('reason', '')).strip() or 'unknown'
        source = str(payload.get('source', '')).strip() or 'unspecified'
        lease_id = str(payload.get('lease_id', '')).strip()

        def _exit():
            import time
            # Ask supervisor to stop backend and then terminate itself.
            try:
                shutdown_payload = {
                    'reason': reason,
                    'source': source,
                    'lease_id': lease_id,
                }
                sup_port = os.environ.get('PINE_SUPERVISOR_PORT', '5001')
                req = urllib.request.Request(
                    f'http://127.0.0.1:{sup_port}/shutdown',
                    method='POST',
                    data=json.dumps(shutdown_payload).encode('utf-8'),
                    headers={
                        'Content-Type': 'application/json',
                        'X-Pine-Supervisor-Token': os.environ.get('PINE_SUPERVISOR_TOKEN', ''),
                    },
                )
                urllib.request.urlopen(req, timeout=1.5)
            except Exception:
                pass
            time.sleep(0.5)
            from .shutdown import graceful_exit
            graceful_exit(0)
        threading.Thread(target=_exit, daemon=True).start()
        return {'ok': True}

    @app.route('/api/internal/quit-backend', methods=['POST'])
    def quit_backend_only():
        """Stop backend process without asking supervisor to stop."""
        import threading

        def _exit_backend():
            import time
            time.sleep(0.3)
            from .shutdown import graceful_exit
            graceful_exit(0)

        threading.Thread(target=_exit_backend, daemon=True).start()
        return {'ok': True}

    @app.route('/settings')
    def settings_page():
        from .models.setting import Setting
        if Setting.get('onboarding_complete') != 'true':
            return redirect('/')
        return render_template('settings.html')

    return app
