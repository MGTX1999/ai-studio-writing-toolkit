"""访谈式灵感采集：先问作者，再把回答组合成可落库的灵感卡。

它不调用模型。问题由项目/章节/人物/伏笔上下文生成；回答由固定模板组合，
因此结果可解释、可复现。作者可以在 Web 里逐题作答，也可以把问答交给模型
继续扩写。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from store import (
    MAX_CHAPTER_N,
    MAX_MATERIAL_LEN,
    LongformError,
    Store,
    _require_int,
    _require_str,
)

_CATEGORY_LABELS = {
    "desire": "人物欲望",
    "obstacle": "阻力",
    "secret": "秘密",
    "motif": "意象",
    "twist": "反转",
    "sensory": "感官",
    "relation": "关系",
    "hook": "章末钩子",
    "free": "自由补充",
}


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _default_chapter_n(s: Store, pid: str, chapter_n: Optional[int]) -> int:
    if chapter_n is not None:
        return _require_int(chapter_n, "chapter_n", minimum=0,
                            maximum=MAX_CHAPTER_N)
    chapters = s.chapter_list(pid)
    return (max([int(c["n"]) for c in chapters] + [0]) + 1)


def inspiration_interview(s: Store, pid: str,
                          chapter_n: Optional[int] = None,
                          theme: str = "", count: int = 6) -> dict:
    """生成一组上下文相关的问题；不写库。"""
    s._require_project(pid)
    theme = _require_str(theme or "", "theme", allow_empty=True,
                         max_len=2000).strip()
    count = _require_int(count, "count", minimum=3, maximum=10)
    n = _default_chapter_n(s, pid, chapter_n)
    project = s.project_get(pid) or {}
    chapter = s.chapter_get(pid, n) if n >= 0 else None
    characters = [item.get("name") for item in s.bible_list(pid, "character")
                  if item.get("name")][:4]
    character = characters[0] if characters else "主角"
    open_fs = [item.get("text") for item in s.foreshadow_list(pid, "open")
               if item.get("text")][:3]
    prop = _clip(open_fs[0], 20) if open_fs else "一个反复出现的物件"
    chapter_title = (chapter or {}).get("title") or f"第{n}章"
    theme_text = f"围绕「{theme}」，" if theme else ""

    base = [
        ("desire", "人物欲望",
         f"{theme_text}在「{chapter_title}」里，{character}最想保住什么？"
         f"如果失去它，他会做出什么平时不会做的事？",
         "把抽象目标变成可观察的行动和代价",
         "例：保住邮袋里的名单；一旦失去，他会去求最不想求的人。"),
        ("obstacle", "阻力",
         f"什么东西阻止{character}立刻得到想要的结果？"
         "为什么不能靠力量、钱或解释直接解决？",
         "确认冲突不是一句话就能消掉的假阻力",
         "例：对方掌握他母亲的把柄；硬抢会让全城知道。"),
        ("secret", "秘密",
         "谁掌握一个说出口就会改变关系的秘密？"
         "他为什么现在才说，而不是更早或更晚？",
         "让信息差变成人物选择，而不是旁白解释",
         "例：码头会计知道船期被改过；他说出来是为了保护妹妹。"),
        ("motif", "意象",
         f"给这一章一个可重复出现的物件、声音或气味（可以是「{prop}」）。"
         "它在开头和结尾各是什么状态？",
         "用同一个意象承载变化，减少解释性文字",
         "例：一枚生锈的钥匙，开头打不开门，结尾打开的是抽屉。"),
        ("twist", "反转",
         f"如果{character}的计划在中途失败，最糟但合理的结果是什么？"
         "谁会因此获得不该有的优势？",
         "反转要改变局面，而不是只让角色吃惊",
         "例：他以为对方要钱，其实对方要的是公开道歉。"),
        ("sensory", "感官",
         "这一章最重要的场景，读者最先闻到、听到或触到什么？"
         "这个感官细节会怎样在结尾再次出现？",
         "用一个具体感官替代形容词堆叠",
         "例：雨棚上的铁皮声；结尾同一声音变成追兵的脚步。"),
        ("relation", "关系",
         "哪两个人物对同一件事的理解完全相反？谁先误判，代价由谁承担？",
         "让关系冲突有信息差和后果",
         "例：姐姐以为妹妹在撒谎，其实妹妹在替她遮掩。"),
        ("hook", "章末钩子",
         "章末最后一个动作是什么？读者会因此追问什么？"
         "这个追问在下一章开头能不能立刻接住？",
         "章末留问题，但不要靠故弄玄虚",
         "例：他把信投入火里，火里露出一行没烧掉的日期。"),
    ]
    questions = []
    for idx, (category, label, question, why, placeholder) in enumerate(base[:count], 1):
        questions.append({
            "id": f"q{idx}",
            "category": category,
            "label": label,
            "question": question,
            "why": why,
            "placeholder": placeholder,
        })

    return {
        "ok": True,
        "project": pid,
        "chapter_n": n,
        "chapter_title": chapter_title,
        "theme": theme,
        "context": {
            "project_name": project.get("name") or pid,
            "characters": characters,
            "open_foreshadow": open_fs,
            "has_chapter": bool(chapter),
        },
        "questions": questions,
        "how_to_answer": "逐题作答；答不出可跳过。回答越具体（动作、地点、代价），"
                         "生成的灵感卡越可用。",
    }


def _normalize_answers(answers: Any) -> List[dict]:
    if answers is None:
        raise LongformError("answers 不能为空：至少回答一个问题")
    rows: List[dict] = []
    if isinstance(answers, str):
        text = answers.strip()
        if text:
            rows.append({"id": "free", "category": "free",
                         "question": "自由补充", "answer": text})
    elif isinstance(answers, dict):
        for key, value in answers.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("id", str(key))
            else:
                item = {"id": str(key), "answer": str(value)}
            rows.append(item)
    elif isinstance(answers, list):
        for item in answers:
            if isinstance(item, str):
                rows.append({"id": "free", "answer": item})
            elif isinstance(item, dict):
                rows.append(dict(item))
    else:
        raise LongformError("answers 必须是对象、数组或字符串")

    clean: List[dict] = []
    for item in rows[:10]:
        text = str(item.get("answer") or "").strip()
        if not text:
            continue
        if len(text) > 5000:
            raise LongformError("单条回答超过 5000 字符，请精简后再提交")
        category = str(item.get("category") or "free").strip()
        if category not in _CATEGORY_LABELS:
            category = "free"
        clean.append({
            "id": str(item.get("id") or item.get("question_id") or "free"),
            "category": category,
            "question": str(item.get("question") or "").strip(),
            "answer": text,
        })
    if not clean:
        raise LongformError("answers 里没有有效回答")
    return clean


def _by_category(answers: List[dict]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in answers:
        key = item["category"]
        if key not in out:
            out[key] = item["answer"]
    return out


def _idea(title: str, content: str, tags: List[str]) -> dict:
    return {"title": title, "content": content.strip(), "kind": "idea",
            "tags": tags, "source": "灵感访谈", "source_kind": "dialog"}


def inspiration_synthesize(s: Store, pid: str,
                           answers: Any,
                           chapter_n: Optional[int] = None,
                           theme: str = "",
                           save: bool = True,
                           tags: Optional[List[str]] = None) -> dict:
    """把回答组合成 1–4 张灵感卡；默认落库为素材。"""
    s._require_project(pid)
    theme = _require_str(theme or "", "theme", allow_empty=True,
                         max_len=2000).strip()
    clean = _normalize_answers(answers)
    by = _by_category(clean)
    n = _default_chapter_n(s, pid, chapter_n)
    project = s.project_get(pid) or {}
    chapter = s.chapter_get(pid, n) if n >= 0 else None
    character = next((item.get("name") for item in s.bible_list(pid, "character")
                      if item.get("name")), "主角")
    setting = (chapter or {}).get("title") or f"第{n}章"
    extra_tags = [str(t).strip() for t in (tags or []) if str(t).strip()]

    ideas: List[dict] = []
    if by.get("desire") and by.get("obstacle"):
        ideas.append(_idea(
            "核心冲突：" + (theme or "想要 × 不能"),
            f"**目标**：{by['desire']}\n"
            f"**阻力**：{by['obstacle']}\n"
            f"**可写场景**：让{character}在「{setting}」做一件看似能解决问题、"
            "实际会加重代价的事。\n"
            f"**下一问**：这件事成功一半时，谁先发现代价？",
            ["灵感访谈", "冲突", "目标/阻力"] + extra_tags,
        ))
    if by.get("secret") and by.get("relation"):
        ideas.append(_idea(
            "秘密与关系反转",
            f"**秘密**：{by['secret']}\n"
            f"**关系错位**：{by['relation']}\n"
            f"**写法**：先让读者和{character}一起误判，再用一个动作揭示"
            "“谁在保护谁”。揭示时不要解释动机，让下一个动作承担后果。",
            ["灵感访谈", "秘密", "关系"] + extra_tags,
        ))
    if by.get("motif") or by.get("sensory"):
        ideas.append(_idea(
            "可复用意象",
            f"**意象**：{by.get('motif') or '（未答）'}\n"
            f"**感官锚点**：{by.get('sensory') or '（未答）'}\n"
            "**写法**：开头用一次中性描写；中段让它在压力下变形；结尾用同一个"
            "感官信号完成回扣。不要把它解释成象征。",
            ["灵感访谈", "意象", "感官"] + extra_tags,
        ))
    if by.get("twist") or by.get("hook"):
        ideas.append(_idea(
            "中段失败与章末钩子",
            f"**失败**：{by.get('twist') or '（未答）'}\n"
            f"**章末追问**：{by.get('hook') or '（未答）'}\n"
            "**写法**：失败要改变局面，不只是一次吃惊；章末最后一句落在一个"
            "未完成的动作上，下一章开头立刻接住这个动作。",
            ["灵感访谈", "反转", "钩子"] + extra_tags,
        ))
    if not ideas:
        first = clean[0]
        ideas.append(_idea(
            "灵感起点",
            f"**{_CATEGORY_LABELS.get(first['category'], '回答')}**：{first['answer']}\n"
            "**下一步**：把这句话变成一个角色在具体地点做出的动作，再给他一个"
            "立刻要付的代价。",
            ["灵感访谈", "自由补充"] + extra_tags,
        ))

    saved = []
    if save:
        for idea in ideas:
            result = s.material_add(
                pid, idea["title"], idea["content"], kind="idea",
                source=idea["source"], source_kind=idea["source_kind"],
                tags=idea["tags"], chapter=n,
                data={"theme": theme, "answers": clean,
                      "chapter_n": n, "generated_by": "inspiration_synthesize"},
            )
            saved.append(result.get("id"))
            idea["saved_id"] = result.get("id")

    return {
        "ok": True,
        "project": pid,
        "chapter_n": n,
        "theme": theme,
        "ideas": ideas,
        "saved": saved,
        "answers_used": len(clean),
        "categories": sorted(by),
        "note": "模板组合，不是模型创作；保存后可在「素材与知识库」继续补正文。",
    }
