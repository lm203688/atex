#!/usr/bin/env python3
"""
ATEX GitHub 发布工具 v1.0
功能：更新核心文件 → 安全审核 → 发布到GitHub
安全措施：代码混淆、敏感信息清除、核心逻辑保护、AGPL-3.0许可证
"""

import os
import sys
import json
import re
import shutil
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path

# === 配置 ===
BASE_DIR = Path(__file__).parent.parent
RELEASE_DIR = BASE_DIR / "releases"
PUBLISH_DIR = RELEASE_DIR / "publish_staging"
REPO_DIR = BASE_DIR

# 敏感信息模式（发布前必须清除）
SENSITIVE_PATTERNS = [
    r'lx688@sina\.com',
    r'PayPal\.me/xinglixingli',
    r'463102527@qq\.com',
    r'/home/z/my-project',
    r'GITHUB_TOKEN',
    r'API_KEY|api_key|apikey',
    r'secret|SECRET',
    r'password|PASSWORD',
]

# 不应出现在发布包中的内部文件/目录
EXCLUDE_PATTERNS = [
    'accounts/', 'orders/', 'logs/', 'data/',
    'settlements/', 'promo/', 'releases/',
    'scripts/', '.git/',
    'promotion_log.json', 'promotion_plan.json',
    'strategy.json', 'promote.py',
    'ATEX_v4.2_完整存档',
    '__pycache__', '*.pyc', '.DS_Store',
    'HEARTBEAT.md', 'AGENTS.md', 'SOUL.md',
    'USER.md', 'IDENTITY.md', 'TOOLS.md',
    'BOOTSTRAP.md',
]

# 需要混淆处理的Python文件
OBFUSCATE_FILES = ['atex.py', 'api/server.py']


