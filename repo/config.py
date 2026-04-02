import os
import json
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
CONFIG_FILE = os.path.join(INSTANCE_DIR, 'config.json')


def _load_or_create_config() -> dict:
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
        # Backfill any keys added after initial config creation
        changed = False
        if 'PAYMENT_SIGNING_KEY' not in cfg:
            cfg['PAYMENT_SIGNING_KEY'] = secrets.token_hex(32)
            changed = True
        if changed:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(cfg, f, indent=2)
        return cfg
    cfg = {
        'SECRET_KEY': secrets.token_hex(32),
        'ENCRYPTION_KEY': secrets.token_hex(32),    # 64 hex chars = 32 bytes
        'PAYMENT_SIGNING_KEY': secrets.token_hex(32),
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
    ADMIN_SEED_PASSWORD: str = 'Admin@123456!'
