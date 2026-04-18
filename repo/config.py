import os
import json
import secrets


def _generate_bootstrap_password() -> str:
    """Generate a strong local bootstrap password (>= 12 chars)."""
    return f"Adm!n{secrets.token_urlsafe(12)}A1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
CONFIG_FILE = os.path.join(INSTANCE_DIR, 'config.json')


def _load_or_create_config() -> dict:
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    env_admin_pw = os.environ.get('PEX_ADMIN_BOOTSTRAP_PASSWORD', '').strip()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
        # Backfill any keys added after initial config creation
        changed = False
        if 'PAYMENT_SIGNING_KEY' not in cfg:
            cfg['PAYMENT_SIGNING_KEY'] = secrets.token_hex(32)
            changed = True
        if 'ADMIN_BOOTSTRAP_PASSWORD' not in cfg:
            cfg['ADMIN_BOOTSTRAP_PASSWORD'] = (
                env_admin_pw or _generate_bootstrap_password()
            )
            changed = True
        elif env_admin_pw and cfg['ADMIN_BOOTSTRAP_PASSWORD'] != env_admin_pw:
            # An explicit env override replaces any previously generated one
            # so the documented demo password stays authoritative across
            # container restarts.
            cfg['ADMIN_BOOTSTRAP_PASSWORD'] = env_admin_pw
            changed = True
        if changed:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(cfg, f, indent=2)
        return cfg
    cfg = {
        'SECRET_KEY': secrets.token_hex(32),
        'ENCRYPTION_KEY': secrets.token_hex(32),    # 64 hex chars = 32 bytes
        'PAYMENT_SIGNING_KEY': secrets.token_hex(32),
        'ADMIN_BOOTSTRAP_PASSWORD': (
            env_admin_pw or _generate_bootstrap_password()
        ),
    }
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)
    return cfg


_cfg = _load_or_create_config()


class Config:
    SECRET_KEY: str = _cfg['SECRET_KEY']
    ENCRYPTION_KEY: bytes = bytes.fromhex(_cfg['ENCRYPTION_KEY'])
    PAYMENT_SIGNING_KEY: bytes = bytes.fromhex(_cfg['PAYMENT_SIGNING_KEY'])
    DATABASE_PATH: str = os.path.join(INSTANCE_DIR, 'app.db')
    REPORTS_DIR: str = os.path.join(INSTANCE_DIR, 'reports')

    # Auth
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_DURATION_MINUTES: int = 15
    MIN_PASSWORD_LENGTH: int = 12
    JWT_EXPIRY_HOURS: int = 24

    # Admin seed (changed after first login)
    ADMIN_SEED_USERNAME: str = 'admin'
    ADMIN_SEED_EMAIL: str = 'admin@local.lan'
    ADMIN_SEED_PASSWORD: str = _cfg['ADMIN_BOOTSTRAP_PASSWORD']

    # Force seeded admin password rotation before privileged actions.
    # Set PEX_FORCE_PASSWORD_ROTATION=0 only for controlled test scenarios.
    FORCE_PASSWORD_ROTATION: bool = os.environ.get(
        'PEX_FORCE_PASSWORD_ROTATION', '1'
    ).strip().lower() in ('1', 'true', 'yes', 'on')

    # Secure cookie defaults to True outside localhost HTTP development.
    SESSION_COOKIE_SECURE: bool = os.environ.get(
        'PEX_SESSION_COOKIE_SECURE', '1'
    ).strip().lower() in ('1', 'true', 'yes', 'on')

    # Deterministic demo credentials for documented /README flows.
    # Enabled by default in Docker (docker-compose.yml) so the credentials
    # advertised in README.md are ready to use immediately.
    SEED_DEMO_USERS: bool = os.environ.get(
        'PEX_SEED_DEMO_USERS', '0'
    ).strip().lower() in ('1', 'true', 'yes', 'on')
    DEMO_AUDITOR_USERNAME: str = 'auditor'
    DEMO_AUDITOR_EMAIL: str    = 'auditor@local.lan'
    DEMO_AUDITOR_PASSWORD: str = os.environ.get(
        'PEX_DEMO_AUDITOR_PASSWORD', 'Auditor@Demo123!'
    )
    DEMO_USER_USERNAME: str = 'demo_user'
    DEMO_USER_EMAIL: str    = 'demo@local.lan'
    DEMO_USER_PASSWORD: str = os.environ.get(
        'PEX_DEMO_USER_PASSWORD', 'User@Demo123!'
    )
