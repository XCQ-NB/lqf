#!/usr/bin/env python3
import base64
import io
import json
import os
import re
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from functools import wraps

from werkzeug.exceptions import HTTPException

from flask import Flask, g, jsonify, request, send_file, send_from_directory

from db import (
    get_conn,
    hash_password,
    init_db,
    list_json_table,
    make_id,
    now_str,
    row_to_dict,
    upsert_json_row,
)
from cos_storage import backup_database, cos_enabled, ensure_dirs, local_dir, save_bytes, save_upload_file, storage_info

# 与 build_config.js 一致的施工内容清单（计入站点进度）
CONSTRUCTION_BUILDS = [
    "安装位置", "监控方向",
    "光缆布放", "光功率测试",
    "电缆施工", "取电位置", "箱体通电",
    "立杆方式", "支臂安装",
    "摄像头安装", "摄像头序列号", "防雨箱安装", "PING测试记录", "光猫编号",
    "安全检查",
]
BUILD_NAME_ALIASES = {
    "监控区域": "监控方向",
    "取电点开关": "电缆施工",
    "开挖路面": "电缆施工",
    "取电点位置": "取电位置",
    "点位箱体通电": "箱体通电",
    "杆件编码": "立杆方式",
    "支臂": "支臂安装",
    "防水箱": "防雨箱安装",
    "防雨箱": "防雨箱安装",
    "水箱": "箱体通电",
    "水箱安装": "箱体通电",
    "防水箱安装": "防雨箱安装",
    "光缆": "光缆布放",
    "光缆敷设": "光缆布放",
    "布放光缆": "光缆布放",
    "光缆施工": "光缆布放",
    "光缆熔接": "光缆布放",
    "PING测记录": "PING测试记录",
}
BUILD_TO_STAGE = {
    "安装位置": "勘察", "监控方向": "勘察",
    "光缆布放": "光路", "光功率测试": "光路",
    "电缆施工": "取电", "取电位置": "取电", "箱体通电": "取电",
    "立杆方式": "杆件", "支臂安装": "杆件",
    "摄像头安装": "设备", "摄像头序列号": "设备",
    "防雨箱安装": "设备", "PING测试记录": "设备", "光猫编号": "设备",
    "安全检查": "验收",
}
STAGE_BUILDS = {
    "勘察": ["安装位置", "监控方向"],
    "光路": ["光缆布放", "光功率测试"],
    "取电": ["电缆施工", "取电位置", "箱体通电"],
    "杆件": ["立杆方式", "支臂安装"],
    "设备": [
        "摄像头安装", "摄像头序列号", "防雨箱安装", "PING测试记录", "光猫编号"
    ],
    "验收": ["安全检查"],
}
STAGE_LABELS = ["勘察", "光路", "取电", "杆件", "设备", "验收"]
STAGE_DISPLAY_NAMES = {
    "勘察": "勘察阶段",
    "光路": "光缆阶段",
    "取电": "取电阶段",
    "杆件": "杆件阶段",
    "设备": "设备阶段",
    "验收": "验收阶段",
    "done": "全部完成",
    "none": "未开始",
}
NON_SITE_BUILDS = {"故障处理", "设备离线检查"}
POLE_TYPE_VALUES = {
    "新立杆3.5", "新立杆6", "借杆", "壁装",
    "立杆3.5米", "立杆6米", "借墙",
}

from daily_log import (
    PHOTO_EXPORT_PRESETS,
    _find_photo_path,
    _photo_caption,
    build_daily_log_docx,
    build_export_filename,
    fetch_day_uploads,
    get_log_meta,
    save_log_meta,
)
from site_photos_docx import STAGE_ORDER as SITE_STAGE_ORDER, build_site_photos_docx, build_site_photos_filename
from image_compress import MAX_PHOTO_BYTES, compress_image_bytes
from kml_parser import parse_kml
from excel_site_parser import parse_excel_rows

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
UPLOAD_DIR = local_dir("uploads")
OCR_PROXY = os.environ.get("TIANWANG_OCR_PROXY", "")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 上传前允许较大原图，服务端会压缩到 1MB


