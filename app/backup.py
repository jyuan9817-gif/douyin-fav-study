"""一键备份 / 恢复。

备份文件是一个普通的 .zip，里面有：
  manifest.json   版本、时间、条数、原来的笔记目录
  library.db      所有条目、文字稿、AI 笔记、精读、答题记录、学习进度
  settings.json   设置（可选是否包含 API Key）
  知识库/          所有 Markdown 笔记

不包含：抖音登录状态（换电脑重新扫码即可，也不应该随便拷来拷去）、语音模型（会自动重新下载）、
临时视频。

恢复 = 用备份覆盖当前数据。恢复之前会先把当前数据自动备份到 data/backups/，万一选错了还能找回来。
"""
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import config, db, notes

FORMAT_VERSION = 1
BACKUP_DIR = config.DATA / "backups"
NOTES_PREFIX = "知识库/"
MAX_UNPACKED = 4 * 1024 ** 3  # 解压后最多 4GB，防止异常文件撑爆硬盘


class BackupError(Exception):
    pass


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def create(include_keys=True, dest_dir=None, tag="备份"):
    """生成备份文件，返回路径。"""
    dest_dir = Path(dest_dir or tempfile.mkdtemp(prefix="dsbk-"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"AI知识库{tag}-{_stamp()}.zip"
    lib = config.library_dir()

    with tempfile.TemporaryDirectory() as tmp:
        # 用 SQLite 自带的在线备份，程序正在运行也能拿到完整一致的数据库
        snap = Path(tmp) / "library.db"
        dst = sqlite3.connect(snap)
        with dst:
            db.conn().backup(dst)
        dst.close()

        s = config.load()
        if not include_keys:
            s["api_key"] = ""
            s["deep_api_key"] = ""
        stats = db.stats()
        note_files = [f for f in sorted(lib.rglob("*")) if f.is_file() and not f.name.startswith(".")] if lib.exists() else []
        manifest = {
            "app": "AI知识库", "format": FORMAT_VERSION, "created_at": time.time(),
            "created": time.strftime("%Y-%m-%d %H:%M"), "items": stats["total"],
            "done": stats["by_status"].get("done", 0), "learned": stats["learned"],
            "library_dir": str(lib), "has_keys": bool(include_keys and (s.get("api_key") or s.get("deep_api_key"))),
            "notes": len(note_files),
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            z.write(snap, "library.db")
            z.writestr("settings.json", json.dumps(s, ensure_ascii=False, indent=2))
            for f in note_files:
                z.write(f, NOTES_PREFIX + f.relative_to(lib).as_posix())
    return path, manifest


def inspect(zip_path):
    """检查备份文件，返回 manifest；不合法就抛 BackupError。"""
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise BackupError("这不是有效的备份文件（不是 zip）")
    with z:
        names = set(z.namelist())
        if "manifest.json" not in names or "library.db" not in names:
            raise BackupError("这不是 AI知识库 的备份文件（缺少 manifest.json 或 library.db）")
        try:
            m = json.loads(z.read("manifest.json").decode("utf-8"))
        except Exception:
            raise BackupError("备份文件里的 manifest.json 坏了")
        # 兼容旧名「收藏学习台」
        if m.get("app") not in ("AI知识库", "收藏学习台"):
            raise BackupError("这不是 AI知识库 的备份文件")
        if int(m.get("format", 0)) > FORMAT_VERSION:
            raise BackupError("这个备份来自更新版本的 AI知识库，请先更新程序再恢复")
        total = sum(i.file_size for i in z.infolist())
        if total > MAX_UNPACKED:
            raise BackupError("备份文件解压后太大，已拒绝")
        for i in z.infolist():
            p = PurePosixPath(i.filename)
            if p.is_absolute() or ".." in p.parts:
                raise BackupError("备份文件里有不安全的路径，已拒绝")
        m["notes_in_zip"] = sum(1 for n in names if n.startswith(NOTES_PREFIX) and not n.endswith("/"))
        return m


def restore(zip_path, keep_current_keys=True):
    """用备份覆盖当前数据。返回 {restored, safety_backup, ...}。"""
    m = inspect(zip_path)

    # 1. 先把当前数据自动备份一份
    safety, _ = create(include_keys=True, dest_dir=BACKUP_DIR, tag="恢复前自动备份")

    cur = config.load()
    with zipfile.ZipFile(zip_path) as z, tempfile.TemporaryDirectory() as tmp:
        # 2. 数据库：用 SQLite 在线备份把内容整体覆盖进当前数据库（程序不用重启）
        src_path = Path(tmp) / "restore.db"
        src_path.write_bytes(z.read("library.db"))
        src = sqlite3.connect(src_path)
        try:
            ok = src.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise BackupError("备份里的数据库已损坏，没有恢复")
            src.execute("SELECT COUNT(*) FROM items").fetchone()
        except sqlite3.DatabaseError as e:
            raise BackupError(f"备份里的数据库读不了：{e}")
        with db._write_lock:
            src.backup(db.conn())
        src.close()
        db.init()  # 补齐新版本才有的字段，并把中断状态放回队列

        # 3. 设置：恢复备份里的设置；备份里没有 Key 时保留当前的 Key
        try:
            s = json.loads(z.read("settings.json").decode("utf-8"))
        except Exception:
            s = {}
        if keep_current_keys:
            for k in ("api_key", "deep_api_key"):
                if not s.get(k) and cur.get(k):
                    s[k] = cur[k]
        lib_saved = s.get("library_dir") or ""
        if lib_saved and not Path(lib_saved).expanduser().exists():
            s["library_dir"] = cur.get("library_dir", "")  # 原来的路径在这台电脑上不存在，用这台电脑的
        config.save({k: v for k, v in s.items() if k in config.DEFAULTS})

        # 4. 笔记文件：解压到这台电脑的笔记目录
        lib = config.library_dir()
        n_files = 0
        for info in z.infolist():
            if not info.filename.startswith(NOTES_PREFIX) or info.is_dir():
                continue
            rel = info.filename[len(NOTES_PREFIX):]
            target = (lib / rel).resolve()
            if lib.resolve() not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as fsrc, open(target, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
            n_files += 1

    # 5. 笔记路径换成这台电脑上的路径；找不到的就按数据库内容重新生成
    old_root = m.get("library_dir") or ""
    fixed = regenerated = 0
    for r in db.conn().execute("SELECT id, md_path FROM items WHERE status='done'").fetchall():
        item = db.get(r["id"])
        new_path = None
        if r["md_path"] and old_root:
            try:
                rel = Path(r["md_path"]).relative_to(old_root)
                cand = lib / rel
                if cand.exists():
                    new_path = str(cand)
            except ValueError:
                pass
        if new_path:
            if new_path != r["md_path"]:
                db.update(r["id"], md_path=new_path)
                fixed += 1
        else:
            try:
                item["md_path"] = None
                db.update(r["id"], md_path=notes.write(item))
                regenerated += 1
            except Exception:
                pass

    st = db.stats()
    return {"ok": True, "items": st["total"], "done": st["by_status"].get("done", 0), "learned": st["learned"],
            "notes": n_files, "regenerated": regenerated, "safety_backup": str(safety), "from": m.get("created", "")}


def list_auto_backups():
    if not BACKUP_DIR.exists():
        return []
    out = []
    for f in sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        out.append({"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime})
    return out[:20]


def prune_auto_backups(keep=5):
    if not BACKUP_DIR.exists():
        return
    files = sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        try:
            os.remove(f)
        except OSError:
            pass
