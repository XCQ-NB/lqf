import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "tianwang.db")


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'operator',
            user_group TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL DEFAULT 'worker',
            pin_hash TEXT NOT NULL,
            phone TEXT DEFAULT '',
            remark TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY,
            time TEXT NOT NULL,
            user TEXT NOT NULL,
            role TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            op_type TEXT NOT NULL,
            detail TEXT DEFAULT '',
            result TEXT DEFAULT '成功'
        );

        CREATE TABLE IF NOT EXISTS points (
            id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS faults (
            id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sites (
            id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kml_sites (
            id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            meta_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS log_meta (
            project_key TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()

    if cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        ts = now_str()
        cur.execute(
            "INSERT INTO users (username, password_hash, display_name, role, user_group, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("admin", hash_password("admin123"), "系统管理员", "admin", "", ts, ts),
        )
        cur.execute(
            "INSERT INTO users (username, password_hash, display_name, role, user_group, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("operator", hash_password("operator123"), "运维管理员", "operator", "", ts, ts),
        )

    if cur.execute("SELECT COUNT(*) FROM workers").fetchone()[0] == 0:
        ts = now_str()
        defaults = [
            ("施之锋", "team_leader", "1111"),
            ("黄宗耀", "project_manager", "2222"),
            ("何瑞琥", "safety_officer", "3333"),
            ("邹永丰", "worker", "4444"),
            ("胡庆斌", "maintenance", "5555"),
            ("黄晟", "maintenance", "6666"),
            ("林喜锋", "worker", "7777"),
        ]
        for name, role, pin in defaults:
            cur.execute(
                "INSERT INTO workers (name, role, pin_hash, created_at, updated_at) VALUES (?,?,?,?,?)",
                (name, role, hash_password(pin), ts, ts),
            )

    ts = now_str()
    default_projects = [
        ("proj_honggutan", "红谷滩天网", "红谷滩区域天网工程"),
        ("proj_lugang", "南昌县陆港天网", "陆港区域天网工程"),
        ("proj_jinyu", "南昌县禁鱼禁捕", "禁鱼禁捕监控工程"),
        ("proj_qingyunpu", "青云谱天网", "青云谱区域天网工程"),
        ("proj_shehui", "社会资源", "社会资源点位"),
        ("proj_pending1", "待定项目1", "待定项目"),
        ("proj_pending2", "待定项目2", "待定项目"),
        ("proj_pending3", "待定项目3", "待定项目"),
    ]
    for pid, name, remark in default_projects:
        row = cur.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
        item = {
            "id": pid,
            "name": name,
            "customer": "中国联通南昌市分公司",
            "status": "进行中",
            "remark": remark,
        }
        if row:
            cur.execute(
                "UPDATE projects SET data_json=?, updated_at=? WHERE id=?",
                (json.dumps(item, ensure_ascii=False), ts, pid),
            )
        else:
            cur.execute(
                "INSERT INTO projects (id, data_json, created_at, updated_at) VALUES (?,?,?,?)",
                (pid, json.dumps(item, ensure_ascii=False), ts, ts),
            )

    # 施工人员绑定账号使用 worker 角色，避免误获派单权限
    cur.execute(
        "UPDATE users SET role='worker' WHERE user_group LIKE 'worker:%' AND role='site_admin'"
    )

    conn.commit()
    conn.close()


def row_to_dict(row):
    return dict(row) if row else None


def make_id(prefix: str) -> str:
    return f"{prefix}_{int(datetime.now().timestamp() * 1000)}_{secrets.token_hex(3)}"


def list_json_table(table: str, order_by="created_at DESC"):
    conn = get_conn()
    rows = conn.execute(f"SELECT data_json FROM {table} ORDER BY {order_by}").fetchall()
    conn.close()
    return [json.loads(r["data_json"]) for r in rows]


def upsert_json_row(table: str, item_id: str, data: dict):
    conn = get_conn()
    ts = now_str()
    exists = conn.execute(f"SELECT id FROM {table} WHERE id=?", (item_id,)).fetchone()
    if exists:
        conn.execute(
            f"UPDATE {table} SET data_json=?, updated_at=? WHERE id=?",
            (json.dumps(data, ensure_ascii=False), ts, item_id),
        )
    else:
        conn.execute(
            f"INSERT INTO {table} (id, data_json, created_at, updated_at) VALUES (?,?,?,?)",
            (item_id, json.dumps(data, ensure_ascii=False), ts, ts),
        )
    conn.commit()
    conn.close()
