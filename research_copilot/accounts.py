"""Local accounts and revocable cookie sessions; workspace ownership is server-side."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path

from .errors import CopilotError

COOKIE = 'research_session'
SESSION_SECONDS = 7 * 24 * 3600


class Accounts:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'accounts.sqlite3'
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL, normalized TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, salt TEXT NOT NULL, owner INTEGER NOT NULL,
                    created_at REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_owner ON users(owner) WHERE owner=1;
                CREATE TABLE IF NOT EXISTS login_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (identity TEXT NOT NULL, created_at REAL NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def password_hash(password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()

    def register(self, username, password):
        username = username.strip() if isinstance(username, str) else ''
        if not re.fullmatch(r'[\w.@-]{3,40}', username):
            raise CopilotError('username_invalid', '账号需为3—40个字母、数字、中文或 . _ @ -。', 'account')
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise CopilotError('password_invalid', '密码长度为8—128个字符。', 'account')
        salt = secrets.token_hex(16)
        digest = self.password_hash(password, salt)
        uid = uuid.uuid4().hex
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            owner = not db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
            try:
                db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',
                           (uid, username, username.casefold(), digest, salt, int(owner), time.time()))
            except sqlite3.IntegrityError:
                raise CopilotError('username_exists', '这个账号已注册，请登录或换一个账号。', 'account') from None
        user = {'id': uid, 'username': username, 'owner': bool(owner)}
        self.workspace(user)
        return user, self.new_session(uid)

    def login(self, username, password, address='local'):
        normalized = str(username or '').strip().casefold()[:40]
        identity = hashlib.sha256((address + ':' + normalized).encode()).hexdigest()
        current = time.time()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM login_attempts WHERE created_at<?', (current-300,))
            if db.execute('SELECT COUNT(*) FROM login_attempts WHERE identity=?', (identity,)).fetchone()[0] >= 10:
                raise CopilotError('login_rate_limit', '尝试次数过多，请5分钟后再登录。', 'account')
            db.execute('INSERT INTO login_attempts VALUES(?,?)', (identity, current))
            row = db.execute('SELECT * FROM users WHERE normalized=?', (normalized,)).fetchone()
        salt = row['salt'] if row else '00'*16
        valid_password = isinstance(password, str) and 1 <= len(password) <= 128
        candidate = self.password_hash(password if valid_password else '', salt)
        if not row or not valid_password or not hmac.compare_digest(candidate, row['password_hash']):
            raise CopilotError('login_failed', '账号或密码不正确。', 'account')
        with self.connection() as db:
            db.execute('DELETE FROM login_attempts WHERE identity=?', (identity,))
        return self.public(row), self.new_session(row['id'])

    @staticmethod
    def public(row):
        return {'id': row['id'], 'username': row['username'], 'owner': bool(row['owner'])}

    def new_session(self, uid):
        token = secrets.token_urlsafe(40)
        with self.connection() as db:
            db.execute('DELETE FROM login_sessions WHERE expires_at<?', (time.time(),))
            db.execute('INSERT INTO login_sessions VALUES(?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), uid, secrets.token_urlsafe(32), time.time()+SESSION_SECONDS))
        return token

    def resolve(self, cookie):
        try:
            parsed = SimpleCookie(cookie or '')
            token = parsed[COOKIE].value
        except (KeyError, ValueError):
            return None
        if len(token) > 100:
            return None
        with self.connection() as db:
            row = db.execute('''SELECT u.*, s.csrf, s.token_hash FROM login_sessions s
                JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?''',
                (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return {**self.public(row), 'csrf': row['csrf'], 'token_hash': row['token_hash']} if row else None

    def logout(self, session):
        if session:
            with self.connection() as db:
                db.execute('DELETE FROM login_sessions WHERE token_hash=?', (session['token_hash'],))

    def workspace(self, user):
        if user['owner']:
            return self.root
        folder = self.root / 'users' / user['id']
        folder.mkdir(parents=True, exist_ok=True)
        (folder / '.isolated-account').touch(exist_ok=True)
        return folder

    @staticmethod
    def cookie(token='', secure=False):
        return f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_SECONDS if token else 0}' + ('; Secure' if secure else '')
