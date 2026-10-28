"""知识卡片：按分类把多条学习笔记汇总成「四格」卡片（对齐抖音知识库）。

存盘：data/知识卡片/{安全分类名}.json + .md
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import config, db, llm

CARDS_DIR = config.DATA / "知识卡片"
CARDS_DIR.mkdir(parents=True, exist_ok=True)
MIN_NOTES = 3  # 少于这个数量不生成，避免空卡

BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(cat: str) -> str:
    s = BAD.sub("-", (cat or "未分类").strip()) or "未分类"
    return s[:60]


def card_paths(cat: str) -> tuple[Path, Path]:
    base = CARDS_DIR / safe_name(cat)
    return base.with_suffix(".json"), base.with_suffix(".md")


def _notes_in_category(cat: str, limit=80):
    rows = []
    for r in db.conn().execute(
        "SELECT id,title,author,url,note_json,deep_json FROM items "
        "WHERE status='done' AND category=? ORDER BY id DESC LIMIT ?",
        (cat, limit),
    ):
        d = dict(r)
        d["note"] = json.loads(d.pop("note_json") or "null") or {}
        d["deep"] = json.loads(d.pop("deep_json") or "null") or {}
        rows.append(d)
    return rows


def list_cards():
    """分类列表 + 是否已有卡片。"""
    cats = db.stats().get("categories") or []
    out = []
    for c in cats:
        cat = c.get("category") or "未分类"
        jp, mp = card_paths(cat)
        meta = None
        if jp.exists():
            try:
                meta = json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                meta = None
        out.append({
            "category": cat,
            "count": int(c.get("n") or 0),
            "learned": int(c.get("learned") or 0),
            "has_card": bool(meta),
            "updated_at": meta.get("updated_at") if meta else None,
            "thesis": (meta or {}).get("thesis") or "",
            "ready": int(c.get("n") or 0) >= MIN_NOTES,
            "min_notes": MIN_NOTES,
        })
    return out


def get_card(cat: str):
    jp, mp = card_paths(cat)
    if not jp.exists():
        return None
    data = json.loads(jp.read_text(encoding="utf-8"))
    data["md_path"] = str(mp) if mp.exists() else ""
    data["md"] = mp.read_text(encoding="utf-8") if mp.exists() else ""
    return data


def _material(notes):
    lines = []
    for i, it in enumerate(notes, 1):
        n = it.get("note") or {}
        title = n.get("title") or it.get("title") or f"笔记{i}"
        lines.append(f"### {i}. {title}")
        if n.get("summary"):
            lines.append(f"总结：{n['summary']}")
        for p in (n.get("points") or [])[:6]:
            lines.append(f"- 观点：{p}")
        for s in (n.get("steps") or [])[:5]:
            lines.append(f"- 步骤：{s}")
        deep = it.get("deep") or {}
        for c in (deep.get("conclusions") or [])[:3]:
            lines.append(f"- 精读结论：{c}")
        lines.append("")
    return "\n".join(lines)[:14000]


def generate(cat: str, settings=None):
    settings = settings or config.load()
    notes = _notes_in_category(cat)
    if len(notes) < MIN_NOTES:
        raise ValueError(f"「{cat}」只有 {len(notes)} 条笔记，至少 {MIN_NOTES} 条再生成卡片")

    system = (
        "你是知识库管家。把同一分类下多条抖音学习笔记，汇总成一张「知识卡片」。"
        "只依据给定材料，不编造。严格输出一个 JSON 对象，不要 Markdown 代码块。"
        "字段："
        '{"thesis":"一句话结论（≤40字）",'
        '"what":"01 讲了什么：3-6 条要点",'
        '"hooks":"02 钩子：吸引人/可传播的说法 2-4 条",'
        '"actions":"03 能抄什么：可执行步骤 3-8 条",'
        '"followup":"04 要不要跟进：建议与风险 2-4 条",'
        '"tags":["关键词"],'
        '"sources":["引用的笔记标题"]}'
    )
    user = f"分类：{cat}\n笔记数：{len(notes)}\n\n材料：\n{_material(notes)}"
    text, used = llm.chat(
        settings,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        json_mode=True,
        temperature=0.2,
        max_tokens=2500,
    )
    config.add_tokens(used)
    raw = llm.parse_json(text)

    def as_list(v):
        if isinstance(v, str):
            return [x.strip(" -•\t") for x in v.splitlines() if x.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    card = {
        "category": cat,
        "thesis": str(raw.get("thesis") or "").strip() or f"{cat} 知识汇总",
        "what": as_list(raw.get("what")),
        "hooks": as_list(raw.get("hooks")),
        "actions": as_list(raw.get("actions")),
        "followup": as_list(raw.get("followup")),
        "tags": as_list(raw.get("tags"))[:12],
        "sources": as_list(raw.get("sources"))[:20] or [str(n.get("note", {}).get("title") or n.get("title") or "") for n in notes[:12]],
        "note_count": len(notes),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "min_notes": MIN_NOTES,
    }
    jp, mp = card_paths(cat)
    jp.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(_render_md(card), encoding="utf-8")
    return card


def _render_md(card: dict) -> str:
    cat = card["category"]
    lines = [
        "---",
        f'title: "{cat} · 知识卡片"',
        f"category: {cat}",
        f"date: {card.get('updated_at', '')}",
        f"note_count: {card.get('note_count', 0)}",
        "tags: [" + ", ".join(card.get("tags") or []) + "]",
        "---",
        "",
        f"# {cat} · 知识卡片",
        "",
        "## 这条讲了什么",
        "",
        card.get("thesis") or "",
        "",
        "## 拆解四格",
        "",
        "### 01 讲了什么",
        "",
        *[f"- {x}" for x in card.get("what") or ["（暂无）"]],
        "",
        "### 02 钩子",
        "",
        *[f"- {x}" for x in card.get("hooks") or ["（暂无）"]],
        "",
        "### 03 能抄什么",
        "",
        *[f"- {x}" for x in card.get("actions") or ["（暂无）"]],
        "",
        "### 04 要不要跟进",
        "",
        *[f"- {x}" for x in card.get("followup") or ["（暂无）"]],
        "",
        "## 来源笔记",
        "",
        *[f"- {x}" for x in card.get("sources") or []],
        "",
    ]
    return "\n".join(lines)


def delete_card(cat: str):
    jp, mp = card_paths(cat)
    for p in (jp, mp):
        if p.exists():
            p.unlink()
    return True
