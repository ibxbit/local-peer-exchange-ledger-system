"""Flask application factory."""

import os
from flask import Flask
from config import Config
from app.models import init_db


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates'),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static'),
    )
    app.config.from_object(Config)

    # ---------------------------------------------------------------------------
    # Logging Security — Register the redaction and format-string filter
    # ---------------------------------------------------------------------------
    import logging
    from app.core.log_filter import SensitiveDataFilter
    secure_filter = SensitiveDataFilter()

    # Flask/Werkzeug default access logs
    logging.getLogger('werkzeug').addFilter(secure_filter)
    # Uvicorn access logs (if running in Docker/Asgi)
    logging.getLogger('uvicorn.access').addFilter(secure_filter)

    with app.app_context():
        init_db()
        _seed_admin()

    # Register blueprints from the new routes layer
    from app.routes.auth         import auth_bp
    from app.routes.users        import users_bp
    from app.routes.verification import verification_bp
    from app.routes.matching     import matching_bp
    from app.routes.reputation   import reputation_bp
    from app.routes.ledger       import ledger_bp
    from app.routes.admin        import admin_bp
    from app.routes.audit        import audit_bp
    from app.routes.analytics    import analytics_bp
    from app.routes.payments     import payments_bp

    app.register_blueprint(auth_bp,         url_prefix='/api/auth')
    app.register_blueprint(users_bp,        url_prefix='/api/users')
    app.register_blueprint(verification_bp, url_prefix='/api/verification')
    app.register_blueprint(matching_bp,     url_prefix='/api/matching')
    app.register_blueprint(reputation_bp,   url_prefix='/api/reputation')
    app.register_blueprint(ledger_bp,       url_prefix='/api/ledger')
    app.register_blueprint(admin_bp,        url_prefix='/api/admin')
    app.register_blueprint(audit_bp,        url_prefix='/api/audit')
    app.register_blueprint(analytics_bp,    url_prefix='/api/analytics')
    app.register_blueprint(payments_bp,     url_prefix='/api/payments')

    # Start background scheduler (skip in test environment or reloader child process)
    if not app.config.get('TESTING') and \
            os.environ.get('WERKZEUG_RUN_MAIN') != 'false':
        from app import scheduler as _sched
        _sched.start(app)

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_spa(path):
        from flask import render_template
        return render_template('index.html')

    return app


def _seed_admin():
    from app.models import db
    from app.utils import hash_password, utcnow
    with db() as conn:
        existing = conn.execute(
            'SELECT id FROM users WHERE username = ?',
            (Config.ADMIN_SEED_USERNAME,)
        ).fetchone()
        if existing:
            return
        now = utcnow()
        must_change = 1 if Config.FORCE_PASSWORD_ROTATION else 0
        conn.execute(
            'INSERT INTO users (username, email, password_hash, role, '
            'is_active, credit_balance, must_change_password, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, 1, 1000.0, ?, ?, ?)',
            (Config.ADMIN_SEED_USERNAME, Config.ADMIN_SEED_EMAIL,
             hash_password(Config.ADMIN_SEED_PASSWORD), 'admin', must_change, now, now)
        )
