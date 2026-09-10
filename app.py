from flask import (Flask, render_template, request, redirect, jsonify, session, url_for,
                   Response, send_file, after_this_request, flash)
import sqlite3
from datetime import datetime, timedelta
from ai_service import (generate_interview_questions, analyze_responses, generate_summary,
                        generate_recommendations)
import gemini_ai
import json
import logging
import csv
import io
import os
import shutil
import tempfile
from functools import wraps
import hashlib
import time
import threading

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key_here')  # Set SECRET_KEY env var in production
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # "Remember me" duration

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "data.db"))

# Unified stakeholder categories used for account registration, login
# authentication ("Login as"), interview participant roles, and session grouping.
USER_CATEGORIES = [
    "Student",
    "Faculty",
    "Applicant",
    "Accounting",
    "Registrar",
    "Cashier",
    "Dean",
    "Department Head/Program Chair",
    "Staff Employee",
    "Guidance Counselor",
    "Staff",
    "Instructor",
    "Owner/Admin",
]

# Display normalization for legacy stakeholder values stored before categories existed
CATEGORY_DISPLAY_MAP = {
    "student": "Student",
    "instructor": "Instructor",
    "": "Unspecified",
    "staff": "Staff",
    "owner": "Owner/Admin",
    "owner/admin": "Owner/Admin",
    "admin": "Owner/Admin",
    "administrator": "Owner/Admin",
    None: "Unspecified",
}

# Staff-level categories: these may EDIT/DELETE any session, including sessions
# created by students. All other categories (Student, Applicant, Accounting,
# Registrar, Cashier) may manage ONLY their own sessions. Everyone who is
# logged in may VIEW all sessions and data. Registration itself stays open
# (no limit on creating accounts).
PRIVILEGED_CATEGORIES = {
    "Dean",
    "Faculty",
    "Staff Employee",
    "Department Head/Program Chair",
    "Guidance Counselor",
    "Staff",
    "Instructor",
    "Owner/Admin",
}

# Categories that may self-register on the public Register page. Accounts for
# Dean, Faculty, Staff and other positions are created by an administrator in
# Manage Accounts (/accounts) so nobody can promote themselves.
REGISTRATION_CATEGORIES = ["Student", "Applicant", "Staff", "Instructor"]

# Owner/Admin bootstrap (env-configurable, never exposed to frontend).
# Set OWNER_USERNAME / OWNER_PASSWORD env vars to override defaults.
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "Dan")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "10231998")
OWNER_ROLE = "Owner/Admin"


# Expose to all templates (used for nav visibility of Manage Accounts)
app.jinja_env.globals['PRIVILEGED_CATEGORIES'] = PRIVILEGED_CATEGORIES


def display_category(raw_role):
    """Normalize a stored stakeholder/category value for display and grouping."""
    if raw_role is None:
        return "Unspecified"
    key = str(raw_role).strip()
    if not key:
        return "Unspecified"
    return CATEGORY_DISPLAY_MAP.get(key.lower(), key)

# ============ LOGGING CONFIGURATION ============
_log_handlers = [logging.StreamHandler()]
try:
    _log_handlers.append(logging.FileHandler(os.path.join(BASE_DIR, 'app.log')))
except OSError:
    pass  # read-only filesystem on some hosts -> stdout logging only
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_log_handlers
)
logger = logging.getLogger(__name__)


@app.after_request
def add_no_cache_headers(response):
    """Never cache HTML pages so the browser back button cannot show logged-in pages after logout."""
    if response.content_type and response.content_type.startswith('text/html'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@app.context_processor
def inject_profile_pic():
    """Make the current user's profile picture available to every template
    (navbar avatar). Reads the DB so picture changes appear without re-login."""
    pic = ""
    uid = session.get('user_id')
    if uid:
        try:
            conn = sqlite3.connect(DATABASE)
            row = conn.execute("SELECT profile_pic FROM users WHERE id = ?", (uid,)).fetchone()
            conn.close()
            pic = (row[0] or "") if row else ""
        except sqlite3.Error:
            pic = ""
    return {'profile_pic': pic}


def _remove_file(path, attempts=20):
    """Delete a file, retrying briefly — Windows/OneDrive can hold a file lock
    for a moment right after a write or after the file was served over HTTP."""
    if not path:
        return False
    for _ in range(attempts):
        try:
            if os.path.isfile(path):
                os.remove(path)
            return True
        except OSError:
            time.sleep(0.1)
    return False


def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # Users table for authentication
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    """)

    # Login log table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            logout_time TIMESTAMP,
            ip_address TEXT,
            status TEXT,
            session_duration INTEGER,
            FOREIGN KEY (username) REFERENCES users(username)
        )
    """)

    # Original records table (for CRUD demo)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT
        )
    """)

    # Interview system tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interview_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            user_role TEXT NOT NULL,
            verifier_name TEXT,
            verifier_date DATE,
            verifier_time TIME,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            status TEXT DEFAULT 'planning'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interview_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            question_type TEXT DEFAULT 'open',
            suggested_by TEXT DEFAULT 'ai',
            category TEXT,
            FOREIGN KEY (session_id) REFERENCES interview_sessions(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interview_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            response_text TEXT,
            response_audio_path TEXT,
            transcription TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            key_points TEXT,
            FOREIGN KEY (session_id) REFERENCES interview_sessions(id),
            FOREIGN KEY (question_id) REFERENCES interview_questions(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interview_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            summary TEXT,
            pain_points TEXT,
            desired_features TEXT,
            recurring_issues TEXT,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES interview_sessions(id)
        )
    """)

    # Settings table (per-user key/value store)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            setting_key TEXT NOT NULL,
            setting_value TEXT,
            UNIQUE(user_id, setting_key),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # Migration: add suggested_solutions column to interview_reports (for existing databases)
    try:
        cursor.execute("ALTER TABLE interview_reports ADD COLUMN suggested_solutions TEXT")
        logger.info("Migration applied: added suggested_solutions column to interview_reports")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: user account category (for existing databases)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT ''")
        logger.info("Migration applied: added role column to users")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: track which user created each interview session (privacy/ownership)
    try:
        cursor.execute("ALTER TABLE interview_sessions ADD COLUMN created_by INTEGER")
        logger.info("Migration applied: added created_by column to interview_sessions")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: automated recommendations / actionable insights per report
    try:
        cursor.execute("ALTER TABLE interview_reports ADD COLUMN recommendations TEXT")
        logger.info("Migration applied: added recommendations column to interview_reports")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: profile picture (relative path inside /static, e.g. uploads/avatars/x.png)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT")
        logger.info("Migration applied: added profile_pic column to users")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: Trash Bin — soft-delete timestamps (NULL = not deleted)
    try:
        cursor.execute("ALTER TABLE interview_sessions ADD COLUMN deleted_at TIMESTAMP")
        logger.info("Migration applied: added deleted_at column to interview_sessions")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE records ADD COLUMN deleted_at TIMESTAMP")
        logger.info("Migration applied: added deleted_at column to records")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Profile pictures live in /static/uploads/avatars
    os.makedirs(os.path.join(BASE_DIR, "static", "uploads", "avatars"), exist_ok=True)

    # Migration: unique auto-generated IDs (client/account + interview) and verifier department
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN account_code TEXT")
        logger.info("Migration applied: added account_code column to users")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE interview_sessions ADD COLUMN interview_code TEXT")
        logger.info("Migration applied: added interview_code column to interview_sessions")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE interview_sessions ADD COLUMN verifier_department TEXT")
        logger.info("Migration applied: added verifier_department column to interview_sessions")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: AI Question Setup — the administrator configures how many
    # respondents and how many questions a session has, plus objectives and
    # instructions. The question count is stored ON the session so every
    # respondent answers exactly the same saved question set.
    try:
        cursor.execute("ALTER TABLE interview_sessions ADD COLUMN num_respondents INTEGER DEFAULT 1")
        logger.info("Migration applied: added num_respondents column to interview_sessions")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE interview_sessions ADD COLUMN num_questions INTEGER")
        logger.info("Migration applied: added num_questions column to interview_sessions")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE interview_sessions ADD COLUMN interview_objectives TEXT")
        logger.info("Migration applied: added interview_objectives column to interview_sessions")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE interview_sessions ADD COLUMN additional_instructions TEXT")
        logger.info("Migration applied: added additional_instructions column to interview_sessions")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: respondent tracking - name and duration for each response
    try:
        cursor.execute("ALTER TABLE interview_responses ADD COLUMN respondent_name TEXT")
        logger.info("Migration applied: added respondent_name column to interview_responses")
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute("ALTER TABLE interview_responses ADD COLUMN response_duration INTEGER DEFAULT 0")
        logger.info("Migration applied: added response_duration column to interview_responses")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: Customer support tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            category TEXT,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    logger.info("Migration applied: created support_tickets table")
    
    # Migration: Add missing columns to existing support_tickets table
    try:
        cursor.execute("ALTER TABLE support_tickets ADD COLUMN priority TEXT DEFAULT 'medium'")
        logger.info("Migration applied: added priority column to support_tickets")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    try:
        cursor.execute("ALTER TABLE support_tickets ADD COLUMN category TEXT")
        logger.info("Migration applied: added category column to support_tickets")
    except sqlite3.OperationalError:
        pass  # Column already exists

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            suggestion_type TEXT,
            suggestion_title TEXT,
            suggestion TEXT NOT NULL,
            benefit TEXT DEFAULT 'my_role',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    logger.info("Migration applied: created system_suggestions table")
    
    # Migration: Add missing columns to existing system_suggestions table
    try:
        cursor.execute("ALTER TABLE system_suggestions ADD COLUMN suggestion_type TEXT")
        logger.info("Migration applied: added suggestion_type column to system_suggestions")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    try:
        cursor.execute("ALTER TABLE system_suggestions ADD COLUMN suggestion_title TEXT")
        logger.info("Migration applied: added suggestion_title column to system_suggestions")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    try:
        cursor.execute("ALTER TABLE system_suggestions ADD COLUMN benefit TEXT DEFAULT 'my_role'")
        logger.info("Migration applied: added benefit column to system_suggestions")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: registration profile fields (Full Name / Student ID / School)
    for _ddl, _label in [
        ("ALTER TABLE users ADD COLUMN full_name TEXT", "full_name"),
        ("ALTER TABLE users ADD COLUMN student_id TEXT", "student_id"),
        ("ALTER TABLE users ADD COLUMN school TEXT", "school"),
    ]:
        try:
            cursor.execute(_ddl)
            logger.info(f"Migration applied: added {_label} column to users")
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Backfill: existing username becomes the display Full Name when empty.
    try:
        cursor.execute("UPDATE users SET full_name = username WHERE full_name IS NULL OR TRIM(full_name) = ''")
    except sqlite3.OperationalError:
        pass

    # Owner/Admin bootstrap — ensures the system owner account exists. The
    # password is stored only as a hash (env-configurable via OWNER_USERNAME /
    # OWNER_PASSWORD) and is never exposed to the frontend or templates.
    try:
        owner = cursor.execute(
            "SELECT id, password, role FROM users WHERE LOWER(username) = LOWER(?)",
            (OWNER_USERNAME,),
        ).fetchone()
        if not owner:
            cursor.execute(
                """
                INSERT INTO users (username, password, email, role, full_name, school)
                VALUES (?, ?, '', ?, ?, 'System Administration')
                """,
                (OWNER_USERNAME, hash_password(OWNER_PASSWORD), OWNER_ROLE, OWNER_USERNAME),
            )
            owner_id = cursor.lastrowid
            account_code = next_code(conn, "users", "account_code", "ACC")
            cursor.execute("UPDATE users SET account_code = ? WHERE id = ?", (account_code, owner_id))
            logger.info(f"Owner/Admin bootstrap: created owner account '{OWNER_USERNAME}'")
        else:
            owner_id, owner_pw, owner_role = owner
            updates = {}
            if not verify_password(OWNER_PASSWORD, owner_pw or ""):
                updates["password"] = hash_password(OWNER_PASSWORD)
            if (owner_role or "") != OWNER_ROLE:
                updates["role"] = OWNER_ROLE
            if updates:
                sets = ", ".join(f"{k} = ?" for k in updates)
                cursor.execute(f"UPDATE users SET {sets} WHERE id = ?", (*updates.values(), owner_id))
                logger.info(f"Owner/Admin bootstrap: repaired owner account '{OWNER_USERNAME}'")
    except sqlite3.Error as exc:
        logger.warning(f"Owner/Admin bootstrap skipped: {exc}")

    conn.commit()
    conn.close()
    logger.info("Database initialization completed successfully")