class SecurityReviewer:
    """发布前安全审核器"""

    def __init__(self, staging_dir):
        self.staging_dir = staging_dir
        self.issues = []
        self.warnings = []
        self.passed = True

    def check_all(self):
        """执行全部安全检查"""
        checks = [
            ("敏感信息扫描", self._check_sensitive_info),
            ("内部路径检查", self._check_internal_paths),
            ("调试代码检查", self._check_debug_code),
            ("运营数据检查", self._check_operational_data),
            ("许可证文件检查", self._check_license),
            ("README完整性检查", self._check_readme),
            ("版本号一致性检查", self._check_version_consistency),
            ("代码混淆检查", self._check_obfuscation),
            ("服务数据脱敏检查", self._check_service_data),
            (".gitignore配置检查", self._check_gitignore),
        ]

        results = {}
        for name, check_fn in checks:
            try:
                status, detail = check_fn()
                results[name] = {"status": status, "detail": detail}
                if status == "FAIL":
                    self.passed = False
                    self.issues.append("FAIL {}: {}".format(name, detail))
                elif status == "WARN":
                    self.warnings.append("WARN {}: {}".format(name, detail))
            except Exception as e:
                results[name] = {"status": "ERROR", "detail": str(e)}
                self.passed = False
                self.issues.append("FAIL {}: {}".format(name, e))

        return {
            "passed": self.passed,
            "issues": self.issues,
            "warnings": self.warnings,
            "checks": results,
            "timestamp": datetime.now().isoformat()
        }

    def _scan_files(self, extensions=None):
        """扫描staging目录中的所有文件"""
        if extensions is None:
            extensions = ['.py', '.json', '.md', '.js', '.yaml', '.yml', '.txt']
        files = []
        for ext in extensions:
            files.extend(self.staging_dir.rglob('*' + ext))
        return files

    def _file_contains(self, filepath, pattern):
        """检查文件是否包含指定模式"""
        try:
            content = filepath.read_text(encoding='utf-8', errors='ignore')
            return bool(re.search(pattern, content, re.IGNORECASE))
        except Exception:
            return False

    def _check_sensitive_info(self):
        """检查敏感信息（支付信息、密钥等）"""
        found = []
        for f in self._scan_files():
            for pattern in SENSITIVE_PATTERNS:
                if self._file_contains(f, pattern):
                    found.append("{}: 匹配 '{}'".format(f.relative_to(self.staging_dir), pattern))
        if found:
            return "FAIL", "发现 {} 处敏感信息: ".format(len(found)) + "; ".join(found[:5])
        return "PASS", "未发现敏感信息"

    def _check_internal_paths(self):
        """检查内部路径引用"""
        found = []
        for f in self._scan_files():
            if self._file_contains(f, r'/home/|/Users/|C:\\Users'):
                found.append(str(f.relative_to(self.staging_dir)))
        if found:
            return "FAIL", "发现内部路径: " + ", ".join(found[:5])
        return "PASS", "无内部路径引用"

    def _check_debug_code(self):
        """检查调试代码残留"""
        debug_patterns = [
            r'print\s*\(\s*[\'"]\s*(debug|DEBUG|test|TEST)',
            r'pdb\.set_trace|breakpoint\(\)',
            r'#\s*TODO|#\s*FIXME|#\s*HACK|#\s*XXX',
            r'import\s+pdb',
        ]
        found = []
        for f in self._scan_files(['.py']):
            for pattern in debug_patterns:
                if self._file_contains(f, pattern):
                    found.append("{}: 匹配调试模式".format(f.relative_to(self.staging_dir)))
                    break
        if found:
            return "WARN", "发现 {} 个文件可能有调试代码: ".format(len(found)) + ", ".join(found[:5])
        return "PASS", "无调试代码残留"

    def _check_operational_data(self):
        """检查运营数据是否混入"""
        forbidden_files = [
            'accounts.json', 'orderbook.json', 'promotion_log.json',
            'daily_service_tracking.json', 'daily_platform_ops.json',
            'strategy.json', 'promotion_plan.json',
        ]
        found = []
        for f in self.staging_dir.rglob('*'):
            if f.name in forbidden_files:
                found.append(str(f.relative_to(self.staging_dir)))
        if found:
            return "FAIL", "发现运营数据文件: " + ", ".join(found)
        return "PASS", "无运营数据混入"

    def _check_license(self):
        """检查许可证文件"""
        license_file = self.staging_dir / "LICENSE"
        if not license_file.exists():
            return "FAIL", "缺少LICENSE文件"
        content = license_file.read_text(encoding='utf-8', errors='ignore')
        if 'AGPL' not in content and 'GNU' not in content:
            return "WARN", "许可证非AGPL，建议使用AGPL-3.0防止商业复制"
        return "PASS", "LICENSE文件存在"

    def _check_readme(self):
        """检查README完整性"""
        readme = self.staging_dir / "README.md"
        if not readme.exists():
            return "FAIL", "缺少README.md"
        content = readme.read_text(encoding='utf-8', errors='ignore')
        required_sections = ['ATEX', 'Install', 'Usage', 'License']
        missing = [s for s in required_sections if s.lower() not in content.lower()]
        if missing:
            return "WARN", "README缺少章节: " + ", ".join(missing)
        return "PASS", "README完整"

    def _check_version_consistency(self):
        """检查版本号一致性"""
        config = self.staging_dir / "config.json"
        package = self.staging_dir / "package.json"
        versions = {}
        if config.exists():
            try:
                data = json.loads(config.read_text())
                versions['config.json'] = data.get('version', 'N/A')
            except Exception:
                pass
        if package.exists():
            try:
                data = json.loads(package.read_text())
                versions['package.json'] = data.get('version', 'N/A')
            except Exception:
                pass
        if len(set(versions.values())) > 1:
            return "WARN", "版本号不一致: {}".format(versions)
        return "PASS", "版本号一致: {}".format(versions)

    def _check_obfuscation(self):
        """检查核心代码是否已混淆"""
        issues = []
        for rel_path in OBFUSCATE_FILES:
            f = self.staging_dir / rel_path
            if not f.exists():
                continue
            content = f.read_text(encoding='utf-8', errors='ignore')
            comment_count = len(re.findall(r'#\s*[A-Za-z]', content))
            long_names = re.findall(r'\b[a-z_]{20,}\b', content)
            if comment_count > 10:
                issues.append("{}: {}行注释未清除".format(rel_path, comment_count))
            if long_names:
                issues.append("{}: 发现长变量名（可能未混淆）".format(rel_path))
        if issues:
            return "WARN", "; ".join(issues[:3])
        return "PASS", "核心代码已混淆处理"

    def _check_service_data(self):
        """检查服务数据是否为示例数据"""
        services = self.staging_dir / "services" / "services.json"
        if not services.exists():
            return "WARN", "services.json不存在"
        try:
            data = json.loads(services.read_text())
            if isinstance(data, list):
                for svc in data:
                    if svc.get('sales', 0) > 0 or svc.get('revenue', 0) > 0:
                        return "FAIL", "services.json包含真实运营数据，需替换为示例数据"
            return "PASS", "服务数据为示例数据"
        except Exception:
            return "WARN", "services.json格式异常"

    def _check_gitignore(self):
        """检查.gitignore配置"""
        gitignore = self.staging_dir / ".gitignore"
        if not gitignore.exists():
            return "FAIL", "缺少.gitignore"
        content = gitignore.read_text(encoding='utf-8', errors='ignore')
        required_entries = ['accounts/', 'logs/', 'data/', '__pycache__', '.env']
        missing = [e for e in required_entries if e not in content]
        if missing:
            return "WARN", ".gitignore缺少: " + ", ".join(missing)
        return "PASS", ".gitignore配置完整"