def ok(data=None, **extra):
    payload = {"success": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return jsonify(payload)


def fail(message, code=400):
    return jsonify({"success": False, "message": message, "error": message}), code


def client_ip():
    return request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or ""


def add_audit(user, role, op_type, detail, result="成功"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_logs (id, time, user, role, ip, op_type, detail, result) VALUES (?,?,?,?,?,?,?,?)",
        (make_id("audit"), now_str(), user or "系统", role or "admin", client_ip(), op_type, detail, result),
    )
    conn.commit()
    conn.close()


def create_token(username):
    token = secrets.token_hex(24)
    conn = get_conn()
    conn.execute(
        "INSERT INTO sessions (token, username, created_at, expires_at) VALUES (?,?,?,?)",
        (
            token,
            username,
            now_str(),
            (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()
    return token


def get_session_user():
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip() if auth.startswith("Bearer ") else request.args.get("token", "")
    if not token:
        return None
    conn = get_conn()
    row = conn.execute("SELECT username FROM sessions WHERE token=? AND expires_at >= ?", (token, now_str())).fetchone()
    conn.close()
    if not row:
        return None
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE username=?", (row["username"],)).fetchone()
    conn.close()
    return row_to_dict(user)


def require_auth(roles=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_session_user()
            if not user:
                return fail("未登录或会话已过期", 401)
            g.current_user = user
            if roles and user["role"] not in roles:
                return fail("权限不足", 403)
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@app.after_request
def cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return resp


@app.errorhandler(Exception)
def handle_exception(exc):
    if isinstance(exc, HTTPException):
        return exc
    import traceback
    traceback.print_exc()
    return fail("服务器繁忙，请稍后重试", 500)


@app.route("/api/ping")
def ping():
    return jsonify({"status": "ok", "version": "2.1-local"})


def resolve_worker_for_user(conn, user):
    group = (user["user_group"] or "").strip()
    if group.startswith("worker:"):
        wid = group.split(":", 1)[1]
        row = conn.execute("SELECT id, name, role FROM workers WHERE id=?", (wid,)).fetchone()
        if row:
            return {"id": row["id"], "name": row["name"], "role": row["role"]}
    row = conn.execute("SELECT id, name, role FROM workers WHERE phone=?", (user["username"],)).fetchone()
    if row:
        return {"id": row["id"], "name": row["name"], "role": row["role"]}
    row = conn.execute("SELECT id, name, role FROM workers WHERE name=?", (user["display_name"],)).fetchone()
    if row:
        return {"id": row["id"], "name": row["name"], "role": row["role"]}
    return {"id": None, "name": user["display_name"], "role": "worker"}


def is_worker_login(user):
    return (user.get("user_group") or "").strip().startswith("worker:")


def user_can_dispatch_orders(user, worker=None):
    """施工人员/队长绑定账号仅可接单与完工回馈，不可创建或改派工单。"""
    if is_worker_login(user):
        return False
    return user.get("role") in ("admin", "operator", "site_admin", "sysadmin", "projadmin", "opadmin")


def user_can_export_daily_log(user, worker=None):
    """队长账号不可导出施工日志。"""
    if worker is None:
        conn = get_conn()
        worker = resolve_worker_for_user(conn, user)
        conn.close()
    return worker.get("role") != "team_leader"


def daily_log_access_denied(user):
    if user_can_export_daily_log(user):
        return None
    return fail("队长账号无权使用施工日志功能", 403)


@app.route("/api/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return fail("请输入用户名和密码")
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or user["password_hash"] != hash_password(password):
        conn.close()
        add_audit(username, "guest", "登录", f"账号 {username} 登录失败", "失败")
        return fail("账号或密码错误")
    worker = resolve_worker_for_user(conn, user)
    conn.close()
    token = create_token(username)
    add_audit(user["display_name"], user["role"], "登录", f"账号 {username} 登录成功")
    return jsonify(
        {
            "success": True,
            "token": token,
            "user": {
                "username": user["username"],
                "displayName": user["display_name"],
                "role": user["role"],
                "group": user["user_group"] or "",
            },
            "worker": worker,
        }
    )


@app.route("/api/workers")
def workers_list():
    conn = get_conn()
    rows = conn.execute("SELECT id, name, role, phone, remark, created_at, updated_at FROM workers ORDER BY id").fetchall()
    conn.close()
    data = [
        {
            "id": r["id"],
            "name": r["name"],
            "role": r["role"],
            "phone": r["phone"] or "",
            "remark": r["remark"] or "",
            "createdAt": r["created_at"],
            "updatedAt": r["updated_at"],
        }
        for r in rows
    ]
    return ok(data)


@app.route("/api/workers", methods=["POST"])
@require_auth(["admin", "operator"])
def workers_save():
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action", "create")
    conn = get_conn()
    ts = now_str()
    audit = None
    try:
        if action == "delete":
            worker_id = data.get("id")
            row = conn.execute("SELECT name FROM workers WHERE id=?", (worker_id,)).fetchone()
            if not row:
                return fail("施工人员不存在", 404)
            conn.execute("DELETE FROM workers WHERE id=?", (worker_id,))
            audit = ("删除施工人员", row["name"])
        else:
            name = (data.get("name") or "").strip()
            role = (data.get("role") or "worker").strip()
            phone = re.sub(r"\D", "", (data.get("phone") or "").strip())
            remark = (data.get("remark") or "").strip()
            worker_id = data.get("id")
            if not name:
                return fail("姓名不能为空")
            if not phone:
                return fail("请填写手机号")
            if not re.fullmatch(r"1\d{10}", phone):
                return fail("请填写正确的11位手机号")
            if action == "create":
                exists = conn.execute("SELECT id FROM workers WHERE name=?", (name,)).fetchone()
                if exists:
                    return fail("施工人员已存在")
                cur = conn.execute(
                    "INSERT INTO workers (name, role, pin_hash, phone, remark, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (name, role, hash_password(""), phone, remark, ts, ts),
                )
                new_worker_id = cur.lastrowid
                if data.get("createLogin", True):
                    login_username = (data.get("loginUsername") or phone).strip()
                    login_password = (data.get("loginPassword") or "").strip()
                    if not login_password:
                        return fail("请设置登录密码")
                    if len(login_password) < 6:
                        return fail("登录密码至少6位")
                    taken = conn.execute("SELECT id FROM users WHERE username=?", (login_username,)).fetchone()
                    if taken:
                        return fail("登录用户名已存在，请换一个")
                    conn.execute(
                        "INSERT INTO users (username, password_hash, display_name, role, user_group, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                        (
                            login_username,
                            hash_password(login_password),
                            name,
                            "worker",
                            f"worker:{new_worker_id}",
                            ts,
                            ts,
                        ),
                    )
                audit = ("新增施工人员", name)
            else:
                row = conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
                if not row:
                    return fail("施工人员不存在", 404)
                pin_hash = row["pin_hash"]
                conn.execute(
                    "UPDATE workers SET name=?, role=?, pin_hash=?, phone=?, remark=?, updated_at=? WHERE id=?",
                    (name, role, pin_hash, phone, remark, ts, worker_id),
                )
                login_password = (data.get("loginPassword") or "").strip()
                if login_password:
                    if len(login_password) < 6:
                        return fail("登录密码至少6位")
                    user_row = conn.execute(
                        "SELECT id FROM users WHERE user_group=?",
                        (f"worker:{worker_id}",),
                    ).fetchone()
                    if user_row:
                        conn.execute(
                            "UPDATE users SET password_hash=?, display_name=?, updated_at=? WHERE id=?",
                            (hash_password(login_password), name, ts, user_row["id"]),
                        )
                    elif data.get("createLogin"):
                        login_username = (data.get("loginUsername") or phone).strip()
                        taken = conn.execute("SELECT id FROM users WHERE username=?", (login_username,)).fetchone()
                        if taken:
                            return fail("登录用户名已存在，请换一个")
                        conn.execute(
                            "INSERT INTO users (username, password_hash, display_name, role, user_group, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                            (
                                login_username,
                                hash_password(login_password),
                                name,
                                "worker",
                                f"worker:{worker_id}",
                                ts,
                                ts,
                            ),
                        )
                audit = ("编辑施工人员", name)
        conn.commit()
    finally:
        conn.close()

    if audit:
        add_audit(g.current_user["display_name"], g.current_user["role"], audit[0], audit[1])
    return ok()


@app.route("/api/users")
@require_auth(["admin"])
def users_list():
    conn = get_conn()
    rows = conn.execute("SELECT id, username, display_name, role, user_group, created_at, updated_at FROM users ORDER BY id").fetchall()
    conn.close()
    return ok(
        [
            {
                "id": r["id"],
                "username": r["username"],
                "displayName": r["display_name"],
                "role": r["role"],
                "group": r["user_group"] or "",
                "createdAt": r["created_at"],
                "updatedAt": r["updated_at"],
            }
            for r in rows
        ]
    )


@app.route("/api/users", methods=["POST"])
@require_auth(["admin"])
def users_save():
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action", "create")
    conn = get_conn()
    ts = now_str()
    audit = None
    try:
        if action == "delete":
            user_id = data.get("id")
            row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                return fail("用户不存在", 404)
            if row["username"] == "admin":
                return fail("不能删除默认管理员")
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            audit = ("删除账号", row["username"])
        else:
            username = (data.get("username") or "").strip()
            password = (data.get("password") or "").strip()
            display_name = (data.get("displayName") or data.get("display_name") or username).strip()
            role = (data.get("role") or "operator").strip()
            group = (data.get("group") or "").strip()
            user_id = data.get("id")
            if not username:
                return fail("用户名不能为空")
            if action == "create" and not password:
                return fail("请设置密码")
            if action == "create":
                exists = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
                if exists:
                    return fail("用户名已存在")
                conn.execute(
                    "INSERT INTO users (username, password_hash, display_name, role, user_group, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (username, hash_password(password), display_name, role, group, ts, ts),
                )
                audit = ("创建账号", username)
            else:
                row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                if not row:
                    return fail("用户不存在", 404)
                pwd_hash = hash_password(password) if password else row["password_hash"]
                conn.execute(
                    "UPDATE users SET username=?, password_hash=?, display_name=?, role=?, user_group=?, updated_at=? WHERE id=?",
                    (username, pwd_hash, display_name, role, group, ts, user_id),
                )
                audit = ("编辑账号", username)
        conn.commit()
    finally:
        conn.close()

    if audit:
        add_audit(g.current_user["display_name"], g.current_user["role"], audit[0], audit[1])
    return ok()


@app.route("/api/audit-logs", methods=["GET", "POST"])
def audit_logs():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        conn = get_conn()
        conn.execute(
            "INSERT INTO audit_logs (id, time, user, role, ip, op_type, detail, result) VALUES (?,?,?,?,?,?,?,?)",
            (
                make_id("audit"),
                data.get("time") or now_str(),
                data.get("user") or "未知",
                data.get("role") or "mobile",
                data.get("ip") or client_ip(),
                data.get("opType") or data.get("op_type") or "操作",
                data.get("detail") or "",
                data.get("result") or "成功",
            ),
        )
        conn.commit()
        conn.close()
        return ok()
    q = (request.args.get("q") or "").strip()
    op_type = (request.args.get("opType") or "").strip()
    limit = min(int(request.args.get("limit") or 200), 1000)
    conn = get_conn()
    sql = "SELECT * FROM audit_logs WHERE 1=1"
    params = []
    if q:
        sql += " AND (user LIKE ? OR detail LIKE ? OR op_type LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if op_type:
        sql += " AND op_type = ?"
        params.append(op_type)
    sql += " ORDER BY time DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return ok([row_to_dict(r) for r in rows])


@app.route("/api/construction-config")
def construction_config():
    return ok(
        {
            "builds": CONSTRUCTION_BUILDS,
            "requiredCount": len(CONSTRUCTION_BUILDS),
            "stages": STAGE_LABELS,
            "stageDisplay": STAGE_DISPLAY_NAMES,
            "stageBuilds": STAGE_BUILDS,
            "nonSiteBuilds": sorted(NON_SITE_BUILDS),
        }
    )


@app.route("/api/points", methods=["GET", "POST"])
def points_api():
    if request.method == "GET":
        all_metas = _load_all_upload_metas()
        points = _list_points_from_uploads(all_metas)
        return ok(points)
    data = request.get_json(force=True, silent=True) or {}
    point_id = data.get("id") or make_id("pt")
    data["id"] = point_id
    data.setdefault("createTime", now_str())
    upsert_json_row("points", point_id, data)
    add_audit(data.get("person") or "手机端", "mobile", "新增点位", data.get("name") or data.get("point") or point_id)
    return ok(_enrich_point(data))


@app.route("/api/points/<point_id>/toggle-complete", methods=["PUT"])
def points_toggle_complete(point_id):
    user = get_session_user()
    if not user:
        return fail("未登录或会话已过期", 401)
    conn = get_conn()
    row = conn.execute("SELECT data_json FROM points WHERE id=?", (point_id,)).fetchone()
    conn.close()
    if not row:
        return fail("点位不存在", 404)
    item = json.loads(row["data_json"])
    item = _enrich_point(item)
    item["constructionStatus"] = "已完工" if item.get("constructionStatus") != "已完工" else "施工中"
    upsert_json_row("points", point_id, item)
    add_audit(user["display_name"], user["role"], "切换点位完工", item.get("name") or point_id)
    return ok(_enrich_point(item))


@app.route("/api/faults", methods=["GET", "POST"])
def faults_api():
    user = get_session_user()
    if request.method == "GET":
        faults = list_json_table("faults")
        if user:
            conn = get_conn()
            linked = resolve_worker_for_user(conn, user)
            conn.close()
            if is_worker_login(user) or linked.get("role") in ("worker", "team_leader"):
                wname = linked.get("name") or ""
                if wname:
                    faults = [f for f in faults if f.get("assignedTo") == wname]
            elif user["role"] not in ("admin", "operator", "site_admin", "sysadmin", "projadmin", "opadmin"):
                worker = request.args.get("workerName")
                if worker:
                    faults = [f for f in faults if f.get("assignedTo") == worker]
        return ok(faults)
    if not user:
        return fail("未登录或会话已过期", 401)
    conn = get_conn()
    linked_worker = resolve_worker_for_user(conn, user)
    worker_name = linked_worker.get("name") or ""
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        payload = {}
    if isinstance(payload, list):
        faults = payload
        action = "update"
    else:
        action = payload.get("action", "update")
        faults = payload.get("faults") or []
    if action == "create":
        conn.close()
        if not user_can_dispatch_orders(user, linked_worker):
            return fail("队长及施工人员账号无权创建派单", 403)
        item = payload.get("fault") or payload
        fault_id = item.get("id") or make_id("WD")
        item["id"] = fault_id
        item.setdefault("status", "待处理")
        item.setdefault("createTime", now_str())
        upsert_json_row("faults", fault_id, item)
        add_audit(user["display_name"], user["role"], "创建工单", fault_id)
        return ok(item)
    if action == "delete":
        conn.close()
        if not user_can_dispatch_orders(user, linked_worker):
            return fail("无权删除工单", 403)
        fault_id = (payload.get("id") or payload.get("faultId") or "").strip()
        if not fault_id:
            return fail("缺少工单 ID", 400)
        conn = get_conn()
        row = conn.execute("SELECT data_json FROM faults WHERE id=?", (fault_id,)).fetchone()
        if not row:
            conn.close()
            return fail("工单不存在", 404)
        item = json.loads(row["data_json"])
        if item.get("status") == "已完成":
            conn.close()
            return fail("已完成工单不可删除", 400)
        hist = item.get("history") or []
        if any(h.get("reason") in ("完工回馈", "回单") for h in hist):
            conn.close()
            return fail("施工人员已提交回馈，不可删除", 400)
        assigned = (item.get("assignedTo") or "").strip()
        status = item.get("status") or ""
        was_redispatched = any(
            h.get("reason") == "派单" and "改派" in (h.get("solution") or "")
            for h in hist
        )
        can_delete = (status == "待处理" and not assigned) or (
            status == "处理中" and assigned and was_redispatched
        )
        if not can_delete:
            conn.close()
            return fail("仅未指定队长的待处理工单或改派后的工单可删除", 400)
        conn.execute("DELETE FROM faults WHERE id=?", (fault_id,))
        conn.commit()
        conn.close()
        add_audit(user["display_name"], user["role"], "删除工单", fault_id)
        return ok({"id": fault_id, "deleted": True})
    if is_worker_login(user):
        existing_map = {f.get("id"): f for f in list_json_table("faults")}
        for item in faults:
            fault_id = item.get("id")
            if not fault_id:
                continue
            existing = existing_map.get(fault_id)
            if not existing:
                conn.close()
                return fail("工单不存在", 404)
            if existing.get("assignedTo") != worker_name:
                conn.close()
                return fail("只能操作指派给您的工单", 403)
            new_assign = (item.get("assignedTo") or "").strip()
            if new_assign and new_assign != worker_name and new_assign != existing.get("assignedTo"):
                conn.close()
                return fail("无权改派工单", 403)
    conn.close()
    for item in faults:
        fault_id = item.get("id")
        if not fault_id:
            continue
        upsert_json_row("faults", fault_id, item)
    add_audit(user["display_name"], user["role"], "更新工单", f"共 {len(faults)} 条")
    return ok(faults)


def _photo_from_upload_row(row) -> dict:
    meta = json.loads(row["meta_json"] or "{}")
    filename = row["filename"]
    url = f"/uploads/{filename}"
    build = _normalize_build_name(meta.get("build") or meta.get("content") or "")
    return {
        "id": row["id"],
        "filename": filename,
        "url": url,
        "cosUrl": meta.get("cosUrl") or "",
        "content": build or meta.get("build") or meta.get("content") or "",
        "build": build or meta.get("build") or "",
        "stage": _build_to_stage(build),
        "point": meta.get("point") or meta.get("name") or "",
        "siteName": (meta.get("point") or meta.get("name") or "未分类站点").strip() or "未分类站点",
        "person": meta.get("person") or "",
        "project": meta.get("project") or "",
        "area": meta.get("area") or "",
        "uploadTime": row["created_at"],
        "meta": meta,
    }


def _match_upload_to_site(meta: dict, site_name: str) -> bool:
    if not site_name:
        return False
    point = (meta.get("point") or meta.get("name") or "").strip()
    if not point:
        return False
    if point == site_name:
        return True
    if site_name in point or point in site_name:
        return True
    return False


def _match_upload_to_point(meta: dict, point_name: str) -> bool:
    """按安装点位精确匹配照片（用于站点管理逐点进度）。"""
    if not point_name:
        return False
    upload_point = (meta.get("point") or meta.get("name") or "").strip()
    return bool(upload_point and upload_point == point_name)


def _load_all_upload_metas() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT meta_json FROM uploads ORDER BY created_at DESC").fetchall()
    conn.close()
    return [json.loads(row["meta_json"] or "{}") for row in rows]


def _compute_progress_from_metas(metas: list) -> dict:
    completed = set()
    total_photos = 0
    for meta in metas:
        total_photos += 1
        build = _normalize_build_name(meta.get("build") or meta.get("content") or "")
        if build in CONSTRUCTION_BUILDS:
            completed.add(build)
    stage_progress = {}
    for stage, builds in STAGE_BUILDS.items():
        done = sum(1 for b in builds if b in completed)
        stage_progress[stage] = {
            "done": done,
            "total": len(builds),
            "complete": done >= len(builds) and len(builds) > 0,
        }
    return {
        "completedBuilds": sorted(completed),
        "photoCount": len(completed),
        "totalPhotos": total_photos,
        "requiredCount": len(CONSTRUCTION_BUILDS),
        "stageProgress": stage_progress,
    }


@app.route("/api/uploads")
@require_auth(["admin", "operator", "site_admin"])
def uploads_list():
    project = (request.args.get("project") or "").strip()
    person = (request.args.get("person") or "").strip()
    point = (request.args.get("point") or "").strip()
    day = (request.args.get("day") or "").strip()
    build = (request.args.get("build") or "").strip()
    limit = min(int(request.args.get("limit") or 500), 1000)

    conn = get_conn()
    if day:
        rows = conn.execute(
            "SELECT id, filename, filepath, meta_json, created_at FROM uploads WHERE created_at LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"{day}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, filename, filepath, meta_json, created_at FROM uploads ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()

    data = []
    for r in rows:
        item = _photo_from_upload_row(r)
        meta = item.get("meta") or {}
        if project and meta.get("project") != project:
            continue
        if person and meta.get("person") != person:
            continue
        if build and meta.get("build") != build:
            continue
        if point:
            pt = (meta.get("point") or meta.get("name") or "").strip()
            if point not in pt and pt != point:
                continue
        data.append(item)
    return ok(data)


@app.route("/api/sites/<site_id>/photos", methods=["GET"])
def site_photos(site_id):
    conn = get_conn()
    row = conn.execute("SELECT data_json FROM sites WHERE id=?", (site_id,)).fetchone()
    if not row:
        conn.close()
        return fail("站点不存在", 404)
    site_name = (json.loads(row["data_json"]).get("name") or "").strip()
    rows = conn.execute(
        "SELECT id, filename, filepath, meta_json, created_at FROM uploads ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    photos = [_photo_from_upload_row(r) for r in rows if _match_upload_to_site(json.loads(r["meta_json"] or "{}"), site_name)]
    return ok(photos)


def _photo_item_for_export(row) -> dict:
    meta = json.loads(row["meta_json"] or "{}")
    item = _photo_from_upload_row(row)
    fp = row["filepath"] if row["filepath"] and os.path.isfile(row["filepath"]) else None
    if not fp:
        fp = _find_photo_path({"filename": row["filename"], **meta})
    item["photo_path"] = fp
    return item


def _fetch_site_photo_items(site_name: str) -> list:
    if not site_name:
        return []
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, filename, filepath, meta_json, created_at FROM uploads ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    items = []
    for row in rows:
        meta = json.loads(row["meta_json"] or "{}")
        if not _match_upload_to_site(meta, site_name):
            continue
        items.append(_photo_item_for_export(row))
    return items


def _find_site_record(site_id: str = "", site_name: str = ""):
    if site_id:
        conn = get_conn()
        row = conn.execute("SELECT data_json FROM sites WHERE id=?", (site_id,)).fetchone()
        conn.close()
        if row:
            return _enrich_site(json.loads(row["data_json"]))
    name = (site_name or "").strip()
    if name:
        for site in list_json_table("sites"):
            if site.get("name") == name or _match_upload_to_site({"point": name}, site.get("name") or ""):
                return _enrich_site(site)
        return _enrich_site(
            {
                "id": "",
                "name": name,
                "area": "",
                "status": "施工中",
                "projectName": "",
                "createTime": "",
            }
        )
    return None


def _group_photos_by_stage(photos: list) -> dict:
    grouped = {st: [] for st in SITE_STAGE_ORDER}
    grouped["其他"] = []
    for ph in photos:
        stage = ph.get("stage") or _build_to_stage(ph.get("build") or ph.get("content") or "")
        if stage not in grouped:
            grouped[stage] = []
        grouped[stage].append(ph)
    return grouped


def _send_site_docx(site: dict, photos: list, audit_label: str):
    if not photos:
        return fail("该站点暂无施工照片", 404)
    grouped = _group_photos_by_stage(photos)
    try:
        doc_bytes = build_site_photos_docx(site, photos, grouped)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return fail(f"生成 Word 失败：{exc}", 500)
    fname = build_site_photos_filename(site.get("name") or "站点")
    user = g.current_user if hasattr(g, "current_user") else {"display_name": "系统", "role": ""}
    add_audit(user.get("display_name") or "系统", user.get("role") or "", "导出施工详情", audit_label)
    return send_file(
        io.BytesIO(doc_bytes),
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.route("/api/sites/<site_id>/export-docx")
@require_auth()
def sites_export_docx(site_id):
    site = _find_site_record(site_id=site_id)
    if not site:
        return fail("站点不存在", 404)
    photos = _fetch_site_photo_items(site.get("name") or "")
    return _send_site_docx(site, photos, site.get("name") or site_id)


@app.route("/api/export/site_report/<site_id>")
@require_auth()
def export_site_report_legacy(site_id):
    """施工管理「导出报告」兼容旧路径。"""
    return sites_export_docx(site_id)


@app.route("/api/export/site-photos-docx")
@require_auth()
def export_site_photos_docx():
    """照片管理：按站点名称导出 Word 施工详情。"""
    site_name = (request.args.get("siteName") or request.args.get("site") or "").strip()
    site_id = (request.args.get("siteId") or "").strip()
    if not site_name and not site_id:
        return fail("请指定站点名称或站点ID")
    site = _find_site_record(site_id=site_id, site_name=site_name)
    if not site:
        return fail("站点不存在", 404)
    photos = _fetch_site_photo_items(site.get("name") or site_name)
    return _send_site_docx(site, photos, site.get("name") or site_name)


@app.route("/api/projects")
def projects_api():
    return ok(list_json_table("projects"))


@app.route("/api/projects", methods=["POST"])
@require_auth(["admin", "operator"])
def projects_save():
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action", "create")
    if action == "delete":
        project_id = data.get("id")
        conn = get_conn()
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        conn.commit()
        conn.close()
        add_audit(g.current_user["display_name"], g.current_user["role"], "删除项目", project_id)
        return ok()
    project_id = data.get("id") or make_id("proj")
    item = {
        "id": project_id,
        "name": data.get("name") or "",
        "customer": data.get("customer") or "",
        "contractNo": data.get("contractNo") or "",
        "startDate": data.get("startDate") or "",
        "endDate": data.get("endDate") or "",
        "status": data.get("status") or "进行中",
        "remark": data.get("remark") or "",
    }
    upsert_json_row("projects", project_id, item)
    add_audit(g.current_user["display_name"], g.current_user["role"], "保存项目", item["name"])
    return ok(item)


def _normalize_build_name(name: str) -> str:
    name = (name or "").strip()
    return BUILD_NAME_ALIASES.get(name, name)


def _build_to_stage(build: str) -> str:
    return BUILD_TO_STAGE.get(_normalize_build_name(build), "其他")


def _extract_pole_type_from_extra(extra: dict) -> str:
    if not isinstance(extra, dict):
        return ""
    for key in ("pole_type", "pole_mount_type", "pole_length"):
        val = (extra.get(key) or "").strip()
        if val:
            return val
    return ""


def _is_valid_pole_type(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    if value in POLE_TYPE_VALUES:
        return True
    return any(v in value for v in POLE_TYPE_VALUES)


def _apply_pole_type_to_point(item: dict, extra: dict = None) -> dict:
    """从水印子项提取立杆方式；build 中误存的村名/施工内容会被清理。"""
    extra = extra if isinstance(extra, dict) else (item.get("extraFields") or {})
    pole = _extract_pole_type_from_extra(extra)
    if pole:
        item["build"] = pole
        return item
    current = (item.get("build") or "").strip()
    normalized = _normalize_build_name(current)
    if normalized in CONSTRUCTION_BUILDS or current in CONSTRUCTION_BUILDS:
        item.pop("build", None)
    elif current and not _is_valid_pole_type(current):
        if item.get("source") == "kml_import" and not item.get("kmlSiteName"):
            item["kmlSiteName"] = current
        item.pop("build", None)
    return item


# 水印拍照「站点明细」子项 → 点位主字段
EXTRA_FIELD_TO_POINT = {
    "probe_type": "cam_type",
    "power_status": "power",
    "ont_code": "ont",
    "device_ip": "device_ip",
    "broadband": "broadband",
    "power_length": "cable_15",
    "dig_soil_m": "dig_soil",
    "dig_cement_m": "dig_cement",
    "camera_sn": "camera_sn",
    "waterproof_code": "waterproof_code",
    "rainproof_code": "rainproof_code",
}


def _parse_extra_fields(meta: dict) -> dict:
    extra = meta.get("extraFields") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (json.JSONDecodeError, TypeError):
            extra = {}
    return extra if isinstance(extra, dict) else {}


def _apply_extra_fields_to_point(item: dict, extra: dict) -> dict:
    """合并水印拍照站点明细（extraFields）到点位记录。"""
    if not isinstance(extra, dict) or not extra:
        return item
    merged = dict(item.get("extraFields") or {})
    merged.update({k: v for k, v in extra.items() if v not in (None, "")})
    item["extraFields"] = merged
    for ekey, pkey in EXTRA_FIELD_TO_POINT.items():
        val = extra.get(ekey)
        if val is None:
            continue
        if isinstance(val, str):
            val = val.strip()
        if val != "":
            item[pkey] = val
    return _apply_pole_type_to_point(item, merged)


def _build_point_from_upload_metas(point_name: str, project: str, metas: list, existing: dict = None) -> dict:
    """从同一安装点位的全部水印照片聚合站点数据。"""
    item = dict(existing) if existing else {}
    item.setdefault("id", make_id("pt"))
    item["name"] = point_name
    if project:
        item["fenju"] = project
    item["source"] = "mobile_photo"
    item.setdefault("createTime", now_str())
    item.setdefault("status", "在线")

    for meta in reversed(metas):
        extra = _parse_extra_fields(meta)
        item = _apply_extra_fields_to_point(item, extra)
        area = (meta.get("area") or "").strip()
        if area:
            item["area"] = area[:120]
        lat = meta.get("lat") or meta.get("latitude")
        lng = meta.get("lng") or meta.get("longitude")
        if lat not in (None, "", 0, "0"):
            item["latitude"] = lat
        if lng not in (None, "", 0, "0"):
            item["longitude"] = lng
        person = meta.get("person")
        if person:
            item["lastPerson"] = person

    if existing:
        item["id"] = existing["id"]
        if existing.get("constructionStatus") == "已完工":
            item["constructionStatus"] = "已完工"
        if existing.get("status") in ("在线", "离线", "故障"):
            item["status"] = existing["status"]
        for key in ("offlineReason", "offlineDuration", "paichusuo", "remark", "序号"):
            if existing.get(key) not in (None, ""):
                item[key] = existing[key]
    return item


def _list_points_from_uploads(all_metas: list = None) -> list:
    """站点管理列表：仅从水印拍照（含站点明细）聚合，不含资料库导入。"""
    if all_metas is None:
        all_metas = _load_all_upload_metas()

    groups = {}
    for meta in all_metas:
        point_name = (meta.get("point") or meta.get("name") or "").strip()
        if not point_name:
            continue
        build = _normalize_build_name(meta.get("build") or "")
        if build in NON_SITE_BUILDS:
            continue
        project = (meta.get("project") or "").strip()
        key = (project, point_name)
        groups.setdefault(key, []).append(meta)

    stored_points = list_json_table("points")
    results = []
    seen_ids = set()
    result_keys = set()

    for (project, point_name), metas in groups.items():
        existing = _find_point_for_name(point_name, project)
        item = _build_point_from_upload_metas(point_name, project, metas, existing)
        results.append(item)
        seen_ids.add(item["id"])
        result_keys.add((project, point_name))

    for p in stored_points:
        if p.get("source") == "kml_import":
            continue
        name = (p.get("name") or p.get("point") or "").strip()
        proj = (p.get("fenju") or p.get("project") or "").strip()
        if (proj, name) in result_keys or p.get("id") in seen_ids:
            continue
        results.append(p)
        seen_ids.add(p.get("id"))

    return [_enrich_point(p, all_metas) for p in results]


def _collect_site_upload_metas(site_name: str, all_metas: list = None) -> list:
    if not site_name:
        return []
    if all_metas is None:
        all_metas = _load_all_upload_metas()
    return [m for m in all_metas if _match_upload_to_site(m, site_name)]


def _collect_point_upload_metas(point_name: str, all_metas: list = None) -> list:
    if not point_name:
        return []
    if all_metas is None:
        all_metas = _load_all_upload_metas()
    return [m for m in all_metas if _match_upload_to_point(m, point_name)]


def _compute_site_progress(site_name: str, all_metas: list = None) -> dict:
    return _compute_progress_from_metas(_collect_site_upload_metas(site_name, all_metas))


def _compute_point_progress(point_name: str, all_metas: list = None) -> dict:
    metas = _collect_point_upload_metas(point_name, all_metas)
    progress = _compute_progress_from_metas(metas)
    progress.update(_compute_stage_info(progress, metas))
    return progress


def _compute_stage_info(progress: dict, metas: list = None) -> dict:
    stage_progress = progress.get("stageProgress") or {}
    photo_count = progress.get("photoCount", 0)
    required = progress.get("requiredCount", len(CONSTRUCTION_BUILDS))
    info = {"currentStageKey": "none", "currentStage": STAGE_DISPLAY_NAMES["none"]}
    if photo_count >= required and required > 0:
        info["currentStageKey"] = "done"
        info["currentStage"] = STAGE_DISPLAY_NAMES["done"]
    elif photo_count > 0:
        for key in STAGE_LABELS:
            sp = stage_progress.get(key) or {}
            if not sp.get("complete"):
                info["currentStageKey"] = key
                info["currentStage"] = STAGE_DISPLAY_NAMES.get(key, key)
                break
    if metas:
        for meta in metas:
            build = _normalize_build_name(meta.get("build") or meta.get("content") or "")
            if build in CONSTRUCTION_BUILDS:
                stage_key = _build_to_stage(build)
                info["latestBuild"] = build
                info["latestStageKey"] = stage_key
                info["latestStage"] = STAGE_DISPLAY_NAMES.get(stage_key, stage_key)
                break
    return info


def _derive_point_construction_status(item: dict, progress: dict) -> str:
    stored = (item.get("constructionStatus") or "").strip()
    if stored == "已完工":
        return "已完工"
    photo_count = progress.get("photoCount", 0)
    required = progress.get("requiredCount", len(CONSTRUCTION_BUILDS))
    if photo_count >= required and required > 0:
        return "已完工"
    if photo_count > 0:
        return "施工中"
    return "未施工"


def _enrich_point(item: dict, all_metas: list = None) -> dict:
    item = dict(item)
    name = (item.get("name") or item.get("point") or "").strip()
    progress = _compute_point_progress(name, all_metas)
    item.update(progress)
    item["constructionStatus"] = _derive_point_construction_status(item, progress)
    return _apply_pole_type_to_point(item)


def _find_site_for_point(point: str, project: str = ""):
    point = (point or "").strip()
    if not point:
        return None
    for site in list_json_table("sites"):
        if not _match_upload_to_site({"point": point}, site.get("name") or ""):
            continue
        if project and site.get("projectName") and site.get("projectName") != project:
            if site.get("projectId") and site.get("projectId") != project:
                continue
        return site
    return None


def _find_point_for_name(name: str, project: str = ""):
    name = (name or "").strip()
    if not name:
        return None
    for point in list_json_table("points"):
        pname = (point.get("name") or point.get("point") or "").strip()
        if not pname:
            continue
        if pname != name and name not in pname and pname not in name:
            continue
        if project and point.get("fenju") and point.get("fenju") != project:
            continue
        return point
    return None


def _sync_point_from_upload(meta: dict):
    """手机水印拍照同步到站点管理（points），数据来自站点明细 extraFields。"""
    point_name = (meta.get("point") or meta.get("name") or "").strip()
    if not point_name:
        return None
    build = _normalize_build_name(meta.get("build") or "")
    project = (meta.get("project") or "").strip()
    existing = _find_point_for_name(point_name, project)
    if build in NON_SITE_BUILDS and not existing:
        return None

    all_metas = _load_all_upload_metas()
    point_metas = _collect_point_upload_metas(point_name, all_metas)
    if project:
        point_metas = [
            m for m in point_metas
            if (m.get("project") or "").strip() in ("", project)
        ]
    item = _build_point_from_upload_metas(point_name, project, point_metas, existing)
    progress = _compute_point_progress(point_name, all_metas)
    item.update(progress)
    item["constructionStatus"] = _derive_point_construction_status(item, progress)
    upsert_json_row("points", item["id"], item)
    return _enrich_point(item, all_metas)


def _ensure_site_from_upload(meta: dict):
    point = (meta.get("point") or meta.get("name") or "").strip()
    if not point:
        return None
    build = _normalize_build_name(meta.get("build") or "")
    if build in NON_SITE_BUILDS:
        return _find_site_for_point(point, meta.get("project") or "")
    project = (meta.get("project") or "").strip()
    site = _find_site_for_point(point, project)
    if site:
        return site
    site_id = make_id("site")
    item = {
        "id": site_id,
        "name": point,
        "area": (meta.get("area") or "")[:120],
        "longitude": meta.get("lng") or meta.get("longitude") or 0,
        "latitude": meta.get("lat") or meta.get("latitude") or 0,
        "projectId": project,
        "projectName": project,
        "status": "施工中",
        "createTime": now_str(),
    }
    upsert_json_row("sites", site_id, item)
    return item


def _sync_site_progress_from_upload(meta: dict):
    point = (meta.get("point") or meta.get("name") or "").strip()
    if not point:
        return None
    build = _normalize_build_name(meta.get("build") or "")
    if build in NON_SITE_BUILDS and not _find_site_for_point(point, meta.get("project") or ""):
        return None
    site = _ensure_site_from_upload(meta)
    if not site or not site.get("id"):
        return None
    progress = _compute_site_progress(site.get("name") or point)
    item = dict(site)
    item.update(progress)
    if item.get("status") != "已完工" and progress["photoCount"] >= progress["requiredCount"]:
        item["status"] = "施工中"
    upsert_json_row("sites", site["id"], item)
    return item


def _site_photo_count(site_name: str) -> int:
    return _compute_site_progress(site_name)["photoCount"]


def _enrich_site(item: dict) -> dict:
    item = dict(item)
    item.setdefault("status", "施工中")
    name = item.get("name") or ""
    progress = _compute_site_progress(name)
    item.update(progress)
    item.setdefault("createTime", item.get("createTime") or now_str())
    return item


@app.route("/api/sites", methods=["GET", "POST"])
def sites_api():
    if request.method == "GET":
        sites = [_enrich_site(s) for s in list_json_table("sites")]
        return ok(sites)
    user = get_session_user()
    if not user:
        return fail("未登录或会话已过期", 401)
    data = request.get_json(force=True, silent=True) or {}
    site_id = data.get("id") or make_id("site")
    item = _enrich_site(
        {
            "id": site_id,
            "name": (data.get("name") or "").strip(),
            "area": data.get("area") or "",
            "longitude": data.get("longitude") or 0,
            "latitude": data.get("latitude") or 0,
            "projectId": data.get("projectId") or data.get("projectName") or "",
            "projectName": data.get("projectName") or data.get("projectId") or "",
            "status": data.get("status") or "施工中",
            "requiredCount": data.get("requiredCount") or len(CONSTRUCTION_BUILDS),
            "createTime": now_str(),
        }
    )
    if not item["name"]:
        return fail("站点名称不能为空")
    upsert_json_row("sites", site_id, item)
    add_audit(user["display_name"], user["role"], "创建站点", item["name"])
    return ok(item)


@app.route("/api/sites/<site_id>/finish", methods=["POST"])
def sites_finish(site_id):
    user = get_session_user()
    if not user:
        return fail("未登录或会话已过期", 401)
    conn = get_conn()
    row = conn.execute("SELECT data_json FROM sites WHERE id=?", (site_id,)).fetchone()
    conn.close()
    if not row:
        return fail("站点不存在", 404)
    item = _enrich_site(json.loads(row["data_json"]))
    item["status"] = "已完工"
    upsert_json_row("sites", site_id, item)
    add_audit(user["display_name"], user["role"], "标记完工", item["name"])
    return ok(item)


@app.route("/api/sites/<site_id>/toggle-complete", methods=["PUT"])
def sites_toggle_complete(site_id):
    user = get_session_user()
    if not user:
        return fail("未登录或会话已过期", 401)
    conn = get_conn()
    row = conn.execute("SELECT data_json FROM sites WHERE id=?", (site_id,)).fetchone()
    conn.close()
    if not row:
        return fail("站点不存在", 404)
    item = _enrich_site(json.loads(row["data_json"]))
    item["status"] = "已完工" if item.get("status") != "已完工" else "施工中"
    upsert_json_row("sites", site_id, item)
    add_audit(user["display_name"], user["role"], "切换完工", item["name"])
    return ok(item)


@app.route("/api/sites/<site_id>", methods=["DELETE"])
def sites_delete(site_id):
    user = get_session_user()
    if not user:
        return fail("未登录或会话已过期", 401)
    conn = get_conn()
    row = conn.execute("SELECT data_json FROM sites WHERE id=?", (site_id,)).fetchone()
    if not row:
        conn.close()
        return fail("站点不存在", 404)
    item = json.loads(row["data_json"])
    conn.execute("DELETE FROM sites WHERE id=?", (site_id,))
    conn.commit()
    conn.close()
    add_audit(user["display_name"], user["role"], "删除站点", item.get("name") or site_id)
    return ok()


def _kml_summary(item: dict) -> dict:
    pms = item.get("placemarks") or []
    return {
        "id": item.get("id"),
        "siteName": item.get("siteName") or "",
        "projectName": item.get("projectName") or "",
        "filename": item.get("filename") or "",
        "sourceType": item.get("sourceType") or "kml",
        "placemarkCount": len(pms),
        "createTime": item.get("createTime") or "",
    }


def _find_kml_by_site(project_name: str, site_name: str):
    for item in list_json_table("kml_sites"):
        if item.get("projectName") == project_name and item.get("siteName") == site_name:
            return item
    return None


def _sync_kml_placemarks_to_points(project_name: str, site_name: str, placemarks: list, kml_id: str):
    """将 KML/Excel 点位同步到站点管理。"""
    synced = 0
    for i, pm in enumerate(placemarks):
        name = (pm.get("name") or "").strip()
        if not name:
            continue
        meta = pm.get("meta") or {}
        addr = meta.get("address") or (pm.get("description") or "").split("建设模式")[0].strip()
        point_id = f"pt_{kml_id}_{i}"
        item = {
            "id": point_id,
            "name": name,
            "fenju": project_name,
            "area": addr[:80] if addr else site_name,
            "longitude": pm.get("longitude") or 0,
            "latitude": pm.get("latitude") or 0,
            "kmlSiteName": site_name,
            "remark": f"来源:{kml_id}",
            "source": "kml_import",
            "kmlSiteId": kml_id,
            "createTime": now_str(),
        }
        if meta.get("ponPort"):
            item.setdefault("extraFields", {})["pon_port"] = meta["ponPort"]
        if meta.get("qrCode"):
            item.setdefault("extraFields", {})["qr_code"] = meta["qrCode"]
        upsert_json_row("points", point_id, item)
        synced += 1
    return synced


def _ensure_construction_site(project_name: str, site_name: str, placemarks: list):
    """确保施工管理中有对应站点。"""
    for s in list_json_table("sites"):
        if s.get("name") == site_name and (
            s.get("projectName") == project_name or s.get("projectId") == project_name
        ):
            return s
    site_id = make_id("site")
    lng = placemarks[0].get("longitude", 0) if placemarks else 0
    lat = placemarks[0].get("latitude", 0) if placemarks else 0
    addr = ""
    if placemarks:
        meta = placemarks[0].get("meta") or {}
        addr = meta.get("address") or (placemarks[0].get("description") or "").split("建设模式")[0].strip()
    item = _enrich_site(
        {
            "id": site_id,
            "name": site_name,
            "area": addr[:80] if addr else site_name,
            "longitude": lng,
            "latitude": lat,
            "projectId": project_name,
            "projectName": project_name,
            "status": "施工中",
            "requiredCount": max(len(placemarks), 1),
            "createTime": now_str(),
        }
    )
    upsert_json_row("sites", site_id, item)
    return item


@app.route("/api/kml-sites", methods=["GET", "POST"])
def kml_sites_api():
    if request.method == "GET":
        items = list_json_table("kml_sites")
        project = (request.args.get("project") or "").strip()
        if project:
            items = [i for i in items if i.get("projectName") == project]
        return ok([_kml_summary(i) for i in items])

    user = get_session_user()
    if not user:
        return fail("未登录或会话已过期", 401)

    data = request.get_json(force=True, silent=True) or {}
    project_name = (request.form.get("projectName") or request.form.get("project") or data.get("projectName") or "").strip()
    sync_points = (request.form.get("syncPoints") or data.get("syncPoints") or "").lower() in ("1", "true", "yes")
    create_site = (request.form.get("createSite") or data.get("createSite") or "").lower() in ("1", "true", "yes")
    source_type = "kml"
    filename = "upload.kml"
    site_name = ""
    placemarks = []

    upload = request.files.get("file")
    if upload and upload.filename:
        filename = upload.filename
        raw = upload.read()
        lower = filename.lower()
        if lower.endswith((".xlsx", ".xls", ".csv")):
            source_type = "excel"
            try:
                import openpyxl
            except ImportError:
                return fail("服务器未安装 openpyxl，请在前端上传 Excel 或使用 KML 文件")
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            rows = [[cell.value for cell in row] for row in ws.iter_rows()]
            default_site = os.path.splitext(filename)[0]
            try:
                site_name, placemarks = parse_excel_rows(rows, default_site)
            except ValueError as exc:
                return fail(str(exc))
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    text = raw.decode("gb18030")
                except UnicodeDecodeError:
                    return fail("KML 文件编码无法识别，请使用 UTF-8")
            try:
                site_name, placemarks = parse_kml(text)
            except ValueError as exc:
                return fail(str(exc))
    elif data.get("placemarks"):
        source_type = data.get("sourceType") or "excel"
        filename = data.get("filename") or "import.xlsx"
        site_name = (data.get("siteName") or "").strip()
        placemarks = data.get("placemarks") or []
        if not placemarks:
            return fail("点位数据为空")
        if not site_name:
            site_name = placemarks[0].get("name", "")[:20] or "Excel站点"
    else:
        filename = data.get("filename") or filename
        raw = (data.get("content") or "").encode("utf-8")
        if not raw:
            return fail("请上传 KML 或 Excel 文件")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gb18030")
            except UnicodeDecodeError:
                return fail("KML 文件编码无法识别，请使用 UTF-8")
        try:
            site_name, placemarks = parse_kml(text)
        except ValueError as exc:
            return fail(str(exc))

    if not project_name:
        return fail("请选择所属项目")

    existing = _find_kml_by_site(project_name, site_name)
    kml_id = existing["id"] if existing else make_id("kml")
    item = {
        "id": kml_id,
        "siteName": site_name,
        "projectName": project_name,
        "filename": filename,
        "sourceType": source_type,
        "placemarks": placemarks,
        "createTime": existing.get("createTime") if existing else now_str(),
        "updateTime": now_str(),
    }
    upsert_json_row("kml_sites", kml_id, item)

    synced = 0
    # 站点管理数据仅来自水印拍照，资料库不再写入 points
    if create_site and site_name:
        _ensure_construction_site(project_name, site_name, placemarks)

    action = "更新" if existing else "上传"
    src_label = "奥维KML" if source_type == "kml" else "Excel"
    add_audit(
        user["display_name"],
        user["role"],
        f"{action}{src_label}站点",
        f"{project_name} / {site_name} ({len(placemarks)}点)",
    )
    result = {**_kml_summary(item), "placemarks": placemarks, "syncedPoints": synced}
    return ok(result)


def _sync_kml_item_to_modules(item: dict, sync_points: bool = False, create_site: bool = True):
    project_name = item.get("projectName") or ""
    site_name = item.get("siteName") or ""
    placemarks = item.get("placemarks") or []
    synced = 0
    if create_site and site_name:
        _ensure_construction_site(project_name, site_name, placemarks)
    return synced


@app.route("/api/kml-sites/sync-all", methods=["POST"])
def kml_sync_all():
    user = get_session_user()
    if not user:
        return fail("未登录或会话已过期", 401)
    data = request.get_json(force=True, silent=True) or {}
    sync_points = data.get("syncPoints", False)
    create_site = data.get("createSite", True)
    total_synced = 0
    site_count = 0
    for item in list_json_table("kml_sites"):
        synced = _sync_kml_item_to_modules(item, sync_points, create_site)
        total_synced += synced
        if create_site and item.get("siteName"):
            site_count += 1
    add_audit(
        user["display_name"],
        user["role"],
        "批量同步资料库到施工管理",
        f"施工站点 {site_count} 个",
    )
    return ok({"syncedPoints": 0, "siteCount": site_count})


@app.route("/api/kml-sites/<kml_id>/sync", methods=["POST"])
def kml_site_sync(kml_id):
    user = get_session_user()
    if not user:
        return fail("未登录或会话已过期", 401)
    conn = get_conn()
    row = conn.execute("SELECT data_json FROM kml_sites WHERE id=?", (kml_id,)).fetchone()
    conn.close()
    if not row:
        return fail("KML 不存在", 404)
    item = json.loads(row["data_json"])
    data = request.get_json(force=True, silent=True) or {}
    sync_points = data.get("syncPoints", False)
    create_site = data.get("createSite", True)
    synced = _sync_kml_item_to_modules(item, sync_points, create_site)
    add_audit(
        user["display_name"],
        user["role"],
        "同步资料库到施工管理",
        f"{item.get('projectName') or ''} / {item.get('siteName') or kml_id}",
    )
    return ok({"syncedPoints": 0, "siteName": item.get("siteName"), "kmlId": kml_id})


@app.route("/api/kml-sites/<kml_id>", methods=["GET", "DELETE"])
def kml_site_detail(kml_id):
    conn = get_conn()
    row = conn.execute("SELECT data_json FROM kml_sites WHERE id=?", (kml_id,)).fetchone()
    conn.close()
    if not row:
        return fail("KML 不存在", 404)
    item = json.loads(row["data_json"])

    if request.method == "DELETE":
        user = get_session_user()
        if not user:
            return fail("未登录或会话已过期", 401)
        conn = get_conn()
        conn.execute("DELETE FROM kml_sites WHERE id=?", (kml_id,))
        conn.commit()
        conn.close()
        add_audit(user["display_name"], user["role"], "删除KML站点", item.get("siteName") or kml_id)
        return ok()

    return ok(item)


@app.route("/api/geocode")
def geocode():
    lat = request.args.get("lat", "")
    lng = request.args.get("lng", "")
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except ValueError:
        return fail("坐标无效")
    address = f"江西省南昌市 ({lat_f:.6f}, {lng_f:.6f})"
    try:
        url = f"https://tianwang-jx.vip.cpolar.cn/api/geocode?lat={lat}&lng={lng}&coord_type={request.args.get('coord_type', 'wgs84')}"
        with urllib.request.urlopen(url, timeout=8) as resp:
            remote = json.loads(resp.read().decode("utf-8"))
            if remote.get("success") and remote.get("address"):
                address = remote["address"]
    except Exception:
        pass
    return jsonify({"success": True, "address": address, "lat": lat, "lng": lng})


@app.route("/api/mobile-upload", methods=["POST"])
def mobile_upload():
    meta = {}
    raw_bytes = None
    compress_info = {}

    if request.content_type and "application/json" in request.content_type:
        data = request.get_json(force=True, silent=True) or {}
        meta = {k: v for k, v in data.items() if k != "photo"}
        b64 = data.get("photo") or ""
        if b64:
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            raw_bytes = base64.b64decode(b64)
    else:
        meta = request.form.to_dict()
        photo = request.files.get("photo") or request.files.get("image")
        if photo:
            raw_bytes = photo.read()

    ensure_dirs()
    upload_id = make_id("up")
    filename = f"{upload_id}.jpg"
    file_info = {"filename": filename, "url": f"/uploads/{filename}", "storage": "local"}

    if raw_bytes:
        compressed, compress_info = compress_image_bytes(raw_bytes, MAX_PHOTO_BYTES)
        file_info = save_bytes("uploads", filename, compressed)
        file_info["size"] = len(compressed)
        file_info["compress"] = compress_info

    meta.update(
        {
            "cosKey": file_info.get("cosKey", ""),
            "cosUrl": file_info.get("cosUrl", ""),
            "url": file_info.get("url", f"/uploads/{filename}"),
            "storage": file_info.get("storage", "local"),
            "photoSize": file_info.get("size", 0),
            "compress": compress_info,
        }
    )
    conn = get_conn()
    conn.execute(
        "INSERT INTO uploads (id, filename, filepath, meta_json, created_at) VALUES (?,?,?,?,?)",
        (upload_id, filename, file_info.get("filepath", ""), json.dumps(meta, ensure_ascii=False), now_str()),
    )
    conn.commit()
    conn.close()
    site_sync = _sync_site_progress_from_upload(meta)
    point_sync = _sync_point_from_upload(meta)
    add_audit(meta.get("person") or "手机端", "mobile", meta.get("build") or "拍照上传", meta.get("point") or meta.get("project") or upload_id)
    msg = "照片上传成功"
    if compress_info.get("compressed"):
        msg = f"照片已压缩至 {compress_info.get('compressedSize', 0) // 1024}KB"
    resp_data = {
        "id": upload_id,
        "filename": filename,
        "url": file_info.get("url"),
        "cosUrl": file_info.get("cosUrl", ""),
        "cosKey": file_info.get("cosKey", ""),
        "storage": file_info.get("storage", "local"),
        "size": file_info.get("size", 0),
        "maxSize": MAX_PHOTO_BYTES,
        "compress": compress_info,
        "message": msg,
    }
    if site_sync:
        resp_data["site"] = {
            "id": site_sync.get("id"),
            "name": site_sync.get("name"),
            "photoCount": site_sync.get("photoCount"),
            "requiredCount": site_sync.get("requiredCount"),
            "completedBuilds": site_sync.get("completedBuilds") or [],
            "stageProgress": site_sync.get("stageProgress") or {},
        }
    if point_sync:
        resp_data["point"] = {"id": point_sync.get("id"), "name": point_sync.get("name")}
    return ok(resp_data)


@app.route("/api/upload-photo", methods=["POST"])
def upload_photo():
    return mobile_upload()


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    directory = local_dir("uploads")
    return send_from_directory(directory, filename)


@app.route("/api/uploads/thumb")
def upload_thumb():
    """手机端缩略图（img 标签无法带 Authorization，用 token 查询参数）。"""
    upload_id = (request.args.get("id") or "").strip()
    token = (request.args.get("token") or "").strip()
    if not upload_id or not token:
        return fail("无效请求", 400)
    conn = get_conn()
    sess = conn.execute(
        "SELECT username FROM sessions WHERE token=? AND expires_at >= ?", (token, now_str())
    ).fetchone()
    if not sess:
        conn.close()
        return fail("未登录或会话已过期", 401)
    row = conn.execute("SELECT filename, filepath FROM uploads WHERE id=?", (upload_id,)).fetchone()
    conn.close()
    if not row:
        return fail("照片不存在", 404)
    path = row["filepath"] if row["filepath"] and os.path.isfile(row["filepath"]) else ""
    if not path:
        path = os.path.join(local_dir("uploads"), row["filename"] or "")
    if not os.path.isfile(path):
        return fail("照片文件不存在", 404)
    resp = send_file(path, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.route("/api/daily-log/meta", methods=["GET", "POST"])
@require_auth()
def daily_log_meta():
    denied = daily_log_access_denied(g.current_user)
    if denied:
        return denied
    conn = get_conn()
    if request.method == "GET":
        project = (request.args.get("project") or "").strip()
        data = get_log_meta(conn, project)
        conn.close()
        return ok({"project": project, "meta": data})
    body = request.get_json(force=True, silent=True) or {}
    project = (body.get("project") or "").strip()
    meta = save_log_meta(conn, project, body.get("meta") or body)
    conn.close()
    add_audit(g.current_user["display_name"], g.current_user["role"], "保存施工日志信息", project or "默认")
    return ok({"project": project, "meta": meta})


@app.route("/api/daily-log")
@require_auth()
def daily_log_preview():
    denied = daily_log_access_denied(g.current_user)
    if denied:
        return denied
    day = (request.args.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
    project = (request.args.get("project") or "").strip()
    person = (request.args.get("person") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return fail("日期格式应为 YYYY-MM-DD")
    conn = get_conn()
    items = fetch_day_uploads(conn, day, project, person)
    conn.close()
    summary = []
    photos = []
    for it in items:
        m = it["meta"]
        summary.append(
            {
                "time": it["time"],
                "point": m.get("point") or "",
                "build": m.get("build") or "",
                "project": m.get("project") or "",
                "person": m.get("person") or "",
                "area": m.get("area") or "",
            }
        )
        if it.get("photo_path"):
            photos.append(
                {
                    "id": it["id"],
                    "time": it["time"],
                    "point": m.get("point") or "",
                    "build": m.get("build") or "",
                    "caption": _photo_caption(m),
                    "filename": it["filename"],
                    "thumb": f"/api/uploads/thumb?id={it['id']}",
                }
            )
    return ok({"date": day, "project": project, "count": len(items), "items": summary, "photos": photos, "photoCount": len(photos)})


@app.route("/api/daily-log/export")
@require_auth()
def daily_log_export():
    denied = daily_log_access_denied(g.current_user)
    if denied:
        return denied
    day = (request.args.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
    project = (request.args.get("project") or "").strip()
    person = (request.args.get("person") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return fail("日期格式应为 YYYY-MM-DD")
    photo_mode = (request.args.get("photos") or "share").strip().lower()
    if photo_mode not in PHOTO_EXPORT_PRESETS:
        photo_mode = "share"
    photo_ids = None
    if "photo_ids" in request.args:
        raw = (request.args.get("photo_ids") or "").strip()
        photo_ids = {x.strip() for x in raw.split(",") if x.strip()}
    conn = get_conn()
    items = fetch_day_uploads(conn, day, project, person)
    meta = get_log_meta(conn, project)
    conn.close()
    recorder = g.current_user.get("display_name") or g.current_user.get("username") or ""
    try:
        doc_bytes = build_daily_log_docx(
            day, project, items, recorder=recorder, meta=meta, photo_mode=photo_mode, photo_ids=photo_ids
        )
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return fail(f"生成施工日志失败：{exc}", 500)
    if not doc_bytes:
        return fail("生成施工日志失败：文档为空", 500)
    fname = build_export_filename(day, project, meta)
    ensure_dirs()
    export_dir = local_dir("exports")
    os.makedirs(export_dir, exist_ok=True)
    with open(os.path.join(export_dir, fname), "wb") as f:
        f.write(doc_bytes)
    add_audit(recorder, g.current_user.get("role"), "导出施工日志", f"{fname} 共{len(items)}条 模式{photo_mode}")
    resp = send_file(
        io.BytesIO(doc_bytes),
        as_attachment=request.args.get("inline") != "1",
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    resp.headers["X-Log-Photo-Mode"] = photo_mode
    resp.headers["X-Log-File-Bytes"] = str(len(doc_bytes))
    return resp


@app.route("/api/daily-log/share")
def daily_log_share_page():
    """微信分享落地页：点开链接后一键下载 Word，避免内置浏览器直接预览 docx。"""
    day = (request.args.get("date") or "").strip()
    project = (request.args.get("project") or "").strip()
    token = (request.args.get("token") or "").strip()
    fname = (request.args.get("fname") or "").strip()
    if not day or not token:
        return fail("链接无效或已过期", 400)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return fail("日期无效", 400)
    photo_mode = (request.args.get("photos") or "share").strip().lower()
    if photo_mode not in PHOTO_EXPORT_PRESETS:
        photo_mode = "share"
    photo_ids_raw = (request.args.get("photo_ids") or "").strip()
    dl_params = {"date": day, "project": project, "token": token, "photos": photo_mode}
    if photo_ids_raw:
        dl_params["photo_ids"] = photo_ids_raw
    dl_q = urllib.parse.urlencode(dl_params)
    dl_url = f"/api/daily-log/export?{dl_q}"
    title = fname or f"施工日志-{day.replace('-', '')}"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:32px 20px 40px;background:#f5f7fa;color:#333;text-align:center}}
.card{{max-width:400px;margin:0 auto;background:#fff;border-radius:14px;padding:28px 20px;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
h1{{font-size:20px;margin:0 0 8px;color:#1565c0}}
.fname{{font-size:13px;color:#666;word-break:break-all;margin-bottom:20px;line-height:1.5}}
.btn{{display:block;width:100%;padding:15px;border:none;border-radius:10px;background:linear-gradient(135deg,#07c160,#06ad56);color:#fff;font-size:17px;font-weight:700;text-decoration:none;box-sizing:border-box}}
.tip{{font-size:12px;color:#888;margin-top:18px;line-height:1.7;text-align:left}}
</style></head><body>
<div class="card">
<h1>📄 施工日志</h1>
<div class="fname">{title}</div>
<a class="btn" id="dlBtn" href="{dl_url}">📥 下载 Word 文档</a>
<p class="tip">• 点击上方按钮即可保存到手机<br>• 在微信聊天中点「＋」→「文件」发送，无需 WPS 打开<br>• 若未自动下载，请点右上角「在浏览器中打开」后再试</p>
</div>
<script>
document.getElementById('dlBtn').addEventListener('click',function(e){{
  e.preventDefault();
  var a=document.createElement('a');
  a.href={json.dumps(dl_url)};
  a.download={json.dumps(title if title.endswith('.docx') else title + '.docx')};
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  setTimeout(function(){{ location.href={json.dumps(dl_url)}; }},300);
}});
</script></body></html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/daily-log/file-share")
def daily_log_file_share_page():
    """系统浏览器打开后，一键以 Word 文件形式分享到微信（非链接）。"""
    day = (request.args.get("date") or "").strip()
    project = (request.args.get("project") or "").strip()
    token = (request.args.get("token") or "").strip()
    fname = (request.args.get("fname") or "").strip()
    if not day or not token:
        return fail("链接无效或已过期", 400)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return fail("日期无效", 400)
    photo_mode = (request.args.get("photos") or "share").strip().lower()
    if photo_mode not in PHOTO_EXPORT_PRESETS:
        photo_mode = "share"
    photo_ids_raw = (request.args.get("photo_ids") or "").strip()
    dl_params = {"date": day, "project": project, "token": token, "photos": photo_mode}
    if photo_ids_raw:
        dl_params["photo_ids"] = photo_ids_raw
    dl_q = urllib.parse.urlencode(dl_params)
    dl_url = f"/api/daily-log/export?{dl_q}"
    title = fname or f"施工日志-{day.replace('-', '')}.docx"
    if not title.endswith(".docx"):
        title += ".docx"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>发送施工日志</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;padding:28px 16px 36px;background:#f5f7fa;color:#333;text-align:center}}
.card{{max-width:400px;margin:0 auto;background:#fff;border-radius:14px;padding:24px 18px;box-shadow:0 4px 20px rgba(0,0,0,.08)}}
h1{{font-size:18px;margin:0 0 6px;color:#1565c0}}
.fname{{font-size:12px;color:#666;word-break:break-all;margin-bottom:16px;line-height:1.5}}
.btn{{display:block;width:100%;padding:15px;border:none;border-radius:10px;background:linear-gradient(135deg,#07c160,#06ad56);color:#fff;font-size:16px;font-weight:700;cursor:pointer;margin-bottom:10px}}
.btn2{{display:block;width:100%;padding:12px;border:1px solid #1565c0;border-radius:10px;background:#fff;color:#1565c0;font-size:14px;font-weight:600;cursor:pointer}}
#status{{font-size:13px;color:#666;margin:12px 0;line-height:1.6;min-height:20px}}
.tip{{font-size:12px;color:#888;margin-top:14px;line-height:1.7;text-align:left}}
.warn{{font-size:12px;color:#e65100;background:#fff8e1;border-radius:8px;padding:10px;margin-bottom:12px;line-height:1.6;text-align:left}}
</style></head><body>
<div class="card">
<h1>📤 以文件发到微信</h1>
<div class="fname">{title}</div>
<div id="wxWarn" class="warn" style="display:none">当前在微信内打开，无法直接调起发文件。<br>请点击右上角 <b>⋯</b> → <b>在浏览器中打开</b>，再点下方绿色按钮。</div>
<div id="status">正在准备 Word 文件…</div>
<button class="btn" id="shareBtn" style="display:none" onclick="shareFile()">📤 发到微信（选择联系人）</button>
<button class="btn2" id="dlBtn" style="display:none" onclick="downloadFile()">💾 仅下载到手机</button>
<p class="tip">• 系统浏览器会自动弹出分享面板，点微信后选择联系人即可<br>• 微信内打开时，请先点右上角「在浏览器中打开」本页</p>
</div>
<script>
var DL_URL={json.dumps(dl_url)};
var FNAME={json.dumps(title)};
var fileBlob=null,fileObj=null;
var isWx=/MicroMessenger/i.test(navigator.userAgent);
if(isWx) document.getElementById('wxWarn').style.display='block';

function setStatus(t){{ document.getElementById('status').textContent=t; }}

function buildFile(blob){{
  fileBlob=blob;
  var types=['application/octet-stream','application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
  fileObj=new File([blob],FNAME,{{type:types[0],lastModified:Date.now()}});
  document.getElementById('shareBtn').style.display='block';
  document.getElementById('dlBtn').style.display='block';
  if(isWx){{
    downloadFile();
    setStatus('文件已下载。请点右上角 ⋯ →「在浏览器中打开」，将自动唤起微信选择联系人');
  }}else{{
    setStatus('正在唤起分享，请选择微信…');
    setTimeout(shareFile,200);
  }}
}}

function downloadFile(){{
  if(!fileBlob){{ setStatus('文件还在加载…'); return; }}
  var u=URL.createObjectURL(fileBlob);
  var a=document.createElement('a');a.href=u;a.download=FNAME;
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  setTimeout(function(){{URL.revokeObjectURL(u);}},5000);
  setStatus('已开始下载');
}}

async function shareFile(){{
  if(!fileBlob){{ setStatus('文件还在加载…'); return; }}
  if(isWx){{ setStatus('请先在浏览器中打开本页'); return; }}
  if(!navigator.share){{ downloadFile(); setStatus('当前环境不支持分享，已改为下载'); return; }}
  var types=['application/octet-stream','application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
  for(var i=0;i<types.length;i++){{
    var f=new File([fileBlob],FNAME,{{type:types[i],lastModified:Date.now()}});
    try{{
      if(navigator.canShare&&!navigator.canShare({{files:[f]}})) continue;
      await navigator.share({{files:[f],title:FNAME}});
      setStatus('已唤起分享，请选择微信');
      return;
    }}catch(e){{ if(e&&e.name==='AbortError') return; }}
  }}
  downloadFile();
  setStatus('无法调起分享，已改为下载，请到微信「文件」中发送');
}}

fetch(DL_URL).then(function(r){{
  if(!r.ok) throw new Error('加载失败');
  return r.blob();
}}).then(buildFile).catch(function(){{
  setStatus('加载失败，请返回重新导出');
}});
</script></body></html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/storage/info")
@require_auth(["admin", "operator"])
def storage_info_api():
    info = storage_info()
    conn = get_conn()
    info["counts"] = {
        "uploads": conn.execute("SELECT COUNT(*) FROM uploads").fetchone()[0],
    }
    conn.close()
    return ok(info)


@app.route("/api/storage/backup", methods=["POST"])
@require_auth(["admin"])
def storage_backup_api():
    from db import DB_PATH

    result = backup_database(DB_PATH)
    if not result:
        return fail("数据库文件不存在", 404)
    add_audit(g.current_user["display_name"], g.current_user["role"], "数据库备份", result["filename"])
    return ok(result)


@app.route("/api/orders/poll")
def orders_poll():
    user = get_session_user()
    if not user:
        return fail("未登录或会话已过期", 401)
    last_check = int(request.args.get("lastCheck") or 0)
    worker_name = request.args.get("workerName", "")
    faults = list_json_table("faults")
    new_items = []
    for f in faults:
        if worker_name and f.get("assignedTo") != worker_name:
            continue
        if f.get("status") in ("已完成",):
            continue
        ts = f.get("createTime") or ""
        new_items.append(
            {
                "id": f.get("id"),
                "pointName": f.get("pointName") or "",
                "desc": f.get("desc") or "",
                "priority": f.get("priority") or "普通",
                "status": f.get("status") or "待处理",
            }
        )
    return ok(new_items, serverTime=int(datetime.now().timestamp()), lastCheck=last_check)


@app.route("/api/settlement/expenses", methods=["GET", "POST", "DELETE"])
@require_auth(["admin", "sysadmin"])
def expenses_api():
    if request.method == "GET":
        conn = get_conn()
        rows = conn.execute("SELECT id, data_json FROM expenses ORDER BY created_at DESC").fetchall()
        conn.close()
        items = []
        for row in rows:
            item = json.loads(row["data_json"])
            item["id"] = item.get("id") or row["id"]
            items.append(item)
        return ok(items)
    if request.method == "DELETE":
        expense_id = request.args.get("id")
        if not expense_id:
            return fail("缺少记录 ID", 400)
        conn = get_conn()
        row = conn.execute("SELECT id FROM expenses WHERE id=?", (expense_id,)).fetchone()
        if not row:
            conn.close()
            return fail("记录不存在", 404)
        conn.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
        conn.commit()
        conn.close()
        return ok()
    data = request.get_json(force=True, silent=True) or {}
    expense_id = data.get("id") or make_id("exp")
    data["id"] = expense_id
    upsert_json_row("expenses", expense_id, data)
    return ok(data)


@app.route("/api/dashboard")
@require_auth(["admin", "operator", "site_admin"])
def dashboard():
    conn = get_conn()
    stats = {
        "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "workers": conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0],
        "points": conn.execute("SELECT COUNT(*) FROM points").fetchone()[0],
        "faults": conn.execute("SELECT COUNT(*) FROM faults").fetchone()[0],
        "projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
        "uploads": conn.execute("SELECT COUNT(*) FROM uploads").fetchone()[0],
        "auditLogs": conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0],
    }
    recent_logs = [row_to_dict(r) for r in conn.execute("SELECT * FROM audit_logs ORDER BY time DESC LIMIT 10").fetchall()]
    conn.close()
    return ok({"stats": stats, "recentLogs": recent_logs})


@app.route("/api/ocr", methods=["POST"])
def ocr_proxy():
    if not request.files.get("image"):
        return fail("缺少图片")
    try:
        import urllib.request

        boundary = secrets.token_hex(8)
        image = request.files["image"].read()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="photo.jpg"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode("utf-8") + image + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            OCR_PROXY + "/api/ocr",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return jsonify(json.loads(resp.read().decode("utf-8")))
    except Exception as exc:
        return fail(f"OCR 服务暂不可用: {exc}", 503)


if __name__ == "__main__":
    init_db()
    ensure_dirs()
    app.run(host="127.0.0.1", port=8080, debug=False)