# ============ UTILITY FUNCTIONS ============

def hash_password(password):
    """Hash a password for storing."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password, hashed_password):
    """Verify a password against its hash."""
    return hash_password(password) == hashed_password


def login_required(f):
    """Decorator to require login for routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            logger.warning(f"Unauthorized access attempt to {request.path}")
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function


def get_user_ip():
    """Get client IP address."""
    if request.environ.get('HTTP_CF_CONNECTING_IP'):
        return request.environ['HTTP_CF_CONNECTING_IP']
    return request.environ.get('REMOTE_ADDR')


def get_setting(user_id, key, default=""):
    """Read a setting value for a user (default is returned if not set)."""
    conn = sqlite3.connect(DATABASE)
    row = conn.execute(
        "SELECT setting_value FROM settings WHERE user_id = ? AND setting_key = ?",
        (user_id, key)
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else default


def set_setting(user_id, key, value):
    """Insert or update a setting value for a user."""
    conn = sqlite3.connect(DATABASE)
    conn.execute(
        """
        INSERT INTO settings (user_id, setting_key, setting_value)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, setting_key)
        DO UPDATE SET setting_value = excluded.setting_value
        """,
        (user_id, key, value or "")
    )
    conn.commit()
    conn.close()


def parse_keywords(text):
    """Turn a comma-separated string into a clean list of keywords."""
    if not text:
        return []
    return [k.strip() for k in text.split(",") if k.strip()]


def build_gemini_cfg(user_id):
    """Build the Gemini AI config for the current user.

    Returned config is passed to ai_service functions. When Gemini is not
    configured/enabled, the config has enabled=False and ai_service
    automatically falls back to the built-in rule-based logic.
    """
    try:
        return gemini_ai.get_config(user_id)
    except Exception as exc:
        logger.warning(f"Gemini config unavailable: {exc}")
        default_model = gemini_ai.DEFAULT_MODEL if gemini_ai else ""
        return {"enabled": False, "api_key": "", "model": default_model, "source": ""}


def next_code(conn, table, column, prefix):
    """Generate a unique sequential code (e.g. ACC-2026-0001, INT-2026-0001)."""
    year = datetime.now().strftime("%Y")
    pattern = f"{prefix}-{year}-%"
    row = conn.execute(
        f"SELECT {column} FROM {table} WHERE {column} LIKE ? ORDER BY {column} DESC LIMIT 1",
        (pattern,),
    ).fetchone()
    if row and (row[0] or "").strip():
        try:
            num = int(row[0].rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            num = 1
    else:
        num = 1
    return f"{prefix}-{year}-{num:04d}"


def get_session_creator(conn, session_id):
    """Return the user id that created an interview session (None for legacy sessions)."""
    row = conn.execute(
        "SELECT created_by FROM interview_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return row[0] if row else None


def session_meta(row):
    """Return readable, index-free view of an interview_sessions row.

    Also safely exposes the newer AI Question Setup columns (num_respondents,
    num_questions, interview_objectives, additional_instructions) for both new
    and legacy sessions. Row length varies because new columns are added by
    additive migrations.
    """
    if row is None:
        return {}
    size = len(row)

    def _val(idx, default=""):
        return row[idx] if idx < size and row[idx] is not None else default

    return {
        'id': row[0],
        'title': row[1],
        'role': row[2],
        'verifier_name': _val(3, ""),
        'verifier_date': _val(4, ""),
        'verifier_time': _val(5, ""),
        'created_at': _val(6, ""),
        'status': _val(8, "planning"),
        'created_by': _val(9, None),
        'interview_code': _val(11, ""),
        'verifier_department': _val(12, ""),
        'num_respondents': _val(13, 1),
        'num_questions': _val(14, 0),
        'objectives': _val(15, ""),
        'instructions': _val(16, ""),
    }


def is_privileged_user():
    """Staff-level categories (Dean, Faculty/instructor, Staff Employee,
    Department Head/Program Chair, Guidance Counselor) may edit/delete ANY
    session, including sessions created by students."""
    return session.get('role') in PRIVILEGED_CATEGORIES


def can_manage_session(creator_id):
    """Permission rule:
    - Staff-level categories (Dean, Faculty, Staff Employee, Dept. Head,
      Guidance Counselor) may manage ANY session (view, edit, conduct,
      complete, delete) — including student-created sessions.
    - All other users may manage ONLY their own sessions; they can still
      VIEW everyone's sessions.
    - Legacy sessions created before this feature (creator is NULL) remain
      manageable by anyone so existing data is not locked out."""
    if creator_id is None:
        return True
    if is_privileged_user():
        return True
    return creator_id == session.get('user_id')


# ============ AUTHENTICATION ROUTES ============

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ip_address = get_user_ip()
        
        logger.info(f"Login attempt for user '{username}' from IP {ip_address}")
        
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id, password, role, username FROM users "
            "WHERE LOWER(username) = LOWER(?) OR (email IS NOT NULL AND email != '' AND LOWER(email) = LOWER(?))",
            (username, username)
        )
        user = cursor.fetchone()
        
        if user and verify_password(password, user[1]):
            user_id = user[0]
            canonical_username = user[3]  # username with its stored casing

            # --- Category authentication: the chosen "Login as" category must
            #     match the category the account was registered with. ---
            stored_role = (user[2] or "").strip()
            chosen_role = request.form.get("login_as", "").strip()

            if stored_role and chosen_role and stored_role != chosen_role:
                # Log the failed (category mismatch) attempt
                cursor.execute(
                    """
                    INSERT INTO login_logs (username, login_time, ip_address, status)
                    VALUES (?, CURRENT_TIMESTAMP, ?, 'failed')
                    """,
                    (canonical_username, ip_address)
                )
                conn.commit()
                conn.close()
                logger.warning(f"Category mismatch for user '{canonical_username}': chose '{chosen_role}', registered as '{stored_role}'")
                return render_template(
                    "login.html",
                    error=(f"Category mismatch: this account is registered as '{stored_role}'. "
                           f"Please login as '{stored_role}'."),
                    categories=USER_CATEGORIES
                )

            # Legacy accounts (no category yet) may only adopt NON-STAFF
            # categories by themselves; staff roles are assigned by an admin
            # in Manage Accounts so nobody can promote themselves.
            if not stored_role and chosen_role:
                if chosen_role in PRIVILEGED_CATEGORIES:
                    cursor.execute(
                        """
                        INSERT INTO login_logs (username, login_time, ip_address, status)
                        VALUES (?, CURRENT_TIMESTAMP, ?, 'failed')
                        """,
                        (canonical_username, ip_address)
                    )
                    conn.commit()
                    conn.close()
                    logger.warning(f"Legacy account '{canonical_username}' tried to self-assign staff category '{chosen_role}'")
                    return render_template(
                        "login.html",
                        error=("Staff/office categories (Dean, Faculty, Staff, Instructor, "
                               "Department Head, Guidance Counselor) can only be assigned by an "
                               "administrator. Please login as Student or Applicant."),
                        categories=USER_CATEGORIES
                    )
                stored_role = chosen_role
                conn.execute("UPDATE users SET role = ? WHERE id = ?", (chosen_role, user_id))

            session['user_id'] = user_id
            session['username'] = canonical_username
            session['role'] = stored_role or "Unspecified"

            # "Remember me" option: keep the user logged in for 30 days
            remember_me = request.form.get("remember_me")
            session.permanent = bool(remember_me)
            
            # Update last login time
            conn.execute(
                "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id,)
            )
            
            # Log successful login
            cursor.execute(
                """
                INSERT INTO login_logs (username, login_time, ip_address, status)
                VALUES (?, CURRENT_TIMESTAMP, ?, 'success')
                """,
                (canonical_username, ip_address)
            )
            conn.commit()
            conn.close()
            
            logger.info(f"User '{canonical_username}' logged in successfully from IP {ip_address}")

            # Optionally remember the account on this device (pre-fills the login form)
            response = redirect("/")
            if remember_me:
                response.set_cookie("remembered_username", canonical_username, max_age=60 * 60 * 24 * 30)
                response.set_cookie("remembered_role", stored_role or chosen_role or "",
                                    max_age=60 * 60 * 24 * 30)
            else:
                response.delete_cookie("remembered_username")
                response.delete_cookie("remembered_role")
            return response
        else:
            # Log failed login attempt
            cursor.execute(
                """
                INSERT INTO login_logs (username, login_time, ip_address, status)
                VALUES (?, CURRENT_TIMESTAMP, ?, 'failed')
                """,
                (username, ip_address)
            )
            conn.commit()
            conn.close()
            
            logger.warning(f"Failed login attempt for user '{username}' from IP {ip_address}")
            return render_template("login.html", error="Invalid username or password", categories=USER_CATEGORIES)
    
    return render_template(
        "login.html",
        categories=USER_CATEGORIES,
        remembered_username=request.cookies.get("remembered_username", ""),
        remembered_role=request.cookies.get("remembered_role", "")
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        student_id = request.form.get("student_id", "").strip()
        email = request.form.get("email", "").strip()
        school = request.form.get("school", "").strip()
        role = request.form.get("role", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        # Backward compatibility: older clients/tests may still post 'username'
        if not full_name:
            full_name = request.form.get("username", "").strip()

        # ---- Field-level validation (errors shown beside each field) ----
        errors = {}
        if not full_name:
            errors["full_name"] = "Full Name is required."
        if role not in REGISTRATION_CATEGORIES:
            errors["role"] = ("Please choose a valid account category "
                              "(Student, Applicant, Staff, or Instructor).")
        if role == "Student" and not student_id:
            errors["student_id"] = "Student ID is required for Student accounts."
        if not email or "@" not in email or "." not in email.split("@")[-1] or " " in email:
            errors["email"] = "Please enter a valid email address (e.g., juan.delacruz@gmail.com)."
        if not school:
            errors["school"] = "School is required."
        if not password:
            errors["password"] = "Password is required."
        elif len(password) < 6:
            errors["password"] = "Password must be at least 6 characters."
        if password and confirm_password != password:
            errors["confirm_password"] = "Passwords do not match."

        form = {"full_name": full_name, "student_id": student_id,
                "email": email, "school": school, "role": role}
        if errors:
            return render_template("register.html", errors=errors, form=form,
                                   categories=REGISTRATION_CATEGORIES)

        # The login username is derived from the Full Name; a numeric suffix is
        # appended automatically when the name is already taken.
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        try:
            base_username = full_name
            username = base_username
            suffix = 2
            while cursor.execute(
                "SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)", (username,)
            ).fetchone():
                username = f"{base_username} {suffix}"
                suffix += 1

            hashed_password = hash_password(password)
            cursor.execute(
                """
                INSERT INTO users (username, password, email, role, full_name, student_id, school)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (username, hashed_password, email, role, full_name, student_id, school)
            )
            new_user_id = cursor.lastrowid
            if new_user_id:
                account_code = next_code(conn, "users", "account_code", "ACC")
                conn.execute("UPDATE users SET account_code = ? WHERE id = ?", (account_code, new_user_id))
            conn.commit()
            conn.close()

            logger.info(f"New user '{username}' registered successfully (category: {role})")
            return redirect("/login")
        except sqlite3.IntegrityError:
            conn.close()
            logger.warning("Registration failed - account already exists")
            return render_template(
                "register.html",
                errors={"full_name": "An account with this Full Name already exists. Try adding your middle name."},
                form=form,
                categories=REGISTRATION_CATEGORIES)

    return render_template("register.html", categories=REGISTRATION_CATEGORIES)


@app.route("/logout")
def logout():
    username = session.get('username', 'Unknown')
    ip_address = get_user_ip()
    
    logger.info(f"User '{username}' logged out from IP {ip_address}")
    
    # Update logout time in login_logs -- best effort, never blocks the logout
    conn = None
    try:
        conn = sqlite3.connect(DATABASE)
        conn.execute(
            """
            UPDATE login_logs 
            SET logout_time = CURRENT_TIMESTAMP 
            WHERE rowid = (
                SELECT rowid FROM login_logs
                WHERE username = ? AND logout_time IS NULL 
                ORDER BY login_time DESC LIMIT 1
            )
            """,
            (username,)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to update logout_time for user '{username}': {e}")
    finally:
        if conn:
            conn.close()
    
    session.clear()
    logger.info(f"User '{username}' session cleared successfully")
    
    # Clear browser cache so the back button cannot show the logged-in pages
    response = redirect(url_for('login'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response



@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Account reset: verify username + registered email, then set a new password."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not username or not email:
            return render_template("forgot_password.html",
                                   error="Please enter both your username and email.")
        if len(new_password) < 6:
            return render_template("forgot_password.html",
                                   error="New password must be at least 6 characters.",
                                   username=username, email=email)
        if new_password != confirm:
            return render_template("forgot_password.html",
                                   error="New passwords do not match.",
                                   username=username, email=email)

        conn = sqlite3.connect(DATABASE)
        user = conn.execute(
            "SELECT id, email FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not user or not user[1] or user[1].strip().lower() != email:
            conn.close()
            logger.warning(f"Password reset failed for '{username}' - username/email did not match")
            return render_template("forgot_password.html",
                                   error="No account matches that username and email.",
                                   username=username)
        conn.execute("UPDATE users SET password = ? WHERE id = ?",
                     (hash_password(new_password), user[0]))
        conn.commit()
        conn.close()
        logger.info(f"Password reset completed for user '{username}'")
        return render_template("forgot_password.html",
                               success="✅ Password reset successful! You can now log in with your new password.")

    return render_template("forgot_password.html")


# ============ ACCOUNT MANAGEMENT (ADMIN ONLY) ============

@app.route("/accounts")
@login_required
def accounts_list():
    """Manage Accounts: only staff-level categories (Dean, Faculty, Staff
    Employee, Department Head, Guidance Counselor) may create/manage user
    accounts. This is where accounts for Dean/Staff/other positions are
    created — the public register page only allows Student/Applicant sign-ups."""
    if not is_privileged_user():
        flash("⛔ Only Dean, Faculty, and Staff accounts can manage user accounts.", "error")
        return redirect("/")

    logger.info(f"User '{session.get('username')}' opened Manage Accounts")
    conn = sqlite3.connect(DATABASE)
    users = conn.execute(
        "SELECT id, username, role, email, created_at, last_login, account_code FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return render_template("accounts.html", users=users, categories=USER_CATEGORIES,
                           current_user_id=session.get('user_id'), active='accounts')


@app.route("/accounts/create", methods=["POST"])
@login_required
def accounts_create():
    if not is_privileged_user():
        flash("⛔ Only Dean, Faculty, and Staff accounts can manage user accounts.", "error")
        return redirect("/")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "").strip()

    if not username or len(password) < 6:
        flash("Username and a password of at least 6 characters are required.", "error")
        return redirect("/accounts")
    if role not in USER_CATEGORIES:
        flash("Please choose a valid account category.", "error")
        return redirect("/accounts")

    conn = sqlite3.connect(DATABASE)
    existing = conn.execute(
        "SELECT username FROM users WHERE LOWER(username) = LOWER(?)", (username,)
    ).fetchone()
    if existing:
        conn.close()
        flash(f"The username '{username}' is already taken (registered as '{existing[0]}').", "error")
        return redirect("/accounts")
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, email, role) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), email, role)
        )
        if cur.lastrowid:
            account_code = next_code(conn, "users", "account_code", "ACC")
            conn.execute("UPDATE users SET account_code = ? WHERE id = ?", (account_code, cur.lastrowid))
        conn.commit()
        flash(f"✅ Account '{username}' ({role}) created. Share the username and password with them.", "success")
        logger.info(f"Account '{username}' ({role}) created by '{session.get('username')}'")
    except sqlite3.IntegrityError:
        flash("That username is already taken. Please choose a different one.", "error")
    finally:
        conn.close()
    return redirect("/accounts")


@app.route("/accounts/<int:user_id>/role", methods=["POST"])
@login_required
def accounts_change_role(user_id):
    if not is_privileged_user():
        flash("⛔ Only Dean, Faculty, and Staff accounts can manage user accounts.", "error")
        return redirect("/")

    new_role = request.form.get("role", "").strip()
    if new_role not in USER_CATEGORIES:
        flash("Please choose a valid account category.", "error")
        return redirect("/accounts")
    if user_id == session.get('user_id') and new_role not in PRIVILEGED_CATEGORIES:
        flash("You cannot change your own category to a non-staff role (you would lose admin access).", "error")
        return redirect("/accounts")

    conn = sqlite3.connect(DATABASE)
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        conn.close()
        flash("Account not found.", "error")
        return redirect("/accounts")
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    conn.commit()
    conn.close()
    flash(f"🏷️ Category for '{row[0]}' set to {new_role}. They must login as '{new_role}'.", "success")
    logger.info(f"Category for '{row[0]}' changed to '{new_role}' by '{session.get('username')}'")
    return redirect("/accounts")


@app.route("/accounts/<int:user_id>/reset-password", methods=["POST"])
@login_required
def accounts_reset_password(user_id):
    if not is_privileged_user():
        flash("⛔ Only Dean, Faculty, and Staff accounts can manage user accounts.", "error")
        return redirect("/")

    new_password = request.form.get("new_password", "")
    if len(new_password) < 6:
        flash("New password must be at least 6 characters.", "error")
        return redirect("/accounts")

    conn = sqlite3.connect(DATABASE)
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        conn.close()
        flash("Account not found.", "error")
        return redirect("/accounts")
    conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(new_password), user_id))
    conn.commit()
    conn.close()
    flash(f"🔑 Password for '{row[0]}' has been reset. Share the new password with them.", "success")
    logger.info(f"Password for '{row[0]}' reset by '{session.get('username')}'")
    return redirect("/accounts")


@app.route("/accounts/<int:user_id>/delete", methods=["POST"])
@login_required
def accounts_delete(user_id):
    if not is_privileged_user():
        flash("⛔ Only Dean, Faculty, and Staff accounts can manage user accounts.", "error")
        return redirect("/")
    if user_id == session.get('user_id'):
        flash("You cannot delete your own account while logged in.", "error")
        return redirect("/accounts")

    conn = sqlite3.connect(DATABASE)
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        conn.close()
        flash("Account not found.", "error")
        return redirect("/accounts")
    conn.execute("DELETE FROM login_logs WHERE username = ?", (row[0],))
    conn.execute("DELETE FROM settings WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    flash(f"🗑️ Account '{row[0]}' deleted.", "success")
    logger.info(f"Account '{row[0]}' deleted by '{session.get('username')}'")
    return redirect("/accounts")


@app.route("/")
@login_required
def home():
    username = session.get('username', 'User')
    logger.info(f"User '{username}' accessed home page")
    
    conn = sqlite3.connect(DATABASE)
    sessions = conn.execute(
        "SELECT * FROM interview_sessions WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    
    # Dashboard stats
    total_sessions = conn.execute(
        "SELECT COUNT(*) FROM interview_sessions WHERE deleted_at IS NULL"
    ).fetchone()[0]
    processed_data = conn.execute(
        "SELECT COUNT(*) FROM interview_responses"
    ).fetchone()[0]
    ai_results = conn.execute(
        "SELECT COUNT(*) FROM interview_reports"
    ).fetchone()[0]
    total_stakeholders = conn.execute(
        "SELECT COUNT(DISTINCT user_role) FROM interview_sessions WHERE deleted_at IS NULL"
    ).fetchone()[0]
    # Status breakdown used for the dashboard KPI trends
    completed_sessions = conn.execute(
        "SELECT COUNT(*) FROM interview_sessions WHERE deleted_at IS NULL AND status = 'completed'"
    ).fetchone()[0]
    in_progress_sessions = conn.execute(
        "SELECT COUNT(*) FROM interview_sessions WHERE deleted_at IS NULL AND status = 'in_progress'"
    ).fetchone()[0]
    planning_sessions = conn.execute(
        "SELECT COUNT(*) FROM interview_sessions WHERE deleted_at IS NULL AND status = 'planning'"
    ).fetchone()[0]
    recent_responses = conn.execute(
        """
        SELECT ir.id, s.title, s.user_role, iq.question_text, ir.response_text, ir.timestamp
        FROM interview_responses ir
        JOIN interview_sessions s ON ir.session_id = s.id
        JOIN interview_questions iq ON ir.question_id = iq.id
        WHERE s.deleted_at IS NULL
        ORDER BY ir.timestamp DESC
        LIMIT 5
        """
    ).fetchall()
    conn.close()

    return render_template(
        "index.html",
        sessions=sessions,
        recent_responses=recent_responses,
        processed_data=processed_data,
        ai_results=ai_results,
        total_sessions=total_sessions,
        total_stakeholders=total_stakeholders,
        completed_sessions=completed_sessions,
        in_progress_sessions=in_progress_sessions,
        planning_sessions=planning_sessions,
        user_id=session.get('user_id'),
        is_privileged=is_privileged_user(),
        active='dashboard'
    )


@app.route("/add", methods=["POST"])
@login_required
def add_data():
    username = session.get('username', 'User')
    name = request.form["name"]
    category = request.form["category"]
    description = request.form["description"]
    
    logger.info(f"User '{username}' adding new record: {name} ({category})")

    conn = sqlite3.connect(DATABASE)

    conn.execute(
        """
        INSERT INTO records (name, category, description)
        VALUES (?, ?, ?)
        """,
        (name, category, description)
    )

    conn.commit()
    conn.close()
    
    logger.info(f"Record added successfully: {name}")

    flash("Record added successfully!", "success")
    return redirect("/")


@app.route("/edit/<int:id>", methods=["GET", "POST"])
@login_required
def edit_data(id):
    username = session.get('username', 'User')
    conn = sqlite3.connect(DATABASE)
    
    if request.method == "POST":
        name = request.form["name"]
        category = request.form["category"]
        description = request.form["description"]
        
        logger.info(f"User '{username}' updating record {id}: {name} ({category})")
        
        conn.execute(
            """
            UPDATE records
            SET name = ?, category = ?, description = ?
            WHERE id = ?
            """,
            (name, category, description, id)
        )
        conn.commit()
        conn.close()
        
        logger.info(f"Record {id} updated successfully")
        return redirect("/")
    
    record = conn.execute(
        "SELECT * FROM records WHERE id = ?", (id,)
    ).fetchone()
    conn.close()
    
    if not record:
        logger.warning(f"Record {id} not found")
        return redirect("/")
    
    return render_template("edit.html", record=record)


@app.route("/delete/<int:id>")
@login_required
def delete_data(id):
    username = session.get('username', 'User')
    logger.info(f"User '{username}' deleting record {id} (moved to Trash Bin)")
    
    conn = sqlite3.connect(DATABASE)
    # Soft delete: keep the row so it can be restored from the Trash Bin.
    conn.execute(
        "UPDATE records SET deleted_at = CURRENT_TIMESTAMP WHERE id = ? AND deleted_at IS NULL",
        (id,)
    )
    conn.commit()
    conn.close()
    
    flash("Record moved to the Trash Bin. You can restore it there.", "success")
    return redirect("/")


# ============ INTERVIEW SYSTEM ROUTES ============

@app.route("/interviews")
@login_required
def interviews_list():
    username = session.get('username', 'User')
    logger.info(f"User '{username}' accessed interviews list")
    
    search_query = request.args.get("q", "").strip()
    
    conn = sqlite3.connect(DATABASE)
    
    if search_query:
        # Search across title, role, verifier name, and interview code
        search_pattern = f"%{search_query}%"
        rows = conn.execute(
            """
            SELECT s.*, u.username
            FROM interview_sessions s
            LEFT JOIN users u ON s.created_by = u.id
            WHERE s.deleted_at IS NULL
              AND (
                  s.title LIKE ? OR
                  s.user_role LIKE ? OR
                  s.verifier_name LIKE ? OR
                  s.interview_code LIKE ? OR
                  CAST(s.id AS TEXT) LIKE ?
              )
            ORDER BY s.created_at DESC
            """,
            (search_pattern, search_pattern, search_pattern, search_pattern, search_pattern)
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT s.*, u.username
            FROM interview_sessions s
            LEFT JOIN users u ON s.created_by = u.id
            WHERE s.deleted_at IS NULL
            ORDER BY s.created_at DESC
            """
        ).fetchall()
    
    conn.close()

    # Build (session, can_manage) pairs — privacy: everyone may view,
    # only the creator may edit/conduct/delete.
    session_items = [(row, can_manage_session(row[9])) for row in rows]

    # Category separation: group counts by normalized stakeholder category
    categories = {}
    for row in rows:
        cat = display_category(row[2])
        categories[cat] = categories.get(cat, 0) + 1

    selected = request.args.get("category", "").strip()
    if selected and selected != "All":
        session_items = [
            (row, can_manage) for row, can_manage in session_items
            if display_category(row[2]) == selected
        ]

    return render_template(
        "interviews_list.html",
        session_items=session_items,
        categories=categories,
        selected_category=selected or "All",
        user_id=session.get('user_id'),
        is_privileged=is_privileged_user(),
        active='history',
        search_query=search_query
    )


@app.route("/interview/new", methods=["GET", "POST"])
@login_required
def new_interview():
    username = session.get('username', 'User')
    user_id = session.get('user_id')
    
    if request.method == "POST":
        title = request.form["title"]
        user_role = request.form["user_role"].strip()
        additional_instructions = request.form.get("additional_instructions", "").strip()
        try:
            num_respondents = max(1, int(request.form.get("num_respondents", "1") or 1))
        except (TypeError, ValueError):
            num_respondents = 1
        try:
            num_questions = int(request.form.get("num_questions", "0") or 0)
        except (TypeError, ValueError):
            num_questions = 0
        if num_questions <= 0:
            num_questions = int(get_setting(user_id, "default_question_count", "5") or 5)
        num_questions = max(1, min(50, num_questions))
        num_respondents = max(1, min(999, num_respondents))

        logger.info(f"User '{username}' creating new interview session: {title}")
        logger.info(f"Interview role: {user_role}, Questions: {num_questions}")

        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        
        cursor.execute(
            """
            INSERT INTO interview_sessions (title, user_role, verifier_name, verifier_date, verifier_time, status, created_by, verifier_department, num_respondents, num_questions, additional_instructions)
            VALUES (?, ?, '', '', '', 'planning', ?, '', ?, ?, ?)
            """,
            (title, user_role, user_id, num_respondents, num_questions, additional_instructions)
        )
        conn.commit()
        session_id = cursor.lastrowid
        # Unique auto-generated Interview ID (e.g. INT-2026-0001)
        if session_id:
            interview_code = next_code(conn, "interview_sessions", "interview_code", "INT")
            conn.execute("UPDATE interview_sessions SET interview_code = ? WHERE id = ?", (interview_code, session_id))
            conn.commit()
        
        # Generate the AI question set for this session. VERY IMPORTANT: the
        # question set is generated ONCE during setup and saved to the database,
        # so every respondent in this interview answers the SAME questions and
        # their responses can be compared and analyzed.
        # The AI uses the interview title and stakeholder role to generate relevant questions.
        extra_context = f"{title}. Stakeholder role: {user_role}"
        questions = generate_interview_questions("basic_gathering", user_role,
                                                 max_questions=num_questions,
                                                 gemini_cfg=build_gemini_cfg(user_id),
                                                 extra_context=extra_context)
        
        for question in questions:
            conn.execute(
                """
                INSERT INTO interview_questions (session_id, question_text, category, suggested_by)
                VALUES (?, ?, ?, 'ai')
                """,
                (session_id, question['text'], question['category'])
            )
        
        conn.commit()
        conn.close()
        
        logger.info(f"Interview session {session_id} created with {len(questions)} questions")
        # Send the admin straight to AI Question Setup to review the generated set.
        return redirect(f"/interview/setup/{session_id}")
    
    # Pre-fill default question count saved in Settings
    default_q = get_setting(user_id, "default_question_count", "5")
    return render_template("interview_new.html", defaults={'num_questions': default_q}, active='setup')


# ============ AI QUESTION SETUP ============
# Centralized question configuration: the administrator decides how many
# questions every respondent answers. The question set is generated ONCE,
# saved to the database, and reused for all respondents in the session.
# Only the creator (or staff-level accounts) may modify the set.

@app.route("/interview/setup/<int:session_id>", methods=["GET", "POST"])
@login_required
def interview_setup(session_id):
    """AI Question Setup - review, edit, regenerate and save the official
    question set for an interview session. Read-only for everyone else."""
    username = session.get('username', 'User')
    conn = sqlite3.connect(DATABASE)
    session_data = conn.execute(
        "SELECT * FROM interview_sessions WHERE id = ? AND deleted_at IS NULL", (session_id,)
    ).fetchone()
    if not session_data:
        conn.close()
        flash("Interview session not found.", "error")
        return redirect("/interviews")

    can_manage = can_manage_session(get_session_creator(conn, session_id))
    questions = conn.execute(
        "SELECT * FROM interview_questions WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall()
    conn.close()

    meta = session_meta(session_data)
    if not meta['num_questions'] or meta['num_questions'] <= 0:
        meta['num_questions'] = len(questions) or 5

    # Pagination: 10 questions per page
    per_page = 10
    total_questions = len(questions)
    total_pages = max(1, (total_questions + per_page - 1) // per_page)
    page = request.args.get('page', 1, type=int)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_questions = questions[start_idx:end_idx]

    logger.info(f"User '{username}' opened AI Question Setup for session {session_id}")
    return render_template("interview_setup.html", sdata=session_data, meta=meta,
                           questions=paginated_questions, all_questions=questions,
                           can_manage=can_manage,
                           is_privileged=is_privileged_user(), active='setup',
                           page=page, total_pages=total_pages,
                           total_questions=total_questions,
                           per_page=per_page)


def _rebuild_question_set(conn, session_id, meta, qty):
    """Delete the current questions and generate a fresh set of exactly `qty`
    questions for the session (single source of truth for the session)."""
    title = meta.get('title') or "Interview"
    role = meta.get('role') or "Stakeholder"
    extra_context = f"{title}. Stakeholder role: {role}"
    conn.execute("DELETE FROM interview_questions WHERE session_id = ?", (session_id,))
    questions = generate_interview_questions(
        "basic_gathering", role,
        max_questions=qty,
        gemini_cfg=build_gemini_cfg(session.get('user_id')),
        extra_context=extra_context)
    for question in questions:
        conn.execute(
            "INSERT INTO interview_questions (session_id, question_text, category, suggested_by) VALUES (?, ?, ?, 'ai')",
            (session_id, question['text'], question['category'])
        )
    return questions


@app.route("/interview/regenerate-questions/<int:session_id>", methods=["POST"])
@login_required
def regenerate_questions(session_id):
    """Regenerate the AI question set using the session's configured count."""
    username = session.get('username', 'User')
    conn = sqlite3.connect(DATABASE)
    session_data = conn.execute(
        "SELECT * FROM interview_sessions WHERE id = ? AND deleted_at IS NULL", (session_id,)
    ).fetchone()
    if not session_data:
        conn.close()
        flash("Interview session not found.", "error")
        return redirect("/interviews")
    if not can_manage_session(get_session_creator(conn, session_id)):
        conn.close()
        flash("Only the creator or staff can regenerate the question set.", "error")
        return redirect(f"/interview/setup/{session_id}")

    meta = session_meta(session_data)
    qty = meta.get('num_questions')
    if not qty or qty <= 0:
        qty = int(request.form.get("num_questions", 5) or 5)
        qty = max(1, min(50, qty))
        conn.execute("UPDATE interview_sessions SET num_questions = ? WHERE id = ?", (qty, session_id))
        conn.commit()
    else:
        qty = max(1, min(50, int(qty)))

    questions = _rebuild_question_set(conn, session_id, meta, qty)
    conn.commit()
    conn.close()
    logger.info(f"Question set regenerated for session {session_id} ({len(questions)} questions) by '{username}'")
    flash(f"AI regenerated a new {len(questions)}-question set. Review and save before starting.", "success")
    return redirect(f"/interview/setup/{session_id}")


@app.route("/interview/update-session/<int:session_id>", methods=["POST"])
@login_required
def update_session_details(session_id):
    """Update respondent count, question count, objectives and instructions."""
    username = session.get('username', 'User')
    conn = sqlite3.connect(DATABASE)
    session_data = conn.execute(
        "SELECT * FROM interview_sessions WHERE id = ? AND deleted_at IS NULL", (session_id,)
    ).fetchone()
    if not session_data:
        conn.close()
        flash("Interview session not found.", "error")
        return redirect("/interviews")
    if not can_manage_session(get_session_creator(conn, session_id)):
        conn.close()
        flash("Only the creator or staff can update this interview.", "error")
        return redirect(f"/interview/setup/{session_id}")

    def _clamp(val, minimum, maximum, fallback):
        try:
            return max(minimum, min(maximum, int(val)))
        except (TypeError, ValueError):
            return fallback

    num_respondents = _clamp(request.form.get("num_respondents"), 1, 999, 1)
    num_questions = _clamp(request.form.get("num_questions"), 1, 50, 5)
    instructions = (request.form.get("additional_instructions") or "").strip()

    conn.execute(
        """UPDATE interview_sessions
           SET num_respondents = ?, num_questions = ?,
               additional_instructions = ?
           WHERE id = ?""",
        (num_respondents, num_questions, instructions, session_id)
    )

    # Keep the question set in sync with the configured count: if the admin
    # changes the number of questions, regenerate exactly that many.
    existing_count = conn.execute(
        "SELECT COUNT(*) FROM interview_questions WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    if existing_count != num_questions:
        meta = session_meta(conn.execute(
            "SELECT * FROM interview_sessions WHERE id = ?", (session_id,)).fetchone())
        questions = _rebuild_question_set(conn, session_id, meta, num_questions)
        generated = len(questions)
    else:
        generated = existing_count

    conn.commit()
    conn.close()
    logger.info(f"Session {session_id} details updated by '{username}' ({num_questions} questions)")
    flash(f"Interview details saved ({generated} questions in the official set).", "success")
    return redirect(f"/interview/setup/{session_id}")


@app.route("/interview/update-questions/<int:session_id>", methods=["POST"])
@login_required
def update_question_set(session_id):
    """Save edited question texts as the official question set for the session."""
    username = session.get('username', 'User')
    conn = sqlite3.connect(DATABASE)
    session_data = conn.execute(
        "SELECT id FROM interview_sessions WHERE id = ? AND deleted_at IS NULL", (session_id,)
    ).fetchone()
    if not session_data:
        conn.close()
        flash("Interview session not found.", "error")
        return redirect("/interviews")
    if not can_manage_session(get_session_creator(conn, session_id)):
        conn.close()
        flash("Only the creator or staff can edit the question set.", "error")
        return redirect(f"/interview/setup/{session_id}")

    question_ids = request.form.getlist("question_ids")
    saved = 0
    for qid in question_ids:
        text = (request.form.get(f"question_{qid}", "") or "").strip()
        if not text:
            continue
        conn.execute(
            "UPDATE interview_questions SET question_text = ? WHERE id = ? AND session_id = ?",
            (text, qid, session_id)
        )
        saved += 1

    # Delete questions the creator marked for removal
    remove_ids = request.form.getlist("remove_question_ids")
    for qid in remove_ids:
        conn.execute(
            "DELETE FROM interview_questions WHERE id = ? AND session_id = ?",
            (qid, session_id)
        )

    # Keep the configured question count in sync with the edited set
    remaining = conn.execute(
        "SELECT COUNT(*) FROM interview_questions WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    remaining = max(1, remaining)
    conn.execute(
        "UPDATE interview_sessions SET num_questions = ? WHERE id = ?",
        (remaining, session_id)
    )

    conn.commit()
    conn.close()
    removed = len(remove_ids)
    if removed:
        logger.info(f"Removed {removed} question(s) from session {session_id} (remaining: {remaining})")
    logger.info(f"Official question set saved for session {session_id} ({saved} questions) by '{username}'")
    if removed:
        flash(f"Saved. Removed {removed} question(s) — the set now has {remaining} questions.", "success")
    else:
        flash("Question set saved. Every respondent will answer exactly these questions.", "success")
    return redirect(f"/interview/setup/{session_id}")


@app.route("/interview/prepare/<int:session_id>")
@login_required
def prepare_interview(session_id):
    username = session.get('username', 'User')
    logger.info(f"User '{username}' preparing interview session {session_id}")
    
    conn = sqlite3.connect(DATABASE)
    session_data = conn.execute(
        "SELECT * FROM interview_sessions WHERE id = ? AND deleted_at IS NULL", (session_id,)
    ).fetchone()
    questions = conn.execute(
        "SELECT * FROM interview_questions WHERE session_id = ? ORDER BY id",
        (session_id,)
    ).fetchall()
    conn.close()
    
    if not session_data:
        logger.warning(f"Interview session {session_id} not found")
        return redirect("/interviews")
    
    logger.info(f"Interview '{session_data[1]}' loaded with {len(questions)} questions")
    return render_template("interview_prepare.html", sdata=session_data, meta=session_meta(session_data),
                           questions=questions, is_privileged=is_privileged_user(), active='setup')


@app.route("/interview/conduct/<int:session_id>")
@login_required
def conduct_interview(session_id):
    username = session.get('username', 'User')
    logger.info(f"User '{username}' starting interview session {session_id}")
    
    conn = sqlite3.connect(DATABASE)
    session_data = conn.execute(
        "SELECT * FROM interview_sessions WHERE id = ? AND deleted_at IS NULL", (session_id,)
    ).fetchone()
    questions = conn.execute(
        "SELECT * FROM interview_questions WHERE session_id = ? ORDER BY id",
        (session_id,)
    ).fetchall()
    
    # Privacy: only the creator can conduct (edit) a session; others may only view it.
    creator_id = get_session_creator(conn, session_id)
    if not can_manage_session(creator_id):
        conn.close()
        flash("🔒 You can view this interview, but only its creator can conduct or edit it.", "error")
        return redirect("/interviews")

    # Update status to in-progress
    conn.execute(
        "UPDATE interview_sessions SET status = 'in_progress' WHERE id = ?",
        (session_id,)
    )
    conn.commit()
    conn.close()
    
    logger.info(f"Interview session {session_id} status updated to 'in_progress'")
    if not session_data:
        logger.warning(f"Interview session {session_id} not found")
        return redirect("/interviews")
    
    return render_template("interview_conduct.html", sdata=session_data, meta=session_meta(session_data),
                           questions=questions, is_privileged=is_privileged_user(), active='sessions')


def _keyword_key_points(response_text, user_id):
    """Instant, offline key-point extraction (no AI call — milliseconds)."""
    pain_words = parse_keywords(get_setting(user_id, "ai_pain_keywords", ""))
    feature_words = parse_keywords(get_setting(user_id, "ai_feature_keywords", ""))
    workflow_words = parse_keywords(get_setting(user_id, "ai_workflow_keywords", ""))
    return analyze_responses([response_text],
                             pain_indicators=pain_words or None,
                             feature_indicators=feature_words or None,
                             workflow_indicators=workflow_words or None)


def _reanalyze_key_points_background(response_id, response_text, user_id):
    """Re-run the AI (Gemini) key-point analysis in a background thread.

    Saving/editing a response must feel instant, so the caller stores
    keyword-based points immediately and this thread quietly upgrades them
    with AI-generated points a few seconds later. Failures are logged and
    never affect the user."""
    def _work():
        try:
            key_points = analyze_responses([response_text],
                                           gemini_cfg=build_gemini_cfg(user_id))
            conn = sqlite3.connect(DATABASE)
            conn.execute(
                "UPDATE interview_responses SET key_points = ? WHERE id = ?",
                (json.dumps(key_points), response_id)
            )
            conn.commit()
            conn.close()
            logger.info(f"Background AI key points saved for response {response_id}")
        except Exception as exc:
            logger.warning(f"Background key-point analysis skipped for response {response_id}: {exc}")
    threading.Thread(target=_work, daemon=True, name=f"keypoints-{response_id}").start()


@app.route("/api/save-response", methods=["POST"])
@login_required
def save_response():
    username = session.get('username', 'User')
    data = request.json
    session_id = data.get("session_id")
    question_id = data.get("question_id")
    response_text = data.get("response_text")
    respondent_name = data.get("respondent_name", "")
    response_duration = data.get("response_duration", 0)
    
    logger.info(f"User '{username}' saving response for session {session_id}, question {question_id}")
    
    conn = sqlite3.connect(DATABASE)

    # Privacy: only the creator can record responses into a session.
    creator_id = get_session_creator(conn, session_id)
    if not can_manage_session(creator_id):
        conn.close()
        return jsonify({"status": "error",
                        "message": "Only the creator of this interview can record responses."}), 403

    cursor = conn.execute(
        """
        INSERT INTO interview_responses (session_id, question_id, response_text, transcription, respondent_name, response_duration)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, question_id, response_text, response_text, respondent_name, response_duration)
    )
    conn.commit()
    response_id = cursor.lastrowid
    
    # Instant keyword-based key points (no AI wait), then upgrade with AI
    # quietly in the background so the interviewer moves on immediately.
    user_id = session.get('user_id')
    key_points = _keyword_key_points(response_text, user_id)
    conn.execute(
        "UPDATE interview_responses SET key_points = ? WHERE id = ?",
        (json.dumps(key_points), response_id)
    )
    conn.commit()
    conn.close()
    
    _reanalyze_key_points_background(response_id, response_text, user_id)
    
    logger.info(f"Response saved successfully for session {session_id}, question {question_id}")
    return jsonify({"status": "success", "key_points": key_points})


@app.route("/api/edit-response", methods=["POST"])
@login_required
def edit_response():
    """Edit an existing response in an interview session.

    Accepts `response_id` (preferred — exact row) or the legacy
    `response_index` (position among the session's responses)."""
    username = session.get('username', 'User')
    data = request.json
    session_id = data.get("session_id")
    response_id = data.get("response_id")
    response_index = data.get("response_index", 0)
    response_text = data.get("response_text")
    
    logger.info(f"User '{username}' editing response for session {session_id}")
    
    if not response_text or not response_text.strip():
        return jsonify({"status": "error", "message": "Response text cannot be empty."}), 400
    
    conn = sqlite3.connect(DATABASE)
    
    # Privacy: only the creator can edit responses
    creator_id = get_session_creator(conn, session_id)
    if not can_manage_session(creator_id):
        conn.close()
        return jsonify({"status": "error", "message": "Only the creator of this interview can edit responses."}), 403
    
    if response_id is not None:
        row = conn.execute(
            "SELECT id FROM interview_responses WHERE id = ? AND session_id = ?",
            (response_id, session_id)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Response not found."}), 404
        response_id = row[0]
    else:
        # Legacy index-based lookup (kept for backward compatibility)
        responses = conn.execute(
            """
            SELECT id FROM interview_responses
            WHERE session_id = ?
            ORDER BY question_id
            """,
            (session_id,)
        ).fetchall()
        if response_index < 0 or response_index >= len(responses):
            conn.close()
            return jsonify({"status": "error", "message": "Invalid response index."}), 400
        response_id = responses[response_index][0]
    
    # Update the response
    conn.execute(
        "UPDATE interview_responses SET response_text = ?, transcription = ? WHERE id = ?",
        (response_text, response_text, response_id)
    )
    
    # Instant keyword-based key points (no AI wait), then upgrade with AI
    # quietly in the background so editing feels immediate.
    user_id = session.get('user_id')
    key_points = _keyword_key_points(response_text, user_id)
    
    conn.execute(
        "UPDATE interview_responses SET key_points = ? WHERE id = ?",
        (json.dumps(key_points), response_id)
    )
    
    conn.commit()
    conn.close()
    
    _reanalyze_key_points_background(response_id, response_text, user_id)
    
    logger.info(f"Response {response_index} updated successfully for session {session_id}")
    return jsonify({"status": "success", "key_points": key_points})


@app.route("/api/delete-response", methods=["POST"])
@login_required
def delete_response():
    """Delete a response from an interview session.

    Accepts `response_id` (preferred — exact row) or the legacy
    `response_index` (position among the session's responses)."""
    username = session.get('username', 'User')
    data = request.json
    session_id = data.get("session_id")
    response_id = data.get("response_id")
    response_index = data.get("response_index", 0)
    
    logger.info(f"User '{username}' deleting response for session {session_id}")
    
    conn = sqlite3.connect(DATABASE)
    
    # Privacy: only the creator can delete responses
    creator_id = get_session_creator(conn, session_id)
    if not can_manage_session(creator_id):
        conn.close()
        return jsonify({"status": "error", "message": "Only the creator of this interview can delete responses."}), 403
    
    if response_id is not None:
        row = conn.execute(
            "SELECT id FROM interview_responses WHERE id = ? AND session_id = ?",
            (response_id, session_id)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Response not found."}), 404
        response_id = row[0]
    else:
        # Legacy index-based lookup (kept for backward compatibility)
        responses = conn.execute(
            """
            SELECT id FROM interview_responses
            WHERE session_id = ?
            ORDER BY question_id
            """,
            (session_id,)
        ).fetchall()
        if response_index < 0 or response_index >= len(responses):
            conn.close()
            return jsonify({"status": "error", "message": "Invalid response index."}), 400
        response_id = responses[response_index][0]
    
    # Delete the response
    conn.execute("DELETE FROM interview_responses WHERE id = ?", (response_id,))
    conn.commit()
    conn.close()
    
    logger.info(f"Response {response_index} deleted successfully for session {session_id}")
    return jsonify({"status": "success", "message": "Response deleted successfully."})


@app.route("/interview/complete/<int:session_id>")
@login_required
def complete_interview(session_id):
    username = session.get('username', 'User')
    logger.info(f"User '{username}' completing interview session {session_id}")
    
    conn = sqlite3.connect(DATABASE)
    
    # Privacy: only the creator can complete (edit) a session; others may only view it.
    creator_id = get_session_creator(conn, session_id)
    if not can_manage_session(creator_id):
        conn.close()
        flash("🔒 You can view this interview, but only its creator can complete or edit it.", "error")
        return redirect("/interviews")

    # Get all responses for this session
    responses = conn.execute(
        """
        SELECT ir.response_text FROM interview_responses ir
        WHERE ir.session_id = ?
        """,
        (session_id,)
    ).fetchall()
    
    response_texts = [r[0] for r in responses]
    
    # Generate summary and analysis
    summary_data = generate_summary(response_texts, gemini_cfg=build_gemini_cfg(session.get('user_id')))

    # Stakeholder category of this session (used to tailor recommendations)
    session_row = conn.execute(
        "SELECT user_role FROM interview_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    session_role = session_row[0] if session_row else None
    
    # Save report (update if one already exists for this session to avoid duplicates)
    existing_report = conn.execute(
        "SELECT id FROM interview_reports WHERE session_id = ?", (session_id,)
    ).fetchone()
    # Automated recommendations / actionable insights
    recommendations = generate_recommendations(
        summary_data.get('pain_points', []),
        summary_data.get('desired_features', []),
        summary_data.get('recurring_issues', []),
        session_role,
        gemini_cfg=build_gemini_cfg(session.get('user_id'))
    )

    report_values = (
        summary_data.get('summary', ''),
        json.dumps(summary_data.get('pain_points', [])),
        json.dumps(summary_data.get('desired_features', [])),
        json.dumps(summary_data.get('recurring_issues', [])),
        json.dumps(summary_data.get('suggested_solutions', [])),
        json.dumps(recommendations)
    )
    if existing_report:
        conn.execute(
            """
            UPDATE interview_reports
            SET summary = ?, pain_points = ?, desired_features = ?, recurring_issues = ?,
                suggested_solutions = ?, recommendations = ?, generated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
            """,
            report_values + (session_id,)
        )
    else:
        conn.execute(
            """
            INSERT INTO interview_reports (session_id, summary, pain_points, desired_features, recurring_issues, suggested_solutions, recommendations)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id,) + report_values
        )
    
    # Update session status
    conn.execute(
        "UPDATE interview_sessions SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (session_id,)
    )
    
    conn.commit()
    conn.close()
    
    logger.info(f"Interview session {session_id} completed and report generated")
    return redirect(f"/interview/report/{session_id}")


@app.route("/interview/report/<int:session_id>")
@login_required
def interview_report(session_id):
    username = session.get('username', 'User')
    logger.info(f"User '{username}' viewing report for interview session {session_id}")
    
    conn = sqlite3.connect(DATABASE)
    session_data = conn.execute(
        "SELECT * FROM interview_sessions WHERE id = ? AND deleted_at IS NULL", (session_id,)
    ).fetchone()
    report = conn.execute(
        "SELECT * FROM interview_reports WHERE session_id = ?", (session_id,)
    ).fetchone()
    responses = conn.execute(
        """
        SELECT ir.id, iq.question_text, ir.response_text, ir.key_points, ir.respondent_name, ir.response_duration, ir.timestamp
        FROM interview_responses ir
        JOIN interview_questions iq ON ir.question_id = iq.id
        WHERE ir.session_id = ?
        ORDER BY ir.id
        """,
        (session_id,)
    ).fetchall()
    can_manage = can_manage_session(get_session_creator(conn, session_id))
    conn.close()
    
    if not session_data:
        logger.warning(f"Interview session {session_id} not found")
        return redirect("/interviews")
    
    pain_points = json.loads(report[3]) if report and report[3] else []
    desired_features = json.loads(report[4]) if report and report[4] else []
    recurring_issues = json.loads(report[5]) if report and report[5] else []

    # Group responses BY QUESTION so the AI analysis and transcript can show
    # every respondent's answer under the exact same question (Q1 -> all
    # respondents, Q2 -> all respondents, ...). This is only meaningful when
    # the same saved question set was used for the whole session.
    grouped_by_question = []
    _current_question = None
    _current_responses = None
    _respondent_name = None
    _total_duration = 0
    for _rid, _rq, _rt, _kp, _rn, _rd, _ts in responses:
        if _current_question is None or _current_question != _rq:
            _current_question = _rq
            _current_responses = []
            grouped_by_question.append({'question': _rq, 'responses': _current_responses})
        try:
            _points = json.loads(_kp) if _kp else []
        except (ValueError, TypeError):
            _points = []
        _current_responses.append({'id': _rid, 'response': _rt or '', 'key_points': _points or []})
        if _rn:
            _respondent_name = _rn
        if _rd:
            _total_duration += _rd

    recommendations = json.loads(report[8]) if report and report[8] else []
    if not recommendations:
        # Legacy reports (created before recommendations existed): generate on the fly
        recommendations = generate_recommendations(
            pain_points, desired_features, recurring_issues,
            session_data[2] if session_data else None,
            gemini_cfg=build_gemini_cfg(session.get('user_id'))
        )

    report_data = {
        'sdata': session_data,
        'meta': session_meta(session_data),
        'report': report,
        'responses': responses,
        'grouped_by_question': grouped_by_question,
        'pain_points': pain_points,
        'desired_features': desired_features,
        'recurring_issues': recurring_issues,
        'suggested_solutions': json.loads(report[7]) if report and report[7] else [],
        'recommendations': recommendations,
        'respondent_name': session_data[3] if session_data and session_data[3] else (_respondent_name or 'Unknown'),
        'response_duration': _total_duration or 'N/A'
    } if report else {'sdata': session_data, 'meta': session_meta(session_data), 'report': None,
                      'responses': responses, 'grouped_by_question': grouped_by_question,
                      'pain_points': [], 'desired_features': [], 'recurring_issues': [],
                      'suggested_solutions': [], 'recommendations': [],
                      'respondent_name': session_data[3] if session_data and session_data[3] else (_respondent_name or 'Unknown'),
                      'response_duration': _total_duration or 'N/A'}
    
    return render_template("interview_report.html", **report_data, can_manage=can_manage)


@app.route("/interview/delete/<int:session_id>")
@login_required
def delete_interview(session_id):
    username = session.get('username', 'User')
    logger.info(f"User '{username}' deleting interview session {session_id} (moved to Trash Bin)")
    
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Privacy: only the creator can delete a session; others may only view it.
    creator_id = get_session_creator(conn, session_id)
    if not can_manage_session(creator_id):
        conn.close()
        flash("🔒 You can view this interview, but only its creator can delete it.", "error")
        return redirect("/interviews")

    # Soft delete: keep everything so the interview can be restored from the
    # Trash Bin. It disappears from every list until restored.
    cursor.execute(
        "UPDATE interview_sessions SET deleted_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND deleted_at IS NULL",
        (session_id,)
    )
    
    conn.commit()
    conn.close()
    
    logger.info(f"Interview session {session_id} moved to Trash Bin by '{username}'")
    flash("Interview moved to the Trash Bin. You can restore it there.", "success")
    return redirect("/interviews")


# ============ TRASH BIN (soft-deleted items) ============

@app.route("/trash")
@login_required
def trash_bin():
    """Show soft-deleted interviews and data records with restore options."""
    username = session.get('username', 'User')
    logger.info(f"User '{username}' opened Trash Bin")

    conn = sqlite3.connect(DATABASE)
    rows = conn.execute(
        """
        SELECT s.*, u.username
        FROM interview_sessions s
        LEFT JOIN users u ON s.created_by = u.id
        WHERE s.deleted_at IS NOT NULL
        ORDER BY s.deleted_at DESC
        """
    ).fetchall()
    deleted_records = conn.execute(
        "SELECT * FROM records WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
    ).fetchall()
    conn.close()

    # Permission: same rule as deleting — creator, staff, or legacy sessions.
    deleted_sessions = [(row, can_manage_session(row[9])) for row in rows]

    return render_template(
        "trash.html",
        deleted_sessions=deleted_sessions,
        deleted_records=deleted_records,
        is_privileged=is_privileged_user(),
        active='trash'
    )


@app.route("/trash/restore/session/<int:session_id>")
@login_required
def trash_restore_session(session_id):
    conn = sqlite3.connect(DATABASE)
    creator_id = get_session_creator(conn, session_id)
    if not can_manage_session(creator_id):
        conn.close()
        flash("🔒 Only the creator or staff can restore this interview.", "error")
        return redirect("/trash")
    conn.execute(
        "UPDATE interview_sessions SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
        (session_id,)
    )
    conn.commit()
    conn.close()
    logger.info(f"Interview session {session_id} restored from Trash by '{session.get('username', 'User')}'")
    flash("Interview restored! It is back in your Interview History.", "success")
    return redirect("/trash")


@app.route("/trash/purge/session/<int:session_id>")
@login_required
def trash_purge_session(session_id):
    """Permanently delete an interview and all of its data."""
    username = session.get('username', 'User')
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    creator_id = get_session_creator(conn, session_id)
    if not can_manage_session(creator_id):
        conn.close()
        flash("🔒 Only the creator or staff can permanently delete this interview.", "error")
        return redirect("/trash")
    cursor.execute("DELETE FROM interview_responses WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM interview_questions WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM interview_reports WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM interview_sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    logger.info(f"Interview session {session_id} permanently deleted by '{username}'")
    flash("Interview permanently deleted.", "success")
    return redirect("/trash")


@app.route("/trash/restore/record/<int:record_id>")
@login_required
def trash_restore_record(record_id):
    conn = sqlite3.connect(DATABASE)
    conn.execute(
        "UPDATE records SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
        (record_id,)
    )
    conn.commit()
    conn.close()
    logger.info(f"Record {record_id} restored from Trash by '{session.get('username', 'User')}'")
    flash("Record restored!", "success")
    return redirect("/trash")


@app.route("/trash/purge/record/<int:record_id>")
@login_required
def trash_purge_record(record_id):
    """Permanently delete a data record."""
    conn = sqlite3.connect(DATABASE)
    conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
    logger.info(f"Record {record_id} permanently deleted by '{session.get('username', 'User')}'")
    flash("Record permanently deleted.", "success")
    return redirect("/trash")


@app.route("/interview/report/<int:session_id>/print")
@login_required
def interview_report_print(session_id):
    """Print-friendly view of an interview summary report."""
    conn = sqlite3.connect(DATABASE)
    session_data = conn.execute(
        "SELECT * FROM interview_sessions WHERE id = ? AND deleted_at IS NULL", (session_id,)
    ).fetchone()
    report = conn.execute(
        "SELECT * FROM interview_reports WHERE session_id = ?", (session_id,)
    ).fetchone()
    responses = conn.execute(
        """
        SELECT iq.question_text, ir.response_text
        FROM interview_responses ir
        JOIN interview_questions iq ON ir.question_id = iq.id
        WHERE ir.session_id = ?
        """,
        (session_id,)
    ).fetchall()
    conn.close()

    if not session_data:
        return redirect("/interviews")

    pain_points = json.loads(report[3]) if report and report[3] else []
    desired_features = json.loads(report[4]) if report and report[4] else []
    recurring_issues = json.loads(report[5]) if report and report[5] else []
    suggested_solutions = json.loads(report[7]) if report and report[7] else []

    recommendations = json.loads(report[8]) if report and report[8] else []
    if not recommendations:
        recommendations = generate_recommendations(
            pain_points, desired_features, recurring_issues,
            session_data[2] if session_data else None,
            gemini_cfg=build_gemini_cfg(session.get('user_id'))
        )

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    return render_template(
        "print_report.html",
        sdata=session_data,
        report=report,
        responses=responses,
        pain_points=pain_points,
        desired_features=desired_features,
        recurring_issues=recurring_issues,
        suggested_solutions=suggested_solutions,
        recommendations=recommendations,
        generated_at=generated_at,
        active='reports'
    )


@app.route("/conduct")
@login_required
def conduct_list():
    """List interview sessions that are ready to be conducted."""
    username = session.get('username', 'User')
    logger.info(f"User '{username}' accessed conduct interview list")
    conn = sqlite3.connect(DATABASE)
    sessions = conn.execute(
        "SELECT * FROM interview_sessions WHERE status != 'completed' AND deleted_at IS NULL ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    # Privacy: only the creator can start/continue/delete; others may only view.
    session_items = [(row, can_manage_session(row[9])) for row in sessions]
    return render_template(
        "conduct.html",
        session_items=session_items,
        sessions=sessions,
        active='sessions'
    )


@app.route("/responses")
@login_required
def responses_list():
    """View all interview responses across sessions."""
    username = session.get('username', 'User')
    logger.info(f"User '{username}' accessed interview responses")
    conn = sqlite3.connect(DATABASE)
    responses = conn.execute(
        """
        SELECT ir.id, s.title, s.user_role, iq.question_text, ir.response_text, ir.timestamp
        FROM interview_responses ir
        JOIN interview_sessions s ON ir.session_id = s.id
        JOIN interview_questions iq ON ir.question_id = iq.id
        WHERE s.deleted_at IS NULL
        ORDER BY ir.timestamp DESC
        """
    ).fetchall()
    total = len(responses)
    conn.close()
    return render_template("responses.html", responses=responses, total=total, active='responses')


@app.route("/stakeholders")
@login_required
def stakeholders():
    """Summarize stakeholders (distinct roles & verifiers) from sessions."""
    username = session.get('username', 'User')
    logger.info(f"User '{username}' accessed stakeholders page")
    conn = sqlite3.connect(DATABASE)
    roles = conn.execute(
        """
        SELECT user_role AS role,
               COUNT(*) AS total_sessions,
               COUNT(DISTINCT verifier_name) AS total_verifiers,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed
        FROM interview_sessions
        WHERE deleted_at IS NULL
        GROUP BY user_role
        ORDER BY total_sessions DESC
        """
    ).fetchall()
    stakeholders_list = conn.execute(
        """
        SELECT DISTINCT verifier_name, COUNT(*) AS session_count
        FROM interview_sessions
        WHERE deleted_at IS NULL AND verifier_name IS NOT NULL AND verifier_name != ''
        GROUP BY verifier_name
        ORDER BY session_count DESC
        """
    ).fetchall()
    conn.close()
    return render_template(
        "stakeholders.html",
        roles=roles,
        stakeholders_list=stakeholders_list,
        active='stakeholders'
    )

@app.route("/analysis")
@login_required
def analysis():
    """Aggregated AI analysis across all interviews."""
    username = session.get('username', 'User')
    logger.info(f"User '{username}' accessed AI analysis")
    conn = sqlite3.connect(DATABASE)

    total_sessions = conn.execute(
        "SELECT COUNT(*) FROM interview_sessions WHERE deleted_at IS NULL").fetchone()[0]
    total_responses = conn.execute("SELECT COUNT(*) FROM interview_responses").fetchone()[0]
    total_reports = conn.execute("SELECT COUNT(*) FROM interview_reports").fetchone()[0]

    pain_points, desired_features, recurring_issues, suggested_solutions, recommendations = [], [], [], [], []
    reports = conn.execute(
        """
        SELECT r.pain_points, r.desired_features, r.recurring_issues,
               r.suggested_solutions, r.recommendations
        FROM interview_reports r
        JOIN interview_sessions s ON r.session_id = s.id
        WHERE s.deleted_at IS NULL
        """
    ).fetchall()
    for r in reports:
        pain_points.extend(json.loads(r[0]) if r[0] else [])
        desired_features.extend(json.loads(r[1]) if r[1] else [])
        recurring_issues.extend(json.loads(r[2]) if r[2] else [])
        suggested_solutions.extend(json.loads(r[3]) if r[3] else [])
        recommendations.extend(json.loads(r[4]) if r[4] else [])

    session_data = conn.execute(
        """
        SELECT s.title, s.status,
               (SELECT COUNT(*) FROM interview_responses ir WHERE ir.session_id = s.id) AS responses
        FROM interview_sessions s
        WHERE s.deleted_at IS NULL
        ORDER BY s.created_at DESC
        """
    ).fetchall()
    conn.close()

    return render_template(
        "analysis.html",
        total_sessions=total_sessions,
        total_responses=total_responses,
        total_reports=total_reports,
        pain_points=pain_points,
        desired_features=desired_features,
        recurring_issues=recurring_issues,
        suggested_solutions=suggested_solutions,
        recommendations=recommendations,
        session_data=session_data,
        active='analysis'
    )


@app.route("/insights")
@login_required
def insights():
    """Derived insights & patterns from all responses."""
    username = session.get('username', 'User')
    logger.info(f"User '{username}' accessed insights & patterns")
    conn = sqlite3.connect(DATABASE)
    responses = conn.execute(
        """
        SELECT response_text FROM interview_responses
        WHERE response_text IS NOT NULL
          AND session_id IN (SELECT id FROM interview_sessions WHERE deleted_at IS NULL)
        """
    ).fetchall()
    sessions = conn.execute(
        """
        SELECT s.title, s.user_role, s.status,
               (SELECT COUNT(*) FROM interview_responses ir WHERE ir.session_id = s.id) AS responses
        FROM interview_sessions s
        WHERE s.deleted_at IS NULL
        """
    ).fetchall()
    conn.close()

    # Simple keyword pattern counting
    patterns = {}
    keywords = {
        "Pain / difficulty": ["difficult", "challenge", "frustrat", "problem", "issue", "hard", "slow"],
        "Desired feature": ["feature", "would help", "need", "should", "want", "improve"],
        "Workflow / process": ["process", "workflow", "step", "procedure", "then"],
        "Frequency / recurring": ["always", "every", "often", "frequently", "repeatedly"],
        "Manual / tedious": ["manual", "paper", "tedious", "typing", "repetitive"],
    }
    all_text = " ".join([r[0] for r in responses if r[0]]).lower()
    for label, words in keywords.items():
        patterns[label] = sum(1 for w in words if w in all_text)

    return render_template(
        "insights.html",
        responses=responses,
        sessions=sessions,
        patterns=patterns,
        total_responses=len(responses),
        active='insights'
    )


@app.route("/requirements")
@login_required
def requirements():
    """Derive functional requirements from reports."""
    username = session.get('username', 'User')
    logger.info(f"User '{username}' accessed requirements page")
    conn = sqlite3.connect(DATABASE)
    rows = conn.execute(
        """
        SELECT s.title, s.user_role, r.desired_features, r.pain_points
        FROM interview_reports r
        JOIN interview_sessions s ON r.session_id = s.id
        WHERE s.deleted_at IS NULL
        ORDER BY s.created_at DESC
        """
    ).fetchall()
    conn.close()

    requirements_list = []
    for row in rows:
        title, role, features_json, pains_json = row[0], row[1], row[2], row[3]
        features = json.loads(features_json) if features_json else []
        pains = json.loads(pains_json) if pains_json else []
        for f in features:
            requirements_list.append({
                'interview': title,
                'role': role,
                'type': 'Feature',
                'text': f
            })
        for p in pains:
            requirements_list.append({
                'interview': title,
                'role': role,
                'type': 'Improvement',
                'text': p
            })

    return render_template(
        "requirements.html",
        requirements_list=requirements_list,
        total=len(requirements_list),
        active='requirements'
    )


@app.route("/reports")
@login_required
def reports():
    """List all summary reports with print/download actions."""
    username = session.get('username', 'User')
    logger.info(f"User '{username}' accessed summary reports")
    conn = sqlite3.connect(DATABASE)
    reports = conn.execute(
        """
        SELECT r.id, r.session_id, s.title, s.user_role, s.verifier_name,
               r.generated_at, r.summary, s.interview_code
        FROM interview_reports r
        JOIN interview_sessions s ON r.session_id = s.id
        WHERE s.deleted_at IS NULL
        ORDER BY r.generated_at DESC
        """
    ).fetchall()
    conn.close()
    return render_template("reports.html", reports=reports, active='reports')

@app.route("/interview/report/<int:session_id>/download")
@login_required
def interview_report_download(session_id):
    """Download a summary report as a Word-compatible HTML .doc file."""
    username = session.get('username', 'User')
    conn = sqlite3.connect(DATABASE)
    session_data = conn.execute(
        "SELECT * FROM interview_sessions WHERE id = ? AND deleted_at IS NULL", (session_id,)
    ).fetchone()
    report = conn.execute(
        "SELECT * FROM interview_reports WHERE session_id = ?", (session_id,)
    ).fetchone()
    responses = conn.execute(
        """
        SELECT iq.question_text, ir.response_text
        FROM interview_responses ir
        JOIN interview_questions iq ON ir.question_id = iq.id
        WHERE ir.session_id = ?
        """,
        (session_id,)
    ).fetchall()
    conn.close()

    if not session_data or not report:
        return redirect("/reports")

    pain_points = json.loads(report[3]) if report[3] else []
    desired_features = json.loads(report[4]) if report[4] else []
    recurring_issues = json.loads(report[5]) if report[5] else []
    suggested_solutions = json.loads(report[7]) if report[7] else []

    recommendations = json.loads(report[8]) if report and report[8] else []
    if not recommendations:
        recommendations = generate_recommendations(
            pain_points, desired_features, recurring_issues,
            session_data[2] if session_data else None,
            gemini_cfg=build_gemini_cfg(session.get('user_id'))
        )

    html = render_template(
        "download_report.html",
        sdata=session_data,
        report=report,
        responses=responses,
        pain_points=pain_points,
        desired_features=desired_features,
        recurring_issues=recurring_issues,
        suggested_solutions=suggested_solutions,
        recommendations=recommendations,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        user=username
    )
    response = Response(html, mimetype="application/msword")
    filename = f"interview_report_{session_id}.doc"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    logger.info(f"User '{username}' downloaded report for session {session_id}")
    return response


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """My Profile: view/update email, change password, view login history."""
    user_id = session.get('user_id')
    username = session.get('username', 'User')

    conn = sqlite3.connect(DATABASE)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    login_history = conn.execute(
        "SELECT * FROM login_logs WHERE username = ? ORDER BY login_time DESC LIMIT 10",
        (username,)
    ).fetchall()
    conn.close()

    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "email":
            email = request.form.get("email", "").strip()
            conn = sqlite3.connect(DATABASE)
            conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
            conn.commit()
            conn.close()
            flash("Email updated successfully!", "success")
            return redirect(url_for('profile'))
        elif form_type == "password":
            current = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            if not verify_password(current, user[2]):
                flash("Current password is incorrect.", "error")
            elif len(new_password) < 6:
                flash("New password must be at least 6 characters.", "error")
            elif new_password != confirm:
                flash("New passwords do not match.", "error")
            else:
                conn = sqlite3.connect(DATABASE)
                conn.execute("UPDATE users SET password = ? WHERE id = ?",
                             (hash_password(new_password), user_id))
                conn.commit()
                conn.close()
                flash("Password changed successfully!", "success")
            return redirect(url_for('profile'))

    return render_template("profile.html", user=user, login_history=login_history, active='profile')
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    user_id = session.get('user_id')
    username = session.get('username', 'User')
    
    conn = sqlite3.connect(DATABASE)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    login_history = conn.execute(
        "SELECT * FROM login_logs WHERE username = ? ORDER BY login_time DESC LIMIT 20",
        (username,)
    ).fetchall()
    conn.close()
    
    if request.method == "POST":
        form_type = request.form.get("form_type")
        
        if form_type == "profile":
            email = request.form.get("email", "").strip()
            conn = sqlite3.connect(DATABASE)
            conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
            conn.commit()
            conn.close()
            logger.info(f"User '{username}' updated their email")
            flash("Profile updated successfully!", "success")
            return redirect(url_for('settings_page'))

        elif form_type == "avatar":
            # Profile picture upload (Settings -> Profile Picture)
            file = request.files.get("avatar")
            if file is None or not file.filename:
                flash("Choose an image file first.", "error")
                return redirect(url_for('settings_page'))
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                flash("Profile picture must be a PNG, JPG, GIF, or WEBP image.", "error")
                return redirect(url_for('settings_page'))
            data = file.read()
            if len(data) > 4 * 1024 * 1024:
                flash("Image is too large (maximum 4 MB).", "error")
                return redirect(url_for('settings_page'))
            save_dir = os.path.join(app.root_path, "static", "uploads", "avatars")
            os.makedirs(save_dir, exist_ok=True)
            # Unique filename every time: user id + ms timestamp + random suffix,
            # so replacing a picture always creates a NEW file (and removes the old one).
            import random
            filename = f"avatar_user{user_id}_{int(datetime.now().timestamp() * 1000)}_{random.randint(1000, 9999)}{ext}"
            with open(os.path.join(save_dir, filename), "wb") as out:
                out.write(data)
            # Remove the previous picture file if it exists (retry: OneDrive/Windows
            # can briefly hold a file lock right after a write or serve.}
            old = user[7] if len(user) > 7 else None
            if old:
                _remove_file(os.path.join(app.root_path, "static", old))
            conn = sqlite3.connect(DATABASE)
            conn.execute("UPDATE users SET profile_pic = ? WHERE id = ?",
                         (f"uploads/avatars/{filename}", user_id))
            conn.commit()
            conn.close()
            logger.info(f"User '{username}' updated their profile picture")
            flash("Profile picture updated!", "success")
            return redirect(url_for('settings_page'))

        elif form_type == "avatar_remove":
            old = user[7] if len(user) > 7 else None
            if old:
                _remove_file(os.path.join(app.root_path, "static", old))

        elif form_type == "ai_api":
            # Gemini AI (real AI API) configuration — ADMIN ONLY, SYSTEM-WIDE.
            # One person (e.g. the Dean) sets the key once; every account then
            # receives AI-generated results. Saved under user_id = 0 (global).
            if not is_privileged_user():
                logger.warning(f"User '{username}' tried to change Gemini AI settings without staff rights")
                flash("🔒 Only the administrator (Dean/staff) can configure the Gemini API key.", "error")
                return redirect(url_for('settings_page'))
            enabled = "1" if request.form.get("ai_enabled") == "on" else "0"
            api_key = (request.form.get("api_key") or "").strip()
            model = (request.form.get("ai_model") or "").strip() or gemini_ai.DEFAULT_MODEL
            saved_key = get_setting(0, "ai_api_key", "")
            if api_key:
                set_setting(0, "ai_api_key", api_key)
            elif enabled == "1" and not saved_key:
                set_setting(0, "ai_api_enabled", "0")
                flash("No API key provided — Gemini AI stays disabled.", "error")
                return redirect(url_for('settings_page'))
            set_setting(0, "ai_api_enabled", enabled)
            set_setting(0, "ai_model", model)
            state = "enabled" if enabled == "1" else "disabled"
            logger.info(f"Admin '{username}' updated SYSTEM-WIDE Gemini AI settings (state={state}, model={model})")
            flash(f"Gemini AI settings saved for the WHOLE system! Gemini is now {state} for all accounts.", "success")
            return redirect(url_for('settings_page'))
    
    ai = {
        'pain_keywords': get_setting(user_id, "ai_pain_keywords",
                                     "difficult, challenge, frustrat, problem, issue, slow, manual"),
        'feature_keywords': get_setting(user_id, "ai_feature_keywords",
                                        "feature, would help, need, should, could, want"),
        'workflow_keywords': get_setting(user_id, "ai_workflow_keywords",
                                         "process, workflow, step, then, next"),
    }
    saved_api_key = get_setting(0, "ai_api_key", "")
    ai_api = {
        'enabled': get_setting(0, "ai_api_enabled", "1") == "1" and bool(saved_api_key),
        'has_key': bool(saved_api_key),
        'masked_key': gemini_ai.mask_key(saved_api_key),
        'model': get_setting(0, "ai_model", gemini_ai.DEFAULT_MODEL),
        'is_admin': is_privileged_user(),
    }
    
    return render_template("settings.html", user=user, login_history=login_history,
                           ai=ai, ai_api=ai_api, active='settings',
                           is_admin=is_privileged_user())


@app.route("/customer-support", methods=["GET", "POST"])
@login_required
def customer_support():
    """Customer support page for submitting reports, suggestions, and feedback."""
    username = session.get('username', 'User')
    user_id = session.get('user_id')
    
    if request.method == "POST":
        form_type = request.form.get("form_type")
        
        if form_type == "support_ticket":
            category = request.form.get("category", "").strip()
            subject = request.form.get("subject", "").strip()
            message = request.form.get("message", "").strip()
            priority = request.form.get("priority", "medium").strip()
            
            if not subject or not message:
                flash("Please fill in all required fields.", "error")
                return redirect(url_for('customer_support'))
            
            conn = sqlite3.connect(DATABASE)
            conn.execute(
                """
                INSERT INTO support_tickets (user_id, username, category, subject, message, priority, status)
                VALUES (?, ?, ?, ?, ?, ?, 'open')
                """,
                (user_id, username, category, subject, message, priority)
            )
            conn.commit()
            conn.close()
            
            logger.info(f"User '{username}' submitted support ticket: {subject} (priority: {priority})")
            flash("Your issue has been reported successfully! We'll review it shortly.", "success")
            session['support_success'] = 'ticket'
            return redirect(url_for('customer_support'))
        
        elif form_type == "suggestion":
            suggestion_type = request.form.get("suggestion_type", "").strip()
            suggestion_title = request.form.get("suggestion_title", "").strip()
            suggestion = request.form.get("suggestion", "").strip()
            benefit = request.form.get("benefit", "my_role").strip()
            
            if not suggestion:
                flash("Please enter your suggestion.", "error")
                return redirect(url_for('customer_support'))
            
            conn = sqlite3.connect(DATABASE)
            conn.execute(
                """
                INSERT INTO system_suggestions (user_id, username, suggestion_type, suggestion_title, suggestion, benefit, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
                """,
                (user_id, username, suggestion_type, suggestion_title, suggestion, benefit)
            )
            conn.commit()
            conn.close()
            
            logger.info(f"User '{username}' submitted suggestion: {suggestion_title or suggestion_type}")
            flash("Thank you for your suggestion! We appreciate your feedback.", "success")
            session['support_success'] = 'suggestion'
            return redirect(url_for('customer_support'))
    
    # Get user's previous tickets and suggestions
    success_kind = session.pop('support_success', None)
    conn = sqlite3.connect(DATABASE)
    tickets = conn.execute(
        "SELECT * FROM support_tickets WHERE user_id = ? ORDER BY created_at DESC LIMIT 10",
        (user_id,)
    ).fetchall()
    suggestions = conn.execute(
        "SELECT * FROM system_suggestions WHERE user_id = ? ORDER BY created_at DESC LIMIT 10",
        (user_id,)
    ).fetchall()
    conn.close()
    
    return render_template("customer_support.html", tickets=tickets, suggestions=suggestions,
                          active='support', success_kind=success_kind)



@app.route("/dean-staffs")
@login_required
def dean_staffs():
    """Dean and Staffs page for viewing all reports, tickets, and suggestions."""
    user_id = session.get('user_id')
    
    # Get all support tickets
    conn = sqlite3.connect(DATABASE)
    tickets = conn.execute("""
        SELECT id, username, category, subject, message, priority, status, created_at
        FROM support_tickets
        ORDER BY created_at DESC
    """).fetchall()
    
    # Get all suggestions
    suggestions = conn.execute("""
        SELECT id, username, suggestion_type, suggestion_title, suggestion, benefit, status, created_at
        FROM system_suggestions
        ORDER BY created_at DESC
    """).fetchall()
    
    # Get all interview reports
    reports = conn.execute("""
        SELECT r.id, COALESCE(s.title, 'Untitled Interview') AS title, r.generated_at AS created_at
        FROM interview_reports r
        LEFT JOIN interview_sessions s ON s.id = r.session_id
        ORDER BY r.generated_at DESC
    """).fetchall()
    
    # Get Dean & Staff member directory (everyone except student/applicant accounts)
    staff_members = conn.execute("""
        SELECT id, username, email, role, profile_pic, created_at, account_code
        FROM users
        WHERE role IS NOT NULL AND role != ''
              AND LOWER(role) NOT IN ('student', 'applicant')
        ORDER BY CASE WHEN role = 'Dean' THEN 0 ELSE 1 END, username ASC
    """).fetchall()

    conn.close()
    
    return render_template("dean_staffs.html", tickets=tickets, suggestions=suggestions,
                          reports=reports, staff_members=staff_members, active='dean_staffs')


@app.route("/dean-staffs/clear-ticket/<int:ticket_id>", methods=["POST"])
@login_required
def clear_ticket(ticket_id):
    """Delete a support ticket."""
    conn = sqlite3.connect(DATABASE)
    conn.execute("DELETE FROM support_tickets WHERE id = ?", (ticket_id,))
    conn.commit()
    conn.close()
    flash("Ticket deleted successfully.", "success")
    return redirect(url_for('dean_staffs'))


@app.route("/dean-staffs/clear-suggestion/<int:suggestion_id>", methods=["POST"])
@login_required
def clear_suggestion(suggestion_id):
    """Delete a suggestion."""
    conn = sqlite3.connect(DATABASE)
    conn.execute("DELETE FROM system_suggestions WHERE id = ?", (suggestion_id,))
    conn.commit()
    conn.close()
    flash("Suggestion deleted successfully.", "success")
    return redirect(url_for('dean_staffs'))


@app.route("/dean-staffs/clear-all-tickets", methods=["POST"])
@login_required
def clear_all_tickets():
    """Delete all support tickets."""
    conn = sqlite3.connect(DATABASE)
    conn.execute("DELETE FROM support_tickets")
    conn.commit()
    conn.close()
    flash("All tickets cleared successfully.", "success")
    return redirect(url_for('dean_staffs'))


@app.route("/dean-staffs/clear-all-suggestions", methods=["POST"])
@login_required
def clear_all_suggestions():
    """Delete all suggestions."""
    conn = sqlite3.connect(DATABASE)
    conn.execute("DELETE FROM system_suggestions")
    conn.commit()
    conn.close()
    flash("All suggestions cleared successfully.", "success")
    return redirect(url_for('dean_staffs'))


@app.route("/settings/test-ai", methods=["POST"])

@login_required
def test_ai_connection():
    """AJAX endpoint: test the Gemini API connection with the form's key (or the saved one)."""
    user_id = session.get('user_id')
    api_key = (request.form.get("api_key") or "").strip() \
        or get_setting(0, "ai_api_key", "") or get_setting(user_id, "ai_api_key", "")
    model = (request.form.get("ai_model") or "").strip() \
        or get_setting(0, "ai_model", "") or get_setting(user_id, "ai_model", "") \
        or gemini_ai.DEFAULT_MODEL
    ok, message = gemini_ai.test_connection(api_key=api_key, model=model)
    logger.info(f"User '{session.get('username', 'User')}' tested Gemini connection: "
                f"{'OK' if ok else message}")
    return jsonify({"ok": ok, "message": message})


# ============ EXPORT / DATA MANAGEMENT ROUTES ============

@app.route("/export/records/csv")
@login_required
def export_records_csv():
    conn = sqlite3.connect(DATABASE)
    rows = conn.execute(
        "SELECT id, name, category, description FROM records WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Category", "Description"])
    for row in rows:
        writer.writerow([row[0], row[1], row[2], row[3] or ""])
    
    response = Response('\ufeff' + output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=records.csv"
    logger.info(f"User '{session.get('username')}' exported records to CSV ({len(rows)} rows)")
    return response


@app.route("/export/records/print")
@login_required
def export_records_print():
    conn = sqlite3.connect(DATABASE)
    rows = conn.execute(
        "SELECT id, name, category, description FROM records WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
    conn.close()
    return render_template("export_records_print.html", records=rows,
                           generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"))


@app.route("/export/logins/csv")
@login_required
def export_logins_csv():
    username = session.get('username')
    conn = sqlite3.connect(DATABASE)
    rows = conn.execute(
        "SELECT username, login_time, logout_time, ip_address, status, session_duration "
        "FROM login_logs WHERE username = ? ORDER BY login_time DESC",
        (username,)
    ).fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Username", "Login Time", "Logout Time", "IP Address", "Status", "Duration (sec)"])
    for row in rows:
        writer.writerow([row[0], row[1], row[2] or "", row[3] or "", row[4] or "", row[5] or ""])
    
    response = Response('\ufeff' + output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=login_history.csv"
    logger.info(f"User '{username}' exported login history to CSV")
    return response


@app.route("/export/reports/csv")
@login_required
def export_reports_csv():
    conn = sqlite3.connect(DATABASE)
    rows = conn.execute(
        """
        SELECT s.title, s.user_role, s.verifier_name, s.verifier_date, s.verifier_time,
               r.summary, r.pain_points, r.desired_features, r.recurring_issues, r.generated_at
        FROM interview_reports r
        JOIN interview_sessions s ON r.session_id = s.id
        WHERE s.deleted_at IS NULL
        ORDER BY r.generated_at DESC
        """
    ).fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Title", "Role", "Verifier", "Date", "Time", "Summary",
                     "Pain Points", "Desired Features", "Recurring Issues", "Generated At"])
    for row in rows:
        writer.writerow([row[0], row[1] or "", row[2] or "", row[3] or "", row[4] or "",
                         (row[5] or "").replace("\n", " "), row[6] or "", row[7] or "",
                         row[8] or "", row[9] or ""])
    
    response = Response('\ufeff' + output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=interview_reports.csv"
    logger.info(f"User '{session.get('username')}' exported interview reports to CSV ({len(rows)} rows)")
    return response


@app.route("/export/records/word")
@login_required
def export_records_word():
    conn = sqlite3.connect(DATABASE)
    rows = conn.execute(
        "SELECT id, name, category, description FROM records WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
    conn.close()
    
    user = session.get('username', 'User')
    html = render_template("export_records_word.html", records=rows,
                           generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"), user=user)
    
    response = Response(html, mimetype="application/msword")
    response.headers["Content-Disposition"] = "attachment; filename=records.doc"
    logger.info(f"User '{user}' exported records to Word ({len(rows)} rows)")
    return response


@app.route("/export/backup")
@login_required
def export_backup():
    user = session.get('username')
    try:
        db_path = os.path.join(app.root_path, DATABASE)
        
        # Make a temp copy so the live DB isn't locked
        fd, temp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        shutil.copy2(db_path, temp_path)
        
        @after_this_request
        def cleanup(response):
            try:
                os.remove(temp_path)
            except Exception:
                pass
            return response
        
        logger.info(f"User '{user}' downloaded database backup")
        return send_file(temp_path, as_attachment=True, download_name="data_backup.db",
                         mimetype="application/octet-stream")
    except Exception as e:
        logger.error(f"Backup export failed for user '{user}': {e}")
        flash("Backup failed. Please try again.", "error")
        return redirect(url_for('settings_page'))


@app.route("/healthz")
def healthz():
    """Lightweight health check for hosting platforms (Render, etc.)."""
    return jsonify(status="ok"), 200


# Initialize the database at import time so production servers
# (gunicorn runs `app:app` and never executes the __main__ block below)
# still get all tables on first boot. Safe to run repeatedly
# (CREATE TABLE IF NOT EXISTS + guarded migrations).
try:
    init_db()
except Exception as _init_exc:  # never crash the import on a read-only FS
    logger.warning(f"Database auto-init skipped: {_init_exc}")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG", "1") == "1")