class CodeObfuscator:
    """Python代码混淆器"""

    def __init__(self, staging_dir):
        self.staging_dir = staging_dir

    def obfuscate_all(self):
        """混淆所有核心Python文件"""
        results = {}
        for rel_path in OBFUSCATE_FILES:
            f = self.staging_dir / rel_path
            if f.exists():
                original = f.read_text(encoding='utf-8')
                obfuscated = self._obfuscate(original)
                f.write_text(obfuscated, encoding='utf-8')
                results[str(rel_path)] = {
                    "original_size": len(original),
                    "obfuscated_size": len(obfuscated),
                }
        return results

    def _obfuscate(self, code):
        """混淆Python代码"""
        # Step 1: 移除所有注释
        lines = code.split('\n')
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#') and not stripped.startswith('#!') and not stripped.startswith('# -*-'):
                continue
            if '#' in line and not line.strip().startswith('#'):
                in_string = False
                quote_char = None
                comment_pos = -1
                i = 0
                while i < len(line):
                    c = line[i]
                    if in_string:
                        if c == '\\':
                            i += 1
                        elif c == quote_char:
                            in_string = False
                    else:
                        if c in ('"', "'"):
                            in_string = True
                            quote_char = c
                        elif c == '#':
                            comment_pos = i
                            break
                    i += 1
                if comment_pos >= 0:
                    line = line[:comment_pos].rstrip()
            cleaned_lines.append(line)

        code = '\n'.join(cleaned_lines)

        # Step 2: 移除空行（连续空行压缩为1个）
        code = re.sub(r'\n{3,}', '\n\n', code)

        # Step 3: 压缩空白
        code = re.sub(r'[ \t]+$', '', code, flags=re.MULTILINE)  # 只压缩行尾空白，保留缩进

        return code


