"""Full-Stack Profiler — P1 建档【采集】工具 (R9-3C; R17.5 P1 重构为采集层).

R17.5 P1 (AGENTS §2.3 / 禁止项 25/26): identification is done by the LLM (Node Worker
Agent) via ProfilingService — this module is now the deterministic **collection** tool.
It ONLY gathers raw facts (list files / structure / case-insensitive candidates /
dependency-manifest & config & test raw contents — redacted / ext counts) and produces
NO identification verdict. 主语言/框架/依赖判定/入口点/配置解读/infra 解读/盲区发声 都由 LLM
在 RealP1Handler 里对 collect_facts() 的事实包推理产出。

已删除（R17.5 P1 WP-3，均为按栈枚举的样本值硬编码规则，改 LLM）：
  HC-P1-01 "扩展名多=主语言" + .gif 当语言（_item4_language_id primary 判定）
  HC-P1-02 依赖清单文件名白名单（_item7_dependency_id 只认 package.json/requirements.txt）
  HC-P1-03 入口 pattern endswith("index.js")（_item8_entry_point_id）
  HC-P1-04 config 文件名大小写敏感精确匹配（_item12 CONFIG_FILES exact）
  HC-P1-05 TEST_DIR_NAMES 全小写敏感（_item9）
  HC-P1-06 framework/infra 关键词/pattern 表（_item5_framework_id / _item11 infra_patterns）

collect_facts() 是图 P1 handler 的采集入口；profile() 保留为遗留 /profile 路由与
run_profiling 工具的采集通道（只产采集产物，不产 LLM 识别）。All config/secret values redacted.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from app.services.workspace_service import workspace_path

logger = logging.getLogger("rebuild.full_stack_profiler")

# Directories to skip during scan
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "__pycache__", ".venv", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".nuxt", ".rebuild",
}

# Authoritative P1 建档 artifact key list — SINGLE SOURCE OF TRUTH (R9-5-8 T2).
# The frontend StagePageP1 / profiling-summary endpoint read this list instead of
# hardcoding their own. In the graph P1 flow the collection layer writes file_index/
# source_structure/cicd_inventory/doc_inventory and the LLM (ProfilingService) writes
# tech_stack/dependency_draft/entry_points/config_inventory/infra_clues/test_inventory/
# module_structure/uncertainty_manifest.
PROFILING_ITEMS: list[tuple[str, str]] = [
    ("file_index", "文件索引"),
    ("source_structure", "源码结构"),
    ("module_structure", "模块结构"),
    ("tech_stack", "技术栈"),
    ("dependency_draft", "依赖清单"),
    ("entry_points", "入口点"),
    ("test_inventory", "测试清单"),
    ("cicd_inventory", "CI/CD 清单"),
    ("infra_clues", "基础设施线索"),
    ("config_inventory", "配置清单"),
    ("doc_inventory", "文档清单"),
    ("uncertainty_manifest", "不确定性清单"),
]

# Build/manifest file NAMES (kept as reference candidates for the LLM; NOT a verdict).
BUILD_FILES = {
    "pom.xml": "maven", "build.gradle": "gradle", "build.gradle.kts": "gradle-kts",
    "settings.gradle": "gradle", "Makefile": "make", "CMakeLists.txt": "cmake",
    "package.json": "npm", "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm",
    "requirements.txt": "pip", "setup.py": "setuptools", "pyproject.toml": "python-build",
    "Cargo.toml": "cargo", "go.mod": "go-mod", "go.sum": "go",
    "Gemfile": "bundler", "composer.json": "composer",
    "Android.bp": "soong", "BUILD": "bazel", "WORKSPACE": "bazel",
}
_BUILD_SUFFIXES = (".csproj", ".vbproj", ".fsproj", ".sln")

# Dependency manifest candidate names (case-insensitive) + suffixes — collection lists
# ALL of them across stacks; the LLM parses/judges dependencies (no whitelist verdict).
_DEP_MANIFEST_NAMES = {
    "package.json", "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
    "pipfile", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "cargo.toml",
    "gemfile", "composer.json", "packages.config", "paket.dependencies",
}
_DEP_MANIFEST_SUFFIXES = (".csproj", ".vbproj", ".fsproj")

# Config file candidate names (case-insensitive) + suffixes / dir prefixes.
_CONFIG_NAMES = {
    ".env", ".env.example", "appsettings.json", "web.config", "app.config",
    "application.yml", "application.yaml", "application.properties",
    "tsconfig.json", "jsconfig.json", "dockerfile", "docker-compose.yml",
    "docker-compose.yaml", ".gitlab-ci.yml", "jenkinsfile", ".travis.yml", "makefile",
}
_CONFIG_SUFFIXES = (".config",)

# Test directory candidate names (matched case-INsensitively — HC-P1-05 fix).
TEST_DIR_NAMES = {"test", "tests", "spec", "specs", "__tests__", "e2e", "integration"}

# Extension → language hint map (reference labels only; NOT a primary-language verdict).
LANG_EXTENSIONS = {
    ".java": "Java", ".kt": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".jsx": "React JSX",
    ".tsx": "React TSX", ".go": "Go", ".rs": "Rust",
    ".cs": "C#", ".vb": "Visual Basic", ".fs": "F#",
    ".c": "C", ".cpp": "C++", ".h": "C/C++ Header", ".hpp": "C++ Header",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".m": "Objective-C",
    ".r": "R", ".sql": "SQL", ".sh": "Shell", ".bash": "Bash",
    ".tf": "Terraform", ".proto": "Protobuf", ".graphql": "GraphQL",
}

CICD_PATTERNS = [
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".gitlab-ci.yml", ".github/workflows/", "Jenkinsfile",
    ".travis.yml", "deploy/", "k8s/", "helm/", "terraform/",
    ".drone.yml", "bitbucket-pipelines.yml",
]

DOC_PATTERNS = {"readme.md", "readme", "changelog.md", "changelog", "contributing.md",
                "license", "license.md", "notice", "authors", "code_of_conduct.md"}

# DB/connection indicators to surface (redacted) — collection reports presence, the LLM
# interprets the actual infra type (no keyword→label verdict table, HC-P1-06 fix).
_CONN_INDICATORS = ["jdbc:", "connectionstring", "connection_string", "datasource",
                    "database_url", "db_url", "redis_url", "kafka_broker", "mongodb://",
                    "server=", "data source="]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(text: str) -> str:
    """采集层脱敏（连接串/密钥值 → [REDACTED]），复用 intake_service 单一事实源。"""
    try:
        from app.services.intake_service import redact_config_text
        return redact_config_text(text)
    except Exception:
        return text


class FullStackProfiler:
    """P1 建档【采集】工具：collect_facts() 产事实包喂 LLM；profile() 为遗留采集通道。"""

    def __init__(self, trace_writer=None, audit_writer=None):
        self.trace = trace_writer
        self.audit = audit_writer
        self.gaps: list[dict] = []
        self.warnings: list[str] = []

    # ── 低层扫描（采集） ────────────────────────────────────────────────
    def _scan(self, src: Path) -> list[dict]:
        files: list[dict] = []
        if not src.exists():
            return files
        for f in sorted(src.rglob("*")):
            parts = f.relative_to(src).parts
            if any(p in SKIP_DIRS for p in parts):
                continue
            if f.is_file():
                try:
                    size = f.stat().st_size
                except Exception:
                    size = 0
                files.append({"path": str(f.relative_to(src)), "size": size,
                              "ext": f.suffix.lower()})
        return files

    @staticmethod
    def _read_text(src: Path, rel: str, max_bytes: int = 6000) -> str:
        try:
            raw = (src / rel).read_bytes()[:max_bytes]
            if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
                return raw.decode("utf-16", errors="replace")
            if raw.startswith(b"\xef\xbb\xbf"):
                return raw.decode("utf-8-sig", errors="replace")
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return ""

    # ── 采集产物构造（无识别结论） ──────────────────────────────────────
    def _file_index(self, files: list[dict]) -> dict:
        return {"total_files": len(files), "files": files[:2000],
                "truncated": len(files) > 2000}

    def _source_structure(self, src: Path) -> dict:
        if not src.exists():
            return {"not_applicable": True, "reason": "source/ is empty", "top_level": []}
        top = []
        for d in sorted(src.iterdir()):
            if d.name in SKIP_DIRS:
                continue
            if d.is_dir():
                sub = sum(1 for x in d.rglob("*") if x.is_file()
                          and not any(p in SKIP_DIRS for p in x.relative_to(src).parts))
                top.append({"name": d.name, "type": "dir", "file_count": sub})
            else:
                try:
                    top.append({"name": d.name, "type": "file", "size": d.stat().st_size})
                except Exception:
                    top.append({"name": d.name, "type": "file", "size": 0})
        return {"top_level": top, "total_top_level": len(top)}

    def _ext_language_counts(self, files: list[dict]) -> dict:
        """采集：原始扩展名计数 + 语言 hint（LANG_EXTENSIONS 仅作标签，未知扩展 language=None）。
        不判 primary_language（交 LLM，HC-P1-01 已删）。"""
        ext_counts: dict[str, int] = {}
        for f in files:
            ext = f.get("ext", "")
            if ext:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
        by_ext = [{"extension": ext, "file_count": cnt,
                   "language_hint": LANG_EXTENSIONS.get(ext)}
                  for ext, cnt in sorted(ext_counts.items(), key=lambda kv: -kv[1])[:20]]
        return {"extension_counts": dict(sorted(ext_counts.items(), key=lambda kv: -kv[1])[:30]),
                "languages_by_extension": by_ext,
                "note": "primary_language / 语言判定交 LLM（采集只给原始计数与 hint 标签）"}

    def _build_candidates(self, files: list[dict]) -> list[dict]:
        out = []
        for f in files:
            fname = os.path.basename(f["path"])
            if fname in BUILD_FILES:
                out.append({"path": f["path"], "build_hint": BUILD_FILES[fname]})
            elif f["path"].lower().endswith(_BUILD_SUFFIXES):
                out.append({"path": f["path"], "build_hint": "msbuild(.NET)"})
        return out

    def _dependency_manifests(self, src: Path, files: list[dict]) -> list[dict]:
        """采集：列全依赖清单候选（大小写不敏感、任意栈）+ 脱敏原文。不解析依赖（交 LLM，HC-P1-02 已删）。"""
        out = []
        for f in files:
            fname = os.path.basename(f["path"]).lower()
            if fname in _DEP_MANIFEST_NAMES or fname.endswith(_DEP_MANIFEST_SUFFIXES):
                content = self._read_text(src, f["path"], max_bytes=6000)
                out.append({"path": f["path"],
                            "content_redacted": _redact(content)[:6000] if content else ""})
            if len(out) >= 30:
                break
        return out

    def _config_candidates(self, src: Path, files: list[dict]) -> list[dict]:
        """采集：大小写不敏感 config 候选 + 脱敏键（值不出，HC-P1-04 已删）。用途解读交 LLM。"""
        out = []
        for f in files:
            fname = os.path.basename(f["path"]).lower()
            is_cfg = (fname in _CONFIG_NAMES or fname.endswith(_CONFIG_SUFFIXES)
                      or f["path"].startswith(".github/"))
            if not is_cfg:
                continue
            content = self._read_text(src, f["path"], max_bytes=8000)
            keys = self._extract_keys(f["path"], content)
            out.append({"path": f["path"], "keys_redacted": keys[:50], "keys_count": len(keys)})
            if len(out) >= 40:
                break
        return out

    @staticmethod
    def _extract_keys(rel: str, content: str) -> list[str]:
        if not content:
            return []
        keys: list[str] = []
        low = rel.lower()
        try:
            if low.endswith(".env"):
                for line in content.splitlines():
                    if line.strip() and not line.startswith("#") and "=" in line:
                        keys.append("[ENV_KEY]_" + line.split("=")[0].strip())
            elif low.endswith((".json", ".yaml", ".yml", ".properties")):
                for line in content.splitlines():
                    s = line.strip()
                    if ":" in s and not s.startswith("#"):
                        k = s.split(":")[0].strip().strip('"').strip("'")
                        if 0 < len(k) < 80:
                            keys.append(k)
                    elif "=" in s and not s.startswith("#") and low.endswith(".properties"):
                        keys.append(s.split("=")[0].strip())
            else:
                keys.append("[non-parseable-config]")
        except Exception:
            keys.append("[read_error]")
        return keys

    def _test_candidates(self, src: Path, files: list[dict]) -> dict:
        """采集：大小写不敏感测试目录/文件候选 + 脱敏原文（供 LLM 读断言，HC-P1-05 已删）。"""
        dirs: set[str] = set()
        test_files: list[dict] = []
        for f in files:
            parts = Path(f["path"]).parts
            lower_parts = [p.lower() for p in parts]
            fname = parts[-1].lower()
            hit_dir = next((p for p, lp in zip(parts, lower_parts) if lp in TEST_DIR_NAMES), None)
            is_test_file = ("test" in fname or "spec" in fname or fname == "conftest.py"
                            or ".test." in fname or ".spec." in fname)
            if hit_dir:
                dirs.add(hit_dir)
            if (hit_dir or is_test_file) and len(test_files) < 15:
                content = self._read_text(src, f["path"], max_bytes=4000)
                test_files.append({"path": f["path"],
                                   "content_redacted": _redact(content)[:4000] if content else ""})
        return {"test_directories": sorted(dirs), "test_files": test_files,
                "note": "测试识别/断言解读/框架判定交 LLM（采集只列候选与原文）"}

    def _cicd_candidates(self, files: list[dict]) -> list[dict]:
        out = []
        for f in files:
            for pat in CICD_PATTERNS:
                if pat in f["path"] or f["path"].startswith(pat.rstrip("/")):
                    out.append({"path": f["path"], "matched_pattern": pat.rstrip("/")})
                    break
        return out

    def _doc_candidates(self, files: list[dict]) -> list[dict]:
        out = []
        for f in files:
            fname = os.path.basename(f["path"]).lower()
            if fname in DOC_PATTERNS or f["path"].startswith("docs/") or "/doc/" in f["path"]:
                out.append({"path": f["path"]})
        return out

    def _infra_indicators(self, src: Path, files: list[dict]) -> list[dict]:
        """采集：报告哪些 config 文件含连接/DB 指示（脱敏）；不判 infra 类型（交 LLM，HC-P1-06 已删）。"""
        out = []
        for f in files:
            if not any(f["path"].endswith(ext) for ext in
                       (".json", ".yml", ".yaml", ".xml", ".properties", ".env", ".config")):
                continue
            content = self._read_text(src, f["path"], max_bytes=5000).lower()
            if any(kw in content for kw in _CONN_INDICATORS):
                out.append({"path": f["path"], "indicator": "connection_config_present",
                            "note": "含连接/数据源配置（值已脱敏）— infra 类型解读交 LLM"})
            if len(out) >= 50:
                break
        return out

    # ── 采集入口（图 P1 handler 用） ────────────────────────────────────
    def collect_facts(self, project_id: str) -> dict:
        """采集事实包 + 写盘 4 个纯采集产物（file_index/source_structure/cicd/doc）。
        返回喂给 ProfilingService(LLM) 的事实包（无识别结论）。"""
        src = workspace_path(project_id) / "source"
        # D-107: P1 采集产物写入 artifacts/p1/（分层文件夹，非扁平根）。
        art_dir = workspace_path(project_id) / "artifacts" / "p1"
        art_dir.mkdir(parents=True, exist_ok=True)

        files = self._scan(src)
        file_index = self._file_index(files)
        source_structure = self._source_structure(src)
        cicd = self._cicd_candidates(files)
        docs = self._doc_candidates(files)

        # 纯采集产物落盘（file listing，无 LLM 识别）
        for name, data in (("file_index", file_index), ("source_structure", source_structure),
                           ("cicd_inventory", {"cicd_items": cicd, "count": len(cicd)}),
                           ("doc_inventory", {"documents": docs, "count": len(docs)})):
            try:
                (art_dir / f"{name}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                self.warnings.append(f"Failed to write {name}.json: {e}")

        facts = {
            "source_path": str(src),
            "file_count": len(files),
            "file_index_truncated": file_index["truncated"],
            "top_level_dirs": [t["name"] for t in source_structure.get("top_level", [])
                               if t.get("type") == "dir"],
            "source_structure": source_structure,
            "ext_language_counts": self._ext_language_counts(files),
            "build_file_candidates": self._build_candidates(files),
            "dependency_manifests": self._dependency_manifests(src, files),
            "config_candidates": self._config_candidates(src, files),
            "test_candidates": self._test_candidates(src, files),
            "cicd_candidates": cicd,
            "doc_candidates": docs,
            "infra_config_indicators": self._infra_indicators(src, files),
            "collection_note": ("源码为空/未物化，事实包仅含基础采集字段（诚实）" if not files else
                                "确定性采集事实（大小写不敏感/依赖配置测试全候选/已脱敏）；识别交 LLM"),
        }
        if self.trace:
            try:
                self.trace.write("collection_complete", action="full_stack_collect",
                                 summary=f"P1 采集：{len(files)} 文件，{len(facts['dependency_manifests'])} 依赖清单候选",
                                 project_id=project_id)
            except Exception:
                logger.debug("collect_facts trace 写入失败（advisory）", exc_info=True)
        return facts

    # ── 遗留采集通道（/profile 路由 + run_profiling 工具） ────────────────
    def profile(self, project_id: str) -> dict:
        """遗留采集通道：写采集产物 + p2_input_manifest + summary（不产 LLM 识别）。
        图 P1 建档识别走 RealP1Handler → ProfilingService(LLM)；本方法只做采集。"""
        src = workspace_path(project_id) / "source"
        # D-107: P1 采集产物写入 artifacts/p1/（分层文件夹，非扁平根）。
        art_dir = workspace_path(project_id) / "artifacts" / "p1"
        facts = self.collect_facts(project_id)  # 写 file_index/source_structure/cicd/doc
        files = facts["file_count"]

        # 采集版（无识别结论）的其余产物落盘。
        collection_artifacts = {
            "module_structure": {"build_file_candidates": facts["build_file_candidates"],
                                 "note": "模块业务角色识别交 LLM（采集只列构建文件位置）"},
            "dependency_draft": {"dependency_manifests": facts["dependency_manifests"],
                                 "note": "依赖解析交 LLM（采集只列清单原文候选）",
                                 "identification_deferred": True},
            "test_inventory": {**facts["test_candidates"], "identification_deferred": True},
            "config_inventory": {"configs": facts["config_candidates"],
                                 "count": len(facts["config_candidates"]),
                                 "redaction_note": "ALL config values REDACTED. Only keys listed.",
                                 "identification_deferred": True},
            "infra_clues": {"infra_config_indicators": facts["infra_config_indicators"],
                            "count": len(facts["infra_config_indicators"]),
                            "identification_deferred": True},
            "uncertainty_manifest": {"evidence_gaps": self.gaps, "total_gaps": len(self.gaps),
                                     "note": "识别盲区由 P1 LLM 建档主动发声（采集层只登记采集警告）"},
        }
        items_completed = 4  # 已由 collect_facts 写盘的 4 个采集产物
        for name, data in collection_artifacts.items():
            try:
                (art_dir / f"{name}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                items_completed += 1
            except Exception as e:
                self.warnings.append(f"Failed to write {name}.json: {e}")

        result = {
            "project_id": project_id, "profiled_at": _now(), "source_path": str(src),
            "items_completed": items_completed, "items_not_applicable": 0,
            "gaps_count": len(self.gaps), "warnings": self.warnings,
            "collection_only": True,
            "note": "profile() 为采集通道；LLM 识别在图 P1 RealP1Handler（AGENTS §2.3）",
        }
        # summary + p2 manifest（采集口径）
        (art_dir / "profiling_summary.md").write_text(
            self._build_summary(project_id, result, facts), encoding="utf-8")
        p2_manifest = {
            "project_id": project_id, "p1_completed_at": _now(),
            "p1_stage": "collection_only",
            "input_artifacts": [{"ref": f"artifacts/p1/{p.name}", "type": p.stem}
                                for p in sorted(art_dir.glob("*.json"))],
            "pending_evidence_gaps": self.gaps,
            "environment_profile_ref": ".rebuild/environment.json",
        }
        (art_dir / "p2_input_manifest.json").write_text(
            json.dumps(p2_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        if self.trace:
            try:
                self.trace.write("profiling_complete", action="full_stack_profile",
                    summary=f"P1 采集通道：{items_completed} 采集产物，{len(self.gaps)} gaps",
                    project_id=project_id)
            except Exception:
                logger.debug("profile trace 写入失败（advisory）", exc_info=True)
        return result

    def _build_summary(self, pid: str, result: dict, facts: dict) -> str:
        exts = facts.get("ext_language_counts", {}).get("languages_by_extension", [])
        ext_dist = ", ".join(f"{e.get('language_hint') or e['extension']}({e['file_count']})"
                             for e in exts[:5])
        deps = facts.get("dependency_manifests", [])
        return "\n".join([
            f"# P1 采集摘要：{pid}",
            f"采集时间：{result['profiled_at']}",
            "",
            "## 采集事实（识别交 LLM P1 建档）",
            f"- 文件总数：{facts.get('file_count', 0)}",
            f"- 扩展名分布（hint）：{ext_dist or '无'}",
            f"- 构建文件候选：{len(facts.get('build_file_candidates', []))}",
            f"- 依赖清单候选：{len(deps)}",
            f"- 配置候选：{len(facts.get('config_candidates', []))}",
            f"- 测试目录候选：{', '.join(facts.get('test_candidates', {}).get('test_directories', [])) or '无'}",
            "",
            "> 主语言/框架/依赖/入口/infra 的【识别】由 P1 LLM 建档 Agent 对采集事实推理产出（AGENTS §2.3）。",
        ])
