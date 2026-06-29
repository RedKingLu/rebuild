"""Full-Stack Profiler — P1 全量项目识别基线 (R9-3C).

14 identification items performed on workspace/source/, generating
structured Artifacts for P2-P6 reuse. All config/env values redacted.
Uncertain items become Evidence Gaps, not guesses.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.workspace_service import workspace_path, _guard

# Directories to skip during scan
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "__pycache__", ".venv", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".nuxt", ".rebuild",
}

# R9-5-8 T2: authoritative P1 identification item list — SINGLE SOURCE OF TRUTH.
# These are the 12 artifact keys actually produced by profile() (see the
# `artifacts` dict in profile()); the frontend StagePageP1 MUST read this list
# from the backend instead of hardcoding its own (formerly a drifted 14-item
# array). Each entry: (artifact_key, human label). uncertainty_manifest backs
# the Evidence Gaps card (T3).
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

# Key files for identification
BUILD_FILES = {
    "pom.xml": "maven", "build.gradle": "gradle", "build.gradle.kts": "gradle-kts",
    "settings.gradle": "gradle", "Makefile": "make", "CMakeLists.txt": "cmake",
    "package.json": "npm", "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm",
    "requirements.txt": "pip", "setup.py": "setuptools", "pyproject.toml": "python-build",
    "Cargo.toml": "cargo", "go.mod": "go-mod", "go.sum": "go",
    "Gemfile": "bundler", "composer.json": "composer",
    "*.csproj": "msbuild", "*.sln": "msbuild-sln", "*.fsproj": "msbuild",
    "Android.bp": "soong", "BUILD": "bazel", "WORKSPACE": "bazel",
}

CONFIG_FILES = {
    ".env": "dotenv", ".env.example": "dotenv-example",
    "appsettings.json": "dotnet-appsettings", "web.config": "iis-webconfig",
    "application.yml": "spring-yml", "application.yaml": "spring-yaml",
    "application.properties": "spring-props",
    "tsconfig.json": "tsconfig", "jsconfig.json": "jsconfig",
    "Dockerfile": "docker", "docker-compose.yml": "docker-compose",
    "docker-compose.yaml": "docker-compose", ".dockerignore": "docker-ignore",
    ".gitignore": "gitignore", ".gitlab-ci.yml": "gitlab-ci",
    ".github/": "github-actions", "Jenkinsfile": "jenkins",
    ".travis.yml": "travis-ci", "Makefile": "make",
}

TEST_DIR_NAMES = {"test", "tests", "spec", "specs", "__tests__", "e2e", "integration"}

LANG_EXTENSIONS = {
    ".java": "Java", ".kt": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".jsx": "React JSX",
    ".tsx": "React TSX", ".go": "Go", ".rs": "Rust",
    ".cs": "C#", ".vb": "Visual Basic", ".fs": "F#",
    ".c": "C", ".cpp": "C++", ".h": "C/C++ Header", ".hpp": "C++ Header",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".m": "Objective-C",
    ".r": "R", ".sql": "SQL", ".sh": "Shell", ".bash": "Bash",
    ".yaml": "YAML", ".yml": "YAML", ".json": "JSON", ".xml": "XML",
    ".md": "Markdown", ".rst": "reStructuredText",
    ".toml": "TOML", ".ini": "INI", ".cfg": "Config",
    ".tf": "Terraform", ".proto": "Protobuf", ".graphql": "GraphQL",
}

# Sensitive config keys whose VALUES must never appear in output
SENSITIVE_KEYS = {
    "password", "passwd", "secret", "key", "token", "apikey", "api_key",
    "connectionstring", "connection_string", "connstr", "credential",
    "private_key", "privatekey", "access_key", "accesskey", "auth",
    "authorization", "jwt_secret", "jwtsecret", "encryption_key",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(text: str) -> str:
    """Return '[REDACTED]' for any text. Never log actual values."""
    return "[REDACTED]"


class FullStackProfiler:
    """Performs P1 full-stack identification on a project workspace."""

    def __init__(self, trace_writer=None, audit_writer=None):
        self.trace = trace_writer
        self.audit = audit_writer
        self.gaps: list[dict] = []
        self.warnings: list[str] = []

    def profile(self, project_id: str) -> dict:
        """Main entry point. Returns profiling_result dict with all 14 items."""
        src = workspace_path(project_id) / "source"
        art_dir = workspace_path(project_id) / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)

        result = {
            "project_id": project_id,
            "profiled_at": _now(),
            "source_path": str(src),
            "items_completed": 0,
            "items_not_applicable": 0,
            "gaps_count": 0,
        }

        # Phase 1: Structure (items 1-3)
        file_index = self._item1_file_index(project_id, src)
        src_structure = self._item2_source_structure(src)
        modules = self._item3_module_structure(src, file_index)

        # Phase 2: Technology (items 4-6)
        languages = self._item4_language_id(file_index)
        frameworks = self._item5_framework_id(src, file_index)
        build_systems = self._item6_build_system_id(src, file_index)

        # Phase 3: Dependencies & Entry (items 7-8)
        dependencies = self._item7_dependency_id(src, file_index)
        entry_points = self._item8_entry_point_id(src, file_index, languages)

        # Phase 4: Quality Infrastructure (items 9-10)
        test_inventory = self._item9_test_inventory(src, file_index)
        cicd_inventory = self._item10_cicd_inventory(src, file_index)

        # Phase 5: Infrastructure & Config (items 11-12)
        infra_clues = self._item11_infra_clues(src, file_index)
        config_inventory = self._item12_config_inventory(src, file_index)

        # Phase 6: Docs & Uncertainty (items 13-14)
        doc_inventory = self._item13_doc_inventory(src, file_index)
        uncertainty = self._item14_uncertainty_manifest()

        # Write all artifacts
        artifacts = {
            "file_index": file_index,
            "source_structure": src_structure,
            "module_structure": modules,
            "tech_stack": {"languages": languages, "frameworks": frameworks, "build_systems": build_systems},
            "dependency_draft": dependencies,
            "entry_points": entry_points,
            "test_inventory": test_inventory,
            "cicd_inventory": cicd_inventory,
            "infra_clues": infra_clues,
            "config_inventory": config_inventory,
            "doc_inventory": doc_inventory,
            "uncertainty_manifest": uncertainty,
        }

        for name, data in artifacts.items():
            try:
                (art_dir / f"{name}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                result["items_completed"] += 1
            except Exception as e:
                self.warnings.append(f"Failed to write {name}.json: {e}")

        # Count not applicable
        for name, data in artifacts.items():
            if isinstance(data, dict) and data.get("not_applicable"):
                result["items_not_applicable"] += 1

        result["gaps_count"] = len(self.gaps)
        result["warnings"] = self.warnings

        # Write profiling summary
        summary = self._build_summary(project_id, result, artifacts)
        (art_dir / "profiling_summary.md").write_text(summary, encoding="utf-8")

        # Write P2 input manifest
        p2_manifest = self._build_p2_manifest(project_id, artifacts)
        (art_dir / "p2_input_manifest.json").write_text(
            json.dumps(p2_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        # Trace
        if self.trace:
            self.trace.write("profiling_complete", action="full_stack_profile",
                summary=f"P1 profiling: {result['items_completed']} items, {result['gaps_count']} gaps",
                project_id=project_id)

        return result

    # ── Item implementations ────────────────────────────────────────────

    def _item1_file_index(self, pid: str, src: Path) -> dict:
        """Full file/directory index."""
        if not src.exists():
            return {"not_applicable": True, "reason": "source/ is empty", "files": [], "total_files": 0}
        files = []
        for f in sorted(src.rglob("*")):
            parts = f.relative_to(src).parts
            if any(p in SKIP_DIRS for p in parts):
                continue
            if f.is_file():
                try:
                    st = f.stat()
                    files.append({"path": str(f.relative_to(src)), "size": st.st_size, "ext": f.suffix.lower()})
                except Exception:
                    files.append({"path": str(f.relative_to(src)), "size": 0, "ext": f.suffix.lower()})
        return {"total_files": len(files), "files": files[:2000], "truncated": len(files) > 2000}

    def _item2_source_structure(self, src: Path) -> dict:
        """Top-level source directory structure."""
        if not src.exists():
            return {"not_applicable": True, "reason": "source/ is empty"}
        top = []
        for d in sorted(src.iterdir()):
            if d.name in SKIP_DIRS:
                continue
            if d.is_dir():
                sub_count = sum(1 for _ in d.rglob("*") if _.is_file() and not any(p in SKIP_DIRS for p in _.relative_to(src).parts))
                top.append({"name": d.name, "type": "dir", "file_count": sub_count})
            else:
                top.append({"name": d.name, "type": "file", "size": d.stat().st_size})
        return {"top_level": top, "total_top_level": len(top)}

    def _item3_module_structure(self, src: Path, fi: dict) -> dict:
        """Module/package structure identification."""
        if fi.get("not_applicable"):
            return {"not_applicable": True, "reason": "No source files"}
        modules = []
        # Detect multi-module: look for nested build files
        build_locations = [f["path"] for f in fi.get("files", []) if os.path.basename(f["path"]) in BUILD_FILES or f["path"].endswith(tuple(f".{e}" for e in ["csproj", "sln"]))]
        for bl in build_locations:
            mod_dir = str(Path(bl).parent) if Path(bl).parent != Path(".") else "root"
            modules.append({"path": mod_dir, "build_file": bl})
        is_monorepo = len(set(m["path"] for m in modules)) > 1
        return {"modules": modules, "is_monorepo": is_monorepo, "module_count": len(modules)}

    def _item4_language_id(self, fi: dict) -> dict:
        """Primary language identification by extension stats."""
        if fi.get("not_applicable"):
            return {"not_applicable": True, "reason": "No source files"}
        ext_counts: dict[str, int] = {}
        for f in fi.get("files", []):
            ext = f.get("ext", "")
            if ext and ext not in {".json", ".yaml", ".yml", ".xml", ".md", ".txt", ".lock"}:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
        sorted_exts = sorted(ext_counts.items(), key=lambda x: -x[1])
        languages = []
        for ext, count in sorted_exts[:10]:
            lang = LANG_EXTENSIONS.get(ext, f"Unknown({ext})")
            languages.append({"language": lang, "extension": ext, "file_count": count,
                            "confidence": "high" if count > 5 else "medium" if count > 1 else "low"})
        primary = languages[0]["language"] if languages else "unknown"
        return {"primary_language": primary, "languages": languages, "total_lang_files": sum(ext_counts.values())}

    def _item5_framework_id(self, src: Path, fi: dict) -> dict:
        """Framework identification from project files."""
        frameworks = []
        files_found = [f["path"] for f in fi.get("files", [])]
        file_set = set(files_found)

        # Java frameworks
        if any("pom.xml" in x for x in files_found):
            # Check for Spring Boot starters
            try:
                pom = (src / "pom.xml").read_text(encoding="utf-8", errors="replace")[:10000]
                if "spring-boot" in pom:
                    frameworks.append({"framework": "Spring Boot", "confidence": "high"})
                elif "spring" in pom.lower():
                    frameworks.append({"framework": "Spring", "confidence": "medium"})
            except Exception:
                pass
        # .NET frameworks
        if any(x.endswith(".csproj") for x in files_found):
            frameworks.append({"framework": ".NET", "confidence": "high"})
        # Node.js
        if "package.json" in file_set:
            try:
                pkg = json.loads((src / "package.json").read_text(encoding="utf-8", errors="replace"))
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "next" in deps: frameworks.append({"framework": "Next.js", "confidence": "high"})
                elif "react" in deps: frameworks.append({"framework": "React", "confidence": "high"})
                elif "vue" in deps: frameworks.append({"framework": "Vue", "confidence": "high"})
                elif "express" in deps: frameworks.append({"framework": "Express", "confidence": "medium"})
            except Exception:
                pass
        # Python
        if "requirements.txt" in file_set or "pyproject.toml" in file_set:
            try:
                req_file = src / "requirements.txt" if "requirements.txt" in file_set else src / "pyproject.toml"
                content = req_file.read_text(encoding="utf-8", errors="replace")[:5000]
                if "django" in content.lower(): frameworks.append({"framework": "Django", "confidence": "high"})
                elif "flask" in content.lower(): frameworks.append({"framework": "Flask", "confidence": "high"})
                elif "fastapi" in content.lower(): frameworks.append({"framework": "FastAPI", "confidence": "high"})
            except Exception:
                pass
        if not frameworks:
            self.gaps.append({"item": 5, "type": "framework_unknown",
                            "detail": "No framework confidently identified from project files"})
        return {"frameworks": frameworks, "count": len(frameworks)}

    def _item6_build_system_id(self, src: Path, fi: dict) -> dict:
        """Build system identification."""
        systems = []
        for f in fi.get("files", []):
            fname = os.path.basename(f["path"])
            if fname in BUILD_FILES:
                systems.append({"build_system": BUILD_FILES[fname], "file": f["path"], "confidence": "high"})
            elif f["path"].endswith(".csproj"):
                systems.append({"build_system": "msbuild", "file": f["path"], "confidence": "high"})
        if not systems:
            self.gaps.append({"item": 6, "type": "build_system_unknown",
                            "detail": "No build file detected"})
        return {"build_systems": systems, "count": len(systems)}

    def _item7_dependency_id(self, src: Path, fi: dict) -> dict:
        """Dependency inventory draft."""
        deps = []
        # Parse package.json
        pkg_json = src / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                for name, ver in {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}.items():
                    deps.append({"name": name, "version": ver, "source": "package.json", "type": "npm"})
            except Exception:
                pass
        # Parse requirements.txt
        req_txt = src / "requirements.txt"
        if req_txt.exists():
            try:
                for line in req_txt.read_text(encoding="utf-8", errors="replace").splitlines()[:500]:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        deps.append({"name": line.split("==")[0].split(">=")[0].strip(),
                                     "version": line, "source": "requirements.txt", "type": "pip"})
            except Exception:
                pass
        return {"dependencies": deps[:500], "total": len(deps), "truncated": len(deps) > 500}

    def _item8_entry_point_id(self, src: Path, fi: dict, langs: dict) -> dict:
        """Entry point / startup method candidates."""
        entries = []
        # Common entry patterns
        patterns = [
            "Program.cs", "Main.cs", "main.go", "main.rs", "index.js", "index.ts",
            "app.py", "main.py", "manage.py", "server.js", "server.ts",
            "App.tsx", "App.jsx", "Application.java",
        ]
        file_set = {f["path"] for f in fi.get("files", [])}
        for pat in patterns:
            for fp in file_set:
                if fp.endswith(pat) or fp == pat:
                    entries.append({"path": fp, "pattern": pat, "confidence": "medium"})
        # Dockerfile ENTRYPOINT/CMD
        dockerfile = src / "Dockerfile"
        if dockerfile.exists():
            try:
                content = dockerfile.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    if line.strip().upper().startswith(("ENTRYPOINT", "CMD")):
                        entries.append({"path": "Dockerfile", "pattern": line.strip(), "confidence": "high"})
            except Exception:
                pass
        return {"entry_points": entries[:20], "count": len(entries)}

    def _item9_test_inventory(self, src: Path, fi: dict) -> dict:
        """Test directories and test framework clues."""
        test_dirs = []
        test_frameworks = set()
        for f in fi.get("files", []):
            parts = Path(f["path"]).parts
            if any(d in TEST_DIR_NAMES for d in parts):
                test_dir = next(d for d in parts if d in TEST_DIR_NAMES)
                if test_dir not in [t["dir"] for t in test_dirs]:
                    test_dirs.append({"dir": test_dir, "path": f["path"]})
            # Detect test frameworks by file patterns
            if "pytest" in f["path"] or "conftest.py" in f["path"]:
                test_frameworks.add("pytest")
            if any(x in f["path"] for x in ["jest.config", ".test.", ".spec."]):
                test_frameworks.add("jest")
            if any(x in f["path"] for x in ["JUnit", "Test.java", "Tests.java"]):
                test_frameworks.add("JUnit")
            if any(x in f["path"] for x in ["NUnit", "xUnit", "Test.cs", "Tests.cs"]):
                test_frameworks.add("NUnit/xUnit")
        return {"test_directories": test_dirs, "test_frameworks": list(test_frameworks),
                "has_tests": len(test_dirs) > 0 or len(test_frameworks) > 0}

    def _item10_cicd_inventory(self, src: Path, fi: dict) -> dict:
        """CI/CD, container, deployment file inventory."""
        items = []
        cicd_patterns = [
            "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
            ".gitlab-ci.yml", ".github/workflows/", "Jenkinsfile",
            ".travis.yml", "deploy/", "k8s/", "helm/", "terraform/",
            ".drone.yml", "bitbucket-pipelines.yml",
        ]
        for f in fi.get("files", []):
            for pat in cicd_patterns:
                if pat in f["path"] or f["path"].startswith(pat.rstrip("/")):
                    items.append({"path": f["path"], "type": pat.rstrip("/"), "confidence": "high"})
                    break
        return {"cicd_items": items, "count": len(items), "has_docker": any("Dockerfile" in i["path"] for i in items)}

    def _item11_infra_clues(self, src: Path, fi: dict) -> dict:
        """Database, middleware, external service configuration clues. REDACTED."""
        clues = []
        # Common DB/middleware indicators in file names
        infra_patterns = {
            "mysql": "MySQL", "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
            "oracle": "Oracle", "mssql": "SQL Server", "sqlserver": "SQL Server",
            "mongodb": "MongoDB", "redis": "Redis", "kafka": "Kafka",
            "rabbitmq": "RabbitMQ", "elasticsearch": "Elasticsearch",
            "nginx": "Nginx", "apache": "Apache", "tomcat": "Tomcat",
            "weblogic": "WebLogic", "websphere": "WebSphere",
            "dameng": "达梦DM", "opengauss": "openGauss", "gaussdb": "GaussDB",
        }
        for f in fi.get("files", []):
            fname = f["path"].lower()
            for key, label in infra_patterns.items():
                if key in fname:
                    clues.append({"file": f["path"], "infra_type": label, "confidence": "low",
                                "note": "File name match only — needs config content verification"})
        # Also scan for connection strings in config files (REDACTED output)
        for f in fi.get("files", []):
            if any(f["path"].endswith(ext) for ext in [".json", ".yml", ".yaml", ".xml", ".properties", ".env"]):
                try:
                    content = (src / f["path"]).read_text(encoding="utf-8", errors="replace")[:5000]
                    # Check for DB connection indicators without outputting values
                    if any(kw in content.lower() for kw in ["jdbc:", "connectionstring", "connection_string",
                                                              "datasource", "database_url", "db_url",
                                                              "redis_url", "kafka_broker"]):
                        clues.append({"file": f["path"], "infra_type": "connection_config_found",
                                    "confidence": "medium", "note": "Connection config detected — values REDACTED"})
                except Exception:
                    pass
        # Deduplicate
        seen = set()
        unique = []
        for c in clues:
            key = (c["file"], c["infra_type"])
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return {"infra_clues": unique[:50], "count": len(unique)}

    def _item12_config_inventory(self, src: Path, fi: dict) -> dict:
        """Config file inventory — keys only, values REDACTED."""
        configs = []
        for f in fi.get("files", []):
            fname = os.path.basename(f["path"])
            matched = None
            for pat, ctype in CONFIG_FILES.items():
                if fname == pat or (pat.endswith("/") and f["path"].startswith(pat)):
                    matched = ctype
                    break
            if matched:
                # Extract keys only, never values
                keys_found = []
                try:
                    content = (src / f["path"]).read_text(encoding="utf-8", errors="replace")[:10000]
                    if f["path"].endswith((".json", ".yaml", ".yml")):
                        # Simple key extraction (not a full parser)
                        for line in content.splitlines():
                            for sk in SENSITIVE_KEYS:
                                if sk in line.lower():
                                    keys_found.append(f"[REDACTED]_{sk}")
                                    break
                            else:
                                # Extract non-sensitive top-level keys
                                stripped = line.strip()
                                if ":" in stripped and not stripped.startswith("#"):
                                    key = stripped.split(":")[0].strip().strip('"').strip("'")
                                    if len(key) < 80:
                                        keys_found.append(key)
                    elif f["path"].endswith(".env"):
                        keys_found = [line.split("=")[0].strip() for line in content.splitlines()
                                     if line.strip() and not line.startswith("#") and "=" in line]
                        # ALL .env keys are potentially sensitive → mark as REDACTED
                        keys_found = [f"[ENV_KEY]_{k}" for k in keys_found]
                    else:
                        keys_found = ["[binary/non-parseable]"]
                except Exception:
                    keys_found = ["[read_error]"]
                configs.append({"path": f["path"], "config_type": matched,
                              "keys_found": keys_found[:50], "keys_count": len(keys_found)})
        return {"configs": configs, "count": len(configs),
                "redaction_note": "ALL config values REDACTED. Only keys are listed."}

    def _item13_doc_inventory(self, src: Path, fi: dict) -> dict:
        """Documentation and README identification."""
        docs = []
        doc_patterns = {"README.md", "README", "CHANGELOG.md", "CHANGELOG", "CONTRIBUTING.md",
                       "LICENSE", "LICENSE.md", "NOTICE", "AUTHORS", "CODE_OF_CONDUCT.md"}
        for f in fi.get("files", []):
            fname = os.path.basename(f["path"])
            if fname in doc_patterns or f["path"].startswith("docs/") or "/doc/" in f["path"]:
                docs.append({"path": f["path"], "type": "documentation"})
        return {"documents": docs, "count": len(docs), "has_readme": any("README" in d["path"] for d in docs)}

    def _item14_uncertainty_manifest(self) -> dict:
        """Uncertainties, conflicts, missing items → Evidence Gaps."""
        return {"evidence_gaps": self.gaps, "total_gaps": len(self.gaps),
                "note": "All uncertain/conflicting/missing items explicitly registered. No guesswork."}

    def _build_summary(self, pid: str, result: dict, artifacts: dict) -> str:
        """Generate Markdown profiling summary."""
        tech = artifacts.get("tech_stack", {})
        langs = tech.get("languages", {}).get("languages", [])
        primary = tech.get("languages", {}).get("primary_language", "unknown")
        fws = tech.get("frameworks", {}).get("frameworks", [])
        bss = tech.get("build_systems", {}).get("build_systems", [])
        deps = artifacts.get("dependency_draft", {})
        ci = artifacts.get("cicd_inventory", {})
        ti = artifacts.get("test_inventory", {})
        fi = artifacts.get("file_index", {})

        lang_dist = ", ".join("{0}({1})".format(l["language"], l["file_count"]) for l in langs[:5])
        framework_str = ", ".join(f["framework"] for f in fws) if fws else "未识别"
        build_str = ", ".join(b["build_system"] for b in bss) if bss else "未识别"

        lines = [
            f"# P1 建档摘要：{pid}",
            f"建档时间：{result['profiled_at']}",
            "",
            "## 技术栈",
            f"- 主要语言：{primary}",
            f"- 语言分布：{lang_dist}",
            f"- 框架：{framework_str}",
            f"- 构建系统：{build_str}",
            "",
            "## 规模",
            f"- 文件总数：{fi.get('total_files', 0)}",
            f"- 依赖项：{deps.get('total', 0)}",
            f"- 入口点候选：{artifacts.get('entry_points', {}).get('count', 0)}",
            "",
            "## 质量基础设施",
            f"- 测试：{'有' if ti.get('has_tests') else '无'} (框架：{', '.join(ti.get('test_frameworks', []))})",
            f"- CI/CD：{ci.get('count', 0)} 项",
            f"- Docker：{'有' if ci.get('has_docker') else '无'}",
            "",
            "## 不确定性",
            f"- Evidence Gap 总数：{result['gaps_count']}",
            f"- 警告：{len(result.get('warnings', []))}",
            "",
            "> 深度兼容性评估归 P2。本档案为 P1 全量识别基线。",
        ]
        return "\n".join(lines)

    def _build_p2_manifest(self, pid: str, artifacts: dict) -> dict:
        """Build P2 input package manifest."""
        return {
            "project_id": pid,
            "p1_completed_at": _now(),
            "p1_stage": "completed",
            "input_artifacts": [
                {"ref": f"artifacts/{name}.json", "type": name}
                for name in artifacts.keys()
            ],
            "pending_evidence_gaps": self.gaps,
            "environment_profile_ref": ".rebuild/environment.json",
        }