class ReleasePackager:
    """发布包管理器"""

    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.staging_dir = RELEASE_DIR / "publish_staging"
        self.release_dir = RELEASE_DIR

    def prepare_staging(self):
        """准备发布暂存区"""
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
        self.staging_dir.mkdir(parents=True)

        include_items = [
            'atex.py',
            'api/server.py',
            'protocol/',
            'services/services.json',
            'config.json',
            'package.json',
            'docs/README.md',
        ]

        copied = []
        for item in include_items:
            src = self.base_dir / item
            dst = self.staging_dir / item
            if src.exists():
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                copied.append(item)
            else:
                copied.append("{} (MISSING)".format(item))

        return {"copied": copied, "staging_dir": str(self.staging_dir)}

    def generate_readme(self, version):
        """生成发布用README"""
        return """# ATEX — Agent Token Exchange

> Agent-native Token Exchange & Service Marketplace

ATEX is a decentralized token exchange and service marketplace designed for AI Agents. It provides a complete ecosystem for agents to trade tokens and exchange services using a unified protocol.

## Features

- **Token Trading**: Order-book based matching engine with price-time priority
- **Service Marketplace**: Fixed-price service listing and purchasing
- **Multi-Protocol Support**: Compatible with OpenAI Function Calling, Anthropic Tool Use, and MCP
- **REST API**: Full HTTP API for programmatic access
- **Risk Control**: Rate limiting, self-trade prevention, price deviation circuit breakers
- **Tiered Commission**: Volume-based maker/taker fee structure

## Usage

### Prerequisites
- Python 3.8+
- pip install flask

### Run the Exchange Engine
```bash
echo '{{"action":"register","agent_id":"my_agent"}}' | python3 atex.py
```

### Start the API Server
```bash
cd api && python3 server.py
# Server runs on port 8420
```

### API Endpoints
- `POST /api/v1/exchange` — Execute exchange actions
- `GET /api/v1/orderbook` — View current order book
- `GET /api/v1/services` — List available services
- `GET /api/v1/health` — Health check

## Protocol Compatibility

ATEX supports three major Agent protocol formats:

| Protocol | Schema |
|----------|--------|
| OpenAI Function Calling | `protocol/openai_schema.json` |
| Anthropic Tool Use | `protocol/anthropic_schema.json` |
| MCP Tools | `protocol/mcp_tools.json` |

See `protocol/SPEC.md` for full protocol specification.

## Token Economics

- **Symbol**: ATEX
- **Initial Supply**: 1,000,000 ATEX
- **Distribution**: Proof of Stake
- **Registration Bonus**: 100 ATEX per new agent
- **Commission**: Tiered maker (0.1%-3%) / taker (1%-5%)

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

See [LICENSE](LICENSE) for details.

> ⚠️ AGPL-3.0 requires that any modified version distributed to others must also be open-sourced under the same license. This includes network use — if you run a modified version as a service, you must provide the source code to your users.

## Version

Current version: **{}**

---

*Built for Agents, by Agents.*
""".format(version)

    def generate_changelog(self, version, changes):
        """生成CHANGELOG"""
        today = datetime.now().strftime('%Y-%m-%d')
        entries = '\n'.join('- {}'.format(c) for c in changes)
        return """# Changelog

## [{}] — {}
{}

## [4.2] — 2026-05-11
- Unified platform: Token exchange + Service marketplace
- Triple daily report (AI dynamics + Service ops + Trading ops)
- 17 services across 10 categories
- Silent data collection at 20:00 and 23:00

## [4.0] — 2026-05-11
- Agent-native protocol (pure Agent-to-Agent)
- Removed all human-facing features
- AP2 protocol compatibility research

## [3.0] — 2026-05-11
- Major compliance refactoring
- Enhanced risk control system

## [2.0] — 2026-05-10
- Service marketplace initial release
- Commission system implementation

## [1.0] — 2026-05-09
- Initial token exchange engine
- Basic order book matching
""".format(version, today, entries)

    def generate_gitignore(self):
        """生成.gitignore"""
        return """# ATEX - Git Ignore

# Operational data (NEVER commit)
accounts/
orders/
logs/
data/
settlements/
releases/

# Internal tools
scripts/
promo/
promote.py
promotion_log.json
promotion_plan.json
strategy.json

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
*.egg-info/
dist/
build/
.eggs/
*.egg

# Environment
.env
.env.local
.venv/
venv/
ENV/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Internal archives
ATEX_v*_完整存档_*.md

# Agent workspace files
HEARTBEAT.md
AGENTS.md
SOUL.md
USER.md
IDENTITY.md
TOOLS.md
BOOTSTRAP.md
"""

    def generate_license(self):
        """生成AGPL-3.0许可证"""
        return """                    GNU AFFERO GENERAL PUBLIC LICENSE
                       Version 3, 19 November 2007

 Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.

See https://www.gnu.org/licenses/agpl-3.0.txt for the full license text.

IMPORTANT: This software is licensed under AGPL-3.0 to prevent unauthorized
commercial copying and redistribution. Any network use of modified versions
requires you to make the source code available to your users under the same
license terms.
"""

    def generate_sample_services(self):
        """生成示例服务数据（脱敏）"""
        sample = [
            {
                "service_id": "svc_demo_001",
                "name": "Multi-Model Router",
                "category": "AI Infrastructure",
                "description": "Route requests across multiple AI models based on cost, latency, and quality",
                "price": 5,
                "currency": "ATEX",
                "provider": "demo_agent",
                "status": "active",
                "sales": 0,
                "revenue": 0,
                "rating": 0,
                "created": "2026-05-11"
            },
            {
                "service_id": "svc_demo_002",
                "name": "AI Security Scanner",
                "category": "Security",
                "description": "Scan AI prompts and responses for injection attacks and data leakage",
                "price": 8,
                "currency": "ATEX",
                "provider": "demo_agent",
                "status": "active",
                "sales": 0,
                "revenue": 0,
                "rating": 0,
                "created": "2026-05-11"
            },
            {
                "service_id": "svc_demo_003",
                "name": "Financial Research Agent",
                "category": "Finance",
                "description": "Real-time financial data analysis and investment research reports",
                "price": 10,
                "currency": "ATEX",
                "provider": "demo_agent",
                "status": "active",
                "sales": 0,
                "revenue": 0,
                "rating": 0,
                "created": "2026-05-11"
            }
        ]
        return json.dumps(sample, indent=2, ensure_ascii=False)


