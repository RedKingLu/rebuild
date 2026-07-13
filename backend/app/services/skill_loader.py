"""SKILL.md Loader — reads frontmatter + body from source/skills/** (R9-5-3, T-7).

For each Skill with a directory_path, reads:
  - YAML frontmatter (between --- markers): name/description/metadata
  - Markdown body (everything after the second --- marker)

Public API:
  load_skill_body(directory_path, max_chars) -> dict
  load_skills_for_stage(stage, max_chars_each) -> list[dict]

On any read failure → capability_status='not_connected', no silent swallow (公理3).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("rebuild.skill_loader")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter and body. Returns (meta_dict, body_str)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text  # no frontmatter — treat all as body
    yaml_block, body = m.group(1), m.group(2)
    meta: dict = {}
    for line in yaml_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body.strip()


def load_skill_body(directory_path: str, max_chars: int = 4000) -> dict:
    """Load a single SKILL.md from directory_path.

    Returns a dict with keys:
      skill_id, name, description, body, capability_status, directory_path, source
    capability_status: 'real' | 'not_connected'
    """
    if not directory_path:
        return _not_connected("", "no directory_path")

    skill_file = Path(directory_path.rstrip("/")) / "SKILL.md"
    if not skill_file.exists():
        logger.warning("SKILL.md not found at %s", skill_file)
        return _not_connected(directory_path, f"SKILL.md not found: {skill_file}")

    try:
        raw = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("Failed to read %s: %s", skill_file, e)
        return _not_connected(directory_path, f"read error: {e}")

    meta, body = _parse_frontmatter(raw)
    name = meta.get("name") or skill_file.parent.name
    description = meta.get("description", "")
    source = meta.get("source", "")

    trimmed_body = body[:max_chars] if len(body) > max_chars else body

    return {
        "name": name,
        "description": description,
        "body": trimmed_body,
        "body_chars": len(trimmed_body),
        "body_truncated": len(body) > max_chars,
        "capability_status": "real",
        "directory_path": directory_path,
        "source": source,
        "meta": meta,
    }


def load_skills_for_stage(
    stage: str,
    max_chars_each: int = 4000,
    skills_db: Optional[list] = None,
) -> list[dict]:
    """Load SKILL.md bodies for skills matching stage (common | stage).

    skills_db: pre-fetched list of SkillDefinition-like dicts with 'directory_path',
               'category', 'name'. If None, falls back to disk scan.
    Returns list of dicts with name/body/capability_status.
    """
    if skills_db is not None:
        results = []
        for s in skills_db:
            dp = s.get("directory_path") or ""
            loaded = load_skill_body(dp, max_chars_each)
            loaded["skill_id"] = s.get("skill_id", "")
            loaded["category"] = s.get("category", "")
            if not loaded.get("name"):
                loaded["name"] = s.get("name", "")
            results.append(loaded)
        return results

    # Disk fallback: scan source/skills/{common,stage}
    import os
    # 单一事实源：默认从 settings.source_path 派生（config.py source_dir），env 仍可覆盖。
    from app.core.config import settings
    default_root = str(settings.source_path / "skills")
    skill_root = Path(os.environ.get("SKILL_SOURCE_ROOT", default_root))
    results = []
    for cat in ["common", stage]:
        cat_dir = skill_root / cat
        if not cat_dir.exists():
            continue
        for skill_dir in sorted(cat_dir.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                loaded = load_skill_body(str(skill_dir) + "/", max_chars_each)
                loaded["skill_id"] = skill_dir.name
                loaded["category"] = cat
                results.append(loaded)
    return results


def _not_connected(directory_path: str, reason: str) -> dict:
    return {
        "name": Path(directory_path.rstrip("/")).name if directory_path else "",
        "description": "",
        "body": "",
        "body_chars": 0,
        "body_truncated": False,
        "capability_status": "not_connected",
        "directory_path": directory_path,
        "source": "",
        "meta": {},
        "not_connected_reason": reason,
    }
