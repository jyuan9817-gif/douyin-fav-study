"""知识蒸馏与 AI 问答：按分类把多篇笔记收成一份可复用的「技能稿」，再基于它提问。

对齐抖音知识库「蒸馏 + 问答」用法；不依赖仓颉，直接用当前 AI 接口。
存盘：data/蒸馏技能/{分类}.md + .json
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
from pathlib import Path

from . import cards, config, db, llm

SKILLS_DIR = config.DATA / "蒸馏技能"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)
BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')
# 蒸馏一次大概要多久（用来估进度条，不挡实际完成）
_EST_SEC = 90.0


def safe_name(cat: str) -> str:
    return (BAD.sub("-", (cat or "未分类").strip()) or "未分类")[:60]


def skill_paths(cat: str) -> tuple[Path, Path]:
    base = SKILLS_DIR / safe_name(cat)
    return base.with_suffix(".md"), base.with_suffix(".json")


def list_skills():
    out = []
    for p in sorted(SKILLS_DIR.glob("*.json")):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "category": meta.get("category") or p.stem,
            "title": meta.get("title") or p.stem,
            "updated_at": meta.get("updated_at"),
            "note_count": meta.get("note_count", 0),
            "md_path": str(skill_paths(meta.get("category") or p.stem)[0]),
        })
    return out


def get_skill(cat: str):
    md, jp = skill_paths(cat)
    if not jp.exists():
        return None
    meta = json.loads(jp.read_text(encoding="utf-8"))
    meta["body"] = md.read_text(encoding="utf-8") if md.exists() else ""
    return meta


def distill(cat: str, settings=None, on_stage=None):
    """同步蒸馏。on_stage(stage, label, pct) 可选，给进度条用。"""
    def stage(key, label, pct):
        if on_stage:
            on_stage(key, label, pct)

    settings = settings or config.load()
    stage("prepare", "整理笔记材料…", 8)
    notes = cards._notes_in_category(cat, limit=60)
    if len(notes) < 2:
        raise ValueError(f"「{cat}」至少需要 2 条已完成笔记才能蒸馏")

    system = (
        "你是知识蒸馏助手。把同一分类的多条学习笔记，蒸馏成一份可给 AI Agent 直接使用的技能说明。"
        "只依据材料，不编造。输出 Markdown 正文（不要包代码块），结构必须包含：\n"
        "# 技能名称\n## 适用场景\n## 核心原则（5-10条）\n## 可执行步骤\n"
        "## 常见坑\n## 自检清单\n## 参考来源\n全部简体中文。"
    )
    user = f"分类：{cat}\n\n材料：\n{cards._material(notes)}"
    stage("llm", "AI 正在蒸馏技能稿…", 18)
    text, used = llm.chat(
        settings,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        json_mode=False,
        temperature=0.25,
        max_tokens=3500,
        timeout=180,
    )
    config.add_tokens(used)
    stage("save", "写入技能文件…", 92)
    body = (text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", body).strip()

    title = cat
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip() or cat
            break

    meta = {
        "category": cat,
        "title": title,
        "note_count": len(notes),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chars": len(body),
    }
    md, jp = skill_paths(cat)
    md.write_text(body + "\n", encoding="utf-8")
    jp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    meta["body"] = body
    stage("done", "蒸馏完成", 100)
    return meta


class DistillRunner:
    """蒸馏放后台跑，切页也能在状态里看到进度。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "busy": False, "category": "", "stage": "", "label": "",
            "pct": 0, "eta": 0, "error": "", "just_done": False,
            "started_at": 0, "title": "",
        }

    def snapshot(self):
        with self._lock:
            st = dict(self._state)
        if st.get("busy") and st.get("started_at"):
            elapsed = time.time() - st["started_at"]
            # 卡在 llm 阶段时用时间估进度，避免长时间停在同一数字
            if st.get("stage") == "llm":
                frac = min(elapsed / _EST_SEC, 0.92)
                st["pct"] = max(st.get("pct") or 18, int(18 + frac * 70))
                st["eta"] = max(int(_EST_SEC - elapsed), 3)
            elif st.get("stage") == "prepare":
                st["pct"] = max(st.get("pct") or 5, 8)
                st["eta"] = max(int(_EST_SEC - elapsed), 5)
            else:
                st["eta"] = max(int(_EST_SEC - elapsed), 2)
        return st

    def start(self, cat: str):
        cat = (cat or "").strip()
        if not cat:
            raise ValueError("请选择分类")
        with self._lock:
            if self._state.get("busy"):
                raise ValueError(f"正在蒸馏「{self._state.get('category') or '分类'}」，请稍后再试")
            self._state.update(
                busy=True, category=cat, stage="prepare", label="准备中…",
                pct=3, eta=int(_EST_SEC), error="", just_done=False,
                started_at=time.time(), title="",
            )
        threading.Thread(target=self._run, args=(cat,), daemon=True, name="distill").start()
        return {"ok": True, "started": True, "category": cat}

    def ack_done(self):
        """前端读到完成后清掉 just_done，避免反复弹提示。"""
        with self._lock:
            self._state["just_done"] = False
            self._state["error"] = ""

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _run(self, cat: str):
        try:
            def on_stage(key, label, pct):
                self._set(stage=key, label=label, pct=pct)

            meta = distill(cat, config.load(), on_stage=on_stage)
            self._set(
                busy=False, stage="done", label="蒸馏完成", pct=100, eta=0,
                just_done=True, title=meta.get("title") or cat, error="",
            )
        except Exception as e:
            traceback.print_exc()
            self._set(
                busy=False, stage="error", label="蒸馏失败", pct=0, eta=0,
                just_done=True, error=str(e),
            )


runner = DistillRunner()


def ask(cat: str, question: str, settings=None, history=None):
    """基于已蒸馏技能（没有则用该分类笔记摘要）回答问题。"""
    settings = settings or config.load()
    q = (question or "").strip()
    if not q:
        raise ValueError("请输入问题")

    skill = get_skill(cat)
    if skill and skill.get("body"):
        context = skill["body"][:12000]
        source = "skill"
    else:
        notes = cards._notes_in_category(cat, limit=40)
        if not notes:
            raise ValueError(f"「{cat}」还没有可问答的笔记，请先处理收藏并生成笔记")
        context = cards._material(notes)
        source = "notes"

    system = (
        "你是学习者的知识助手。只根据下面「知识库内容」回答；没有的信息要明确说不知道，不要编造。"
        "用简体中文，条理清楚，尽量给可执行建议。控制在 500 字以内，除非用户要求展开。\n\n"
        f"【知识库内容｜来源={source}｜分类={cat}】\n{context}"
    )
    messages = [{"role": "system", "content": system}]
    for h in (history or [])[-6:]:
        role = "assistant" if h.get("role") in ("assistant", "bot") else "user"
        content = str(h.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": q})

    text, used = llm.chat(
        settings, messages, json_mode=False, temperature=0.3, max_tokens=1200, timeout=120,
    )
    config.add_tokens(used)
    return {"answer": (text or "").strip(), "source": source, "category": cat, "tokens": used}
