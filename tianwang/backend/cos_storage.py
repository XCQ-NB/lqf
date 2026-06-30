import os
from datetime import datetime

# 腾讯云 COS（通过 cosfs 挂载到本地）
COS_MOUNT = os.environ.get("TIANWANG_COS_MOUNT", "/lhcos-data")
COS_BUCKET = os.environ.get("TIANWANG_COS_BUCKET", "lhcos-8a072-1447557129")
COS_REGION = os.environ.get("TIANWANG_COS_REGION", "ap-guangzhou")
COS_PREFIX = os.environ.get("TIANWANG_COS_PREFIX", "tianwang")

# 存储目录规划
DIRS = {
    "uploads": f"{COS_PREFIX}/uploads",      # 手机水印照片
    "backups": f"{COS_PREFIX}/backups",      # 数据库备份
    "exports": f"{COS_PREFIX}/exports",      # 导出文件
}


def cos_enabled() -> bool:
    return os.path.isdir(COS_MOUNT) and os.access(COS_MOUNT, os.W_OK)


def local_dir(kind: str) -> str:
    rel = DIRS.get(kind, f"{COS_PREFIX}/{kind}")
    if cos_enabled():
        return os.path.join(COS_MOUNT, rel)
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), kind)
    return base


def ensure_dirs():
    for kind in DIRS:
        path = local_dir(kind)
        os.makedirs(path, exist_ok=True)
    return cos_enabled()


def cos_key(kind: str, filename: str) -> str:
    return f"{DIRS[kind]}/{filename}"


def public_url(cos_key: str) -> str:
    return f"https://{COS_BUCKET}.cos.{COS_REGION}.myqcloud.com/{cos_key}"


def save_bytes(kind: str, filename: str, data: bytes) -> dict:
    ensure_dirs()
    rel_key = cos_key(kind, filename)
    full_path = os.path.join(COS_MOUNT, rel_key) if cos_enabled() else os.path.join(local_dir(kind), filename)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(data)
    info = {
        "filename": filename,
        "filepath": full_path,
        "cosKey": rel_key,
        "storage": "cos" if cos_enabled() else "local",
        "url": f"/uploads/{filename}" if kind == "uploads" else f"/files/{kind}/{filename}",
    }
    if cos_enabled():
        info["cosUrl"] = public_url(rel_key)
        info["url"] = info["cosUrl"]
    return info


def save_upload_file(file_storage, filename: str) -> dict:
    ensure_dirs()
    rel_key = cos_key("uploads", filename)
    if cos_enabled():
        full_path = os.path.join(COS_MOUNT, rel_key)
    else:
        full_path = os.path.join(local_dir("uploads"), filename)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    file_storage.save(full_path)
    info = {
        "filename": filename,
        "filepath": full_path,
        "cosKey": rel_key,
        "storage": "cos" if cos_enabled() else "local",
    }
    if cos_enabled():
        info["cosUrl"] = public_url(rel_key)
        info["url"] = info["cosUrl"]
    else:
        info["url"] = f"/uploads/{filename}"
    return info


def backup_database(db_path: str) -> dict | None:
    if not os.path.isfile(db_path):
        return None
    ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"tianwang_{stamp}.db"
    rel_key = cos_key("backups", filename)
    if cos_enabled():
        dest = os.path.join(COS_MOUNT, rel_key)
    else:
        dest = os.path.join(local_dir("backups"), filename)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(db_path, "rb") as src, open(dest, "wb") as dst:
        dst.write(src.read())
    result = {"filename": filename, "cosKey": rel_key, "filepath": dest, "storage": "cos" if cos_enabled() else "local"}
    if cos_enabled():
        result["cosUrl"] = public_url(rel_key)
    return result


def storage_info() -> dict:
    ensure_dirs()
    return {
        "enabled": cos_enabled(),
        "mount": COS_MOUNT,
        "bucket": COS_BUCKET,
        "region": COS_REGION,
        "prefix": COS_PREFIX,
        "dirs": DIRS,
    }