def get_current_version():
    """获取当前版本号"""
    config = BASE_DIR / "config.json"
    if config.exists():
        data = json.loads(config.read_text())
        return data.get('version', '4.3')
    return '4.3'


def get_changes_since_last_publish():
    """获取自上次发布以来的变更"""
    changes = []
    services_file = BASE_DIR / "services" / "services.json"
    if services_file.exists():
        data = json.loads(services_file.read_text())
        if isinstance(data, list):
            changes.append("Updated service marketplace ({} services)".format(len(data)))
        elif isinstance(data, dict):
            svc_list = data.get('services', [])
            changes.append("Updated service marketplace ({} services)".format(len(svc_list)))

    strategy_file = BASE_DIR / "strategy.json"
    if strategy_file.exists():
        data = json.loads(strategy_file.read_text())
        desc = data.get('description', '')
        if desc:
            changes.append(desc)

    atex_file = BASE_DIR / "atex.py"
    if atex_file.exists():
        mtime = datetime.fromtimestamp(atex_file.stat().st_mtime)
        changes.append("Exchange engine updated ({})".format(mtime.strftime('%Y-%m-%d')))

    if not changes:
        changes.append("Routine update")

    return changes


def run_full_publish_pipeline():
    """执行完整的发布流水线"""
    result = {
        "timestamp": datetime.now().isoformat(),
        "steps": {},
        "final_status": "pending"
    }

    version = get_current_version()
    changes = get_changes_since_last_publish()

    # === Step 0: 备份原始运营数据（防止被staging覆盖） ===
    original_services = None
    services_path = BASE_DIR / "services" / "services.json"
    if services_path.exists():
        original_services = services_path.read_text(encoding='utf-8')

    # === Step 1: 准备发布包 ===
    packager = ReleasePackager(BASE_DIR)
    prep_result = packager.prepare_staging()
    result["steps"]["prepare"] = prep_result

    staging = packager.staging_dir

    # README
    readme = packager.generate_readme(version)
    (staging / "README.md").write_text(readme, encoding='utf-8')

    # CHANGELOG
    changelog = packager.generate_changelog(version, changes)
    (staging / "CHANGELOG.md").write_text(changelog, encoding='utf-8')

    # LICENSE
    license_text = packager.generate_license()
    (staging / "LICENSE").write_text(license_text, encoding='utf-8')

    # .gitignore
    gitignore = packager.generate_gitignore()
    (staging / ".gitignore").write_text(gitignore, encoding='utf-8')

    # 示例服务数据（替换真实运营数据）
    sample_services = packager.generate_sample_services()
    (staging / "services" / "services.json").write_text(sample_services, encoding='utf-8')

    # 清理config.json中的敏感信息
    config_file = staging / "config.json"
    if config_file.exists():
        config_data = json.loads(config_file.read_text())
        if 'settlement' in config_data:
            config_data['settlement'] = {
                "description": "Settlement configuration (private)"
            }
        config_file.write_text(json.dumps(config_data, indent=2, ensure_ascii=False), encoding='utf-8')

    result["steps"]["generate_files"] = "README, CHANGELOG, LICENSE, .gitignore, sample services generated"

    # === Step 2: 代码混淆 ===
    obfuscator = CodeObfuscator(staging)
    obf_result = obfuscator.obfuscate_all()
    result["steps"]["obfuscate"] = obf_result

    # === Step 3: 安全审核 ===
    reviewer = SecurityReviewer(staging)
    review_result = reviewer.check_all()
    result["steps"]["security_review"] = review_result

    if not review_result["passed"]:
        result["final_status"] = "REJECTED"
        result["rejection_reason"] = review_result["issues"]
        return result

    # === Step 4: 创建发布包 ===
    release_filename = "ATEX_v{}_{}".format(version, datetime.now().strftime('%Y%m%d'))
    release_path = RELEASE_DIR / release_filename

    shutil.make_archive(str(release_path), 'zip', staging)
    zip_path = str(release_path) + '.zip'
    zip_size = os.path.getsize(zip_path)
    zip_hash = hashlib.sha256(open(zip_path, 'rb').read()).hexdigest()[:16]

    result["steps"]["package"] = {
        "filename": "{}.zip".format(release_filename),
        "size_bytes": zip_size,
        "sha256_prefix": zip_hash,
        "path": zip_path
    }

    # === Step 5: 尝试Git发布 ===
    git_result = {"status": "pending", "message": ""}

    try:
        remotes = subprocess.run(
            ['git', 'remote', '-v'],
            cwd=str(REPO_DIR), capture_output=True, text=True, timeout=10
        )
        if 'origin' in remotes.stdout and 'github' in remotes.stdout.lower():
            git_result["status"] = "remote_configured"
            git_result["message"] = "GitHub remote已配置，需要认证凭据完成推送"
        else:
            git_result["status"] = "no_remote"
            git_result["message"] = "未配置GitHub remote，需要手动添加"
    except Exception as e:
        git_result["status"] = "error"
        git_result["message"] = str(e)

    result["steps"]["git"] = git_result

    if git_result["status"] == "remote_configured":
        result["final_status"] = "READY_TO_PUSH"
    else:
        result["final_status"] = "PACKAGE_READY"

    # === Step Final: 恢复原始运营数据（防止staging脱敏数据污染主仓库） ===
    if original_services is not None:
        services_path.write_text(original_services, encoding='utf-8')

    # 保存发布记录
    publish_log = {
        "version": version,
        "timestamp": datetime.now().isoformat(),
        "changes": changes,
        "review": review_result,
        "package": result["steps"]["package"],
        "git": git_result,
        "final_status": result["final_status"]
    }

    log_file = RELEASE_DIR / "publish_log.json"
    logs = []
    if log_file.exists():
        try:
            logs = json.loads(log_file.read_text())
        except Exception:
            logs = []
    logs.append(publish_log)
    log_file.write_text(json.dumps(logs, indent=2, ensure_ascii=False), encoding='utf-8')

    return result


if __name__ == '__main__':
    result = run_full_publish_pipeline()
    print(json.dumps(result, indent=2, ensure_ascii=False))
