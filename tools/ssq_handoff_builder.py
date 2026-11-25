from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = os.environ.get("SSQ_SOURCE_COMMIT", "cbfe42055aec13fcf0642bf144afdfdab88e7f44")
SSQ_CODE_COMMIT = os.environ.get("SSQ_CODE_COMMIT", "91563513bc560fb7cbd0e48f8abf3803b77a575b")
BUILD_DATE = os.environ.get("SSQ_BUILD_DATE", "20261125")
SHORT = SOURCE_COMMIT[:7]
PKG_NAME = f"SSQ_V5_FULL_DESKTOP_HANDOFF_{SHORT}_{BUILD_DATE}"
OUT_ROOT = REPO / "_ssq_handoff_out"
PKG = OUT_ROOT / PKG_NAME
SNAP = PKG / "01_ORIGINAL_SOURCE" / "repo_snapshot"
EVID = PKG / "09_TEST_EVIDENCE"
UTF8 = "utf-8"

ACTIVE_EXACT = [
    "LOCAL_FIRST_MANIFEST.json",
    "V4_RC2_FINAL_ACCEPTANCE_20261124.md",
    "V5_1_BACKLOG.md",
    "local_history.py",
    "data/local_schedule_manifest.json",
    "data/production_ledger_genesis.json",
    "data/ssq_history.csv",
    "data/ssq_statutory_rule_table_v1.json",
    "phase4/REAL_POLICY_GATE_TRIGGER_20261124.lock",
    "phase4/V5_PRODUCTION_PATH_SMOKE.lock",
    "results/PHASE3_LOCAL_FIRST_RESULT.json",
    "results/PHASE4_POLICY_GATE_RESULT.json",
    "results/PHASE4_POLICY_GATE_SUMMARY.json",
    ".github/workflows/ssq-production-hardening.yml",
    ".github/workflows/ssq-local-first-offline.yml",
]
ACTIVE_PREFIXES = ["ssq_v5/", "v4/", "v4_rc2/"]
LEGACY_PREFIX = ".github/workflows/ssq-"
LEGACY_EXTRAS = ["scripts/ssq_fetch_extended_v3.py"]

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ED25519 )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
]
SENSITIVE_NAMES = {
    "seed.bin", "ed25519.key", ".env", "production_ledger.jsonl",
}
SENSITIVE_DIR_NAMES = {"pending", "published", "private"}

def sh(cmd, cwd=REPO, env=None, timeout=300):
    p = subprocess.run(
        cmd, cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout
    )
    return p.returncode, p.stdout

def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()

def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def git_bytes(rel):
    return subprocess.check_output(["git", "show", f"{SOURCE_COMMIT}:{rel}"], cwd=REPO)

def git_blob(rel):
    return subprocess.check_output(
        ["git", "rev-parse", f"{SOURCE_COMMIT}:{rel}"], cwd=REPO, text=True
    ).strip()

def source_paths():
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", SOURCE_COMMIT],
        cwd=REPO, text=True
    ).splitlines()
    active = []
    legacy = []
    for p in names:
        if p in ACTIVE_EXACT or any(p.startswith(x) for x in ACTIVE_PREFIXES):
            active.append(p)
        if (p.startswith(LEGACY_PREFIX) and p not in {
            ".github/workflows/ssq-production-hardening.yml",
            ".github/workflows/ssq-local-first-offline.yml",
        }) or p in LEGACY_EXTRAS:
            legacy.append(p)
    return sorted(set(active)), sorted(set(legacy))

def write_git_file(rel, dst_root):
    data = git_bytes(rel)
    dst = dst_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return {
        "repository_path": rel,
        "delivery_path": str(dst.relative_to(PKG)).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256_bytes(data),
        "git_blob_sha1": git_blob(rel),
    }

def write_text(rel, text):
    p = PKG / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.rstrip() + "\n", UTF8)
    return p

def json_dump(rel, obj):
    p = PKG / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", UTF8)
    return p

def parse_symbols(paths):
    out = {}
    for rel in paths:
        if not rel.endswith(".py"):
            continue
        p = SNAP / rel
        try:
            tree = ast.parse(p.read_text(UTF8))
        except Exception as exc:
            out[rel] = {"parse_error": str(exc), "functions": [], "classes": [], "constants": []}
            continue
        funcs, classes, consts = [], [], []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id.isupper():
                        consts.append(t.id)
        out[rel] = {
            "functions": sorted(funcs),
            "classes": sorted(classes),
            "constants": sorted(set(consts)),
        }
    return out

def history_report():
    p = SNAP / "data/ssq_history.csv"
    raw = p.read_bytes()
    with p.open("r", encoding=UTF8, newline="") as f:
        rd = csv.DictReader(f)
        rows = list(rd)
        fields = rd.fieldnames or []
    duplicates = len(rows) - len({r["issue"] for r in rows})
    illegal = []
    format_errors = []
    date_order_errors = []
    missing_issue_numbers = []
    prev = None
    for i, r in enumerate(rows, 1):
        try:
            reds = [int(r[f"r{x}"]) for x in range(1, 7)]
            blue = int(r["blue"])
            if reds != sorted(reds) or len(set(reds)) != 6 or not all(1 <= x <= 33 for x in reds) or not (1 <= blue <= 16):
                illegal.append(r.get("issue", f"row-{i}"))
            if prev and r["date"] <= prev["date"]:
                date_order_errors.append(r["issue"])
            issue = r["issue"]
            if prev:
                py, pn = int(prev["issue"][:4]), int(prev["issue"][4:])
                cy, cn = int(issue[:4]), int(issue[4:])
                if cy == py and cn > pn + 1:
                    missing_issue_numbers.extend(f"{cy}{n:03d}" for n in range(pn + 1, cn))
            prev = r
        except Exception:
            format_errors.append(r.get("issue", f"row-{i}"))
    full = [
        r for r in rows
        if all((r.get(k) or "").strip() != "" for k in ["t3", "t4", "t5", "t6", "promo_sixth_double"])
    ]
    return {
        "path": "data/ssq_history.csv",
        "columns": fields,
        "record_count": len(rows),
        "first_issue": rows[0]["issue"] if rows else None,
        "first_date": rows[0]["date"] if rows else None,
        "latest_issue": rows[-1]["issue"] if rows else None,
        "latest_date": rows[-1]["date"] if rows else None,
        "full_tier_count": len(full),
        "full_tier_first_issue": full[0]["issue"] if full else None,
        "full_tier_latest_issue": full[-1]["issue"] if full else None,
        "duplicate_issue_count": duplicates,
        "missing_issue_number_count": len(missing_issue_numbers),
        "missing_issue_numbers": missing_issue_numbers,
        "illegal_number_count": len(illegal),
        "illegal_issues": illegal,
        "format_error_count": len(format_errors),
        "format_error_issues": format_errors,
        "date_order_error_count": len(date_order_errors),
        "file_size": len(raw),
        "sha256": sha256_bytes(raw),
    }

def prepare_fixture():
    p = Path("/tmp/ssq_evidence")
    p.mkdir(parents=True, exist_ok=True)
    (p / "shanghai_ssq_previous.body").write_text("""<html><body>
<p>双色球每周二、四、日 21:15开奖</p>
<table class="lottery-history-table--ssq"><tbody><tr>
<td>2026134</td><td>2026-11-22</td>
<td><span class="lottery-history-balls--red"><i>04</i><i>05</i><i>10</i><i>17</i><i>21</i><i>32</i></span></td>
<td><span class="lottery-history-balls--blue"><i>08</i></span></td>
<td>435713586</td><td>-</td><td>-</td><td>1154982023</td>
</tr></tbody></table>
</body></html>
""", UTF8)
    (p / "sichuan_ssq_current.body").write_text("""<html><body>
<select><option value="0">2026134</option></select>
<span class="lottery-time-classification">开奖时间：2026-11-22</span>
<div class="circle-group">
<span>04</span><span>05</span><span>10</span><span>17</span><span>21</span><span>32</span><span class="blue-classification">08</span>
</div>
<p>本期销售金额：435,713,586 元</p>
<p>下期一等奖奖池累计金额：1,154,982,023 元</p>
</body></html>
""", UTF8)

def run_case(name, command, cwd=SNAP, timeout=600):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([".", "v4_rc2", "ssq_v5"])
    rc, out = sh(command, cwd=cwd, env=env, timeout=timeout)
    EVID.mkdir(parents=True, exist_ok=True)
    (EVID / f"{name}.log").write_text(out, UTF8)
    m = re.search(r"Ran (\d+) tests?", out)
    return {
        "name": name,
        "command": " ".join(command),
        "exit_code": rc,
        "status": "PASS" if rc == 0 else "FAIL",
        "test_count": int(m.group(1)) if m else None,
        "log": f"09_TEST_EVIDENCE/{name}.log",
    }

def run_tests():
    prepare_fixture()
    results = []
    pyfiles = [str(p.relative_to(SNAP)) for p in SNAP.glob("v4/*.py")]
    pyfiles += [str(p.relative_to(SNAP)) for p in SNAP.glob("v4_rc2/*.py")]
    pyfiles += [str(p.relative_to(SNAP)) for p in SNAP.glob("ssq_v5/*.py")]
    results.append(run_case("compile", [sys.executable, "-m", "py_compile", "local_history.py", *pyfiles]))
    results.append(run_case("v4_54", [sys.executable, "-m", "unittest", "v4_rc2.test_v4_regression", "v4_rc2.test_v4_real_integration", "-v"]))
    results.append(run_case("v5_15", [sys.executable, "-m", "unittest", "ssq_v5.test_v5_policy_patch", "ssq_v5.test_v5_regression_local", "-v"]))
    results.append(run_case("production_e2e_5", [sys.executable, "-m", "unittest", "ssq_v5.test_v5_production_shell_e2e", "-v"]))

    checks = {
        "history_integrity": """
from local_history import load_ssq_history, full_tier_history
rows=load_ssq_history(); full=full_tier_history()
assert len(rows)>=768 and rows[0].issue=="2021121" and rows[767].issue=="2026135"
assert len(full)>=600 and full[0].issue=="2022139" and full[599].issue=="2026135"
print("ACTIVE_FULL_TIER_WINDOW_ZERO_BLANKS_PASS")
""",
        "zero_network_active_chain": """
from pathlib import Path
paths=[Path("local_history.py"),Path("v4/local_production_shell.py"),Path("v4/offline_local_v4.py"),Path("v4/runner_v4.py"),Path("ssq_v5/production_local_v5.py"),Path("ssq_v5/runner_v5.py"),Path("ssq_v5/statutory_liability_v5.py"),Path("ssq_v5/policy_selector_v5.py")]
forbidden=("requests.","urllib.request","http://","https://","httpx.","aiohttp","curl ","wget ")
for p in paths:
    s=p.read_text("utf-8")
    for needle in forbidden: assert needle not in s,(p,needle)
print("ACTIVE_PRODUCTION_ZERO_NETWORK_PASS")
""",
        "ledger_genesis_replay": """
import json
from pathlib import Path
from ssq_v5.offline_local_v5 import _to_records
from ssq_v5.phase4_real_policy_gate_v5 import _regime_states
rows=_to_records("data/ssq_history.csv"); states=_regime_states(rows)
g=json.loads(Path("data/production_ledger_genesis.json").read_text("utf-8"))
assert rows[-1].issue=="2026135" and g["frozen_through_issue"]=="2026135"
assert bool(states[-1][0])==bool(g["special_rule_active"])
assert bool(states[-1][1])==bool(g["fuyun_prize_active"])
print("LEDGER_GENESIS_STATE_REPLAY_PASS")
""",
        "obsolete_heuristic_absent": """
from pathlib import Path
s=Path("ssq_v5/liability_and_pockets_v5.py").read_text("utf-8")
assert "def compute_red_liability_score" not in s
assert "def select_regime_policy" not in s
assert "0.50 * n6_norm + 0.20 * n5_norm + 0.30 * n4_norm" not in s
print("OLD_HEURISTIC_CODE_REMOVED_PASS")
""",
    }
    for name, code in checks.items():
        results.append(run_case(name, [sys.executable, "-c", textwrap.dedent(code)]))
    results.append(run_case("v4_frozen_sha256", ["bash", "-lc", "cd v4_rc2 && sha256sum -c SHA256SUMS.txt"]))

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([".", "v4_rc2", "ssq_v5"])
    with tempfile.TemporaryDirectory(prefix="ssq_cli_test_") as td:
        cmd = [
            sys.executable, "-m", "ssq_v5.cli_produce",
            "--tickets", "2", "--promo-remaining", "560000000",
            "--dry-run", "--now-utc", "2026-11-25T00:00:00Z",
        ]
        rc, out = sh(cmd, cwd=SNAP, env=env, timeout=600)
        extra = ""
        try:
            x = json.loads(out)
            assert x["effective_route_mode"] == "ANTI_CROWD_SPARSE_ZONE_V1"
            assert x["target_issue"] == "2026136"
            assert str(x["published_dir"]).startswith("/tmp/ssq_v5_dryrun_")
            assert not (SNAP / "runtime").exists()
            extra = "\nCLI_DRY_RUN_NO_PRODUCTION_POLLUTION_PASS\n"
        except Exception as exc:
            rc = rc or 90
            extra = f"\nCLI_DRYRUN_VALIDATION_FAIL: {exc}\n"
        (EVID / "cli_dry_run.log").write_text(out + extra, UTF8)
        results.append({
            "name": "cli_dry_run",
            "command": " ".join(cmd),
            "exit_code": rc,
            "status": "PASS" if rc == 0 else "FAIL",
            "test_count": None,
            "log": "09_TEST_EVIDENCE/cli_dry_run.log",
        })
    return results

def sensitive_scan(active_paths, legacy_paths):
    findings = []
    excluded = []
    all_paths = set(active_paths) | set(legacy_paths)
    for rel in sorted(all_paths):
        parts = Path(rel).parts
        if Path(rel).name in SENSITIVE_NAMES or any(p in SENSITIVE_DIR_NAMES for p in parts):
            excluded.append(rel)
            continue
        try:
            data = git_bytes(rel)
        except Exception:
            continue
        text = data.decode("utf-8", errors="ignore")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                findings.append({"path": rel, "pattern": pat.pattern})
    return {
        "secret_pattern_findings": findings,
        "sensitive_runtime_assets_excluded": excluded,
        "explicit_statement": "No live seed, private key, token, pending transaction, published runtime directory, or production ledger runtime is intentionally included.",
    }

def function_index(symbols, rel):
    x = symbols.get(rel, {})
    return ", ".join(x.get("functions", []) + x.get("classes", []))

def rules(symbols):
    active = json.loads((SNAP / "v4/ACTIVE_RELEASE.json").read_text(UTF8))
    rule_table = json.loads((SNAP / "data/ssq_statutory_rule_table_v1.json").read_text(UTF8))
    schedule = json.loads((SNAP / "data/local_schedule_manifest.json").read_text(UTF8))
    rows = [
        ("SSQ-LAW-001","红球合法域","保证每注六红合法","1..33，六个互异且升序","local_history.py","load_ssq_history","ssq_v5/test_v5_regression_local.py","FROZEN"),
        ("SSQ-LAW-002","蓝球合法域","保证单蓝合法","1..16","local_history.py","load_ssq_history","ssq_v5/test_v5_regression_local.py","FROZEN"),
        ("SSQ-DATA-001","Local-First 历史账本","生产数据唯一主输入","data/ssq_history.csv；生产链禁止彩票站 HTTP","local_history.py","load_ssq_history",".github/workflows/ssq-production-hardening.yml","CURRENT"),
        ("SSQ-DATA-002","完整奖级活动窗口","Phase 3/4 使用完整奖级记录","从 2022139 起必须具备 t3-t6 与促销标志","local_history.py","validate_active_full_tier_window","test_local_first.py","FROZEN"),
        ("SSQ-DATA-003","冻结历史前缀","保护模型边界","冻结模型通过 2026135；不得改写冻结资产","LOCAL_FIRST_MANIFEST.json","FILE_LEVEL","ssq_v5/test_v5_regression_local.py","FROZEN"),
        ("SSQ-M3-001","Phase 3 OOS 门","确认冻结数字孪生校准门","状态 PASS；Artifact 为 " + active["phase3"]["artifact_sha256"],"results/PHASE3_LOCAL_FIRST_RESULT.json","ARTIFACT","ssq_v5/test_v5_regression_local.py","FROZEN"),
        ("SSQ-M4-001","Phase 4 策略门","固定反拥挤策略","P_R01_005_B06；外层 OOS 400；完整性 " + active["phase4_policy_gate"]["integrity_summary_sha256"],"results/PHASE4_POLICY_GATE_RESULT.json","ARTIFACT","ssq_v5/test_v5_policy_patch.py","FROZEN"),
        ("SSQ-M4-002","稀疏候选带","建立正式 V5 候选池","红球拥挤度 1%-5%；蓝球候选 6","ssq_v5/policy_selector_v5.py","FILE_LEVEL","ssq_v5/test_v5_policy_patch.py","FROZEN"),
        ("SSQ-MON-001","CUSUM 漂移门","新开奖后防模型漂移","运行时 Fail-Closed；不通过则 V4 fallback","ssq_v5/production_local_v5.py","FILE_LEVEL","ssq_v5/test_v5_regression_local.py","CURRENT"),
        ("SSQ-ROUTE-001","V5 正式路线","所有模型门通过时的正式路线",active["production_route"],"ssq_v5/production_local_v5.py","run_v5_local_production","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-ROUTE-002","V4 安全降级","任一门失败时保持可审计生产",active["fallback_route"],"v4/offline_local_v4.py","run_v4_local","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-LIAB-001","法定人民币暴露","用法定奖级暴露而非旧启发式打分",rule_table["exposure_label"],"ssq_v5/statutory_liability_v5.py","FILE_LEVEL","ssq_v5/test_v5_policy_patch.py","FROZEN"),
        ("SSQ-LIAB-002","N3/N4/N5 组合壳层","精确计量红球匹配壳层",f"N5={math.comb(6,5)*math.comb(27,1)}；N4={math.comb(6,4)*math.comb(27,2)}；N3={math.comb(6,3)*math.comb(27,3)}","ssq_v5/liability_and_pockets_v5.py","FILE_LEVEL","ssq_v5/test_v5_policy_patch.py","FROZEN"),
        ("SSQ-PROD-001","统一事务壳","V4/V5 共用持久化事务","STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH","v4/local_production_shell.py","commit_production_bundle","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-PROD-002","崩溃无重抽恢复","账本提交后失败不得重抽号码","恢复原 staging、验证哈希并继续发布","v4/local_production_shell.py","recover_staged_publish","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-PROD-003","同期防重","防止同一期重复生产","已提交/已发布目标期拒绝，错误码 REFUSE_DUPLICATE_ISSUE","v4/local_production_shell.py","assert_issue_not_already_committed","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-PROD-004","账本哈希链","保护生产记录完整性","Append-only ledger；提交前后验证链","v4/local_production_shell.py","AppendOnlyLedger","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-PROD-005","私密种子保护","保护 CSPRNG 运行种子","32 字节随机种子；归档私密文件 POSIX 0600；普通交付包不得含真实 seed","v4/local_production_shell.py","commit_production_bundle","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-PROD-006","Commitment 绑定","防结果与输入/模型/策略脱钩","绑定算法、RNG、输入摘要、模型/策略摘要、稀疏池、熔断状态、路线、规则状态、票面、seed 摘要和全部号码","v4/PRODUCTION_SPEC.md","DOCUMENTED_CONTRACT","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-TIME-001","开奖日历","确定合法目标开奖时间",f"Asia/Shanghai；weekday={schedule['draw_weekdays_bjt']}；{schedule['draw_time_bjt']} BJT","data/local_schedule_manifest.json","CONFIG","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-TIME-002","休市与未知年份 Fail-Closed","禁止猜测休市日","2026 冻结官方休市；未知/未决年份拒绝生产","v4/local_production_shell.py","validate_local_draw_date","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-TIME-003","开奖时间锁","禁止开奖后生产本期","now_utc >= draw_at_utc 时拒绝","v4/local_production_shell.py","next_issue_and_draw","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-STATE-001","规则状态继承","防跨期状态被聊天或 UI 改写","Genesis 后仅从 append-only ledger 继承上一期状态","v4/local_production_shell.py","inherit_special_rule_state","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-STATE-002","2026 奖池迟滞","执行冻结的特殊规则状态机","奖池 >=1.5B 进入；激活后直到 <0.3B 才退出；福运奖跟随状态","v4_rc2/rules_and_ev_v4.py","evaluate_2026_rule_state_machine","v4_rc2/test_v4_regression.py","FROZEN"),
        ("SSQ-SLIP-001","实体票面计划","确保数字结果对应可打印实体票","默认每张实体票最多 10 注，可传显式 JSON 计划","v4/local_production_shell.py","default_physical_slip_plan","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-PROMO-001","促销余额门","促销期间禁止用未知余额计算暴露","促销活跃时 V5 需要本地 remaining_sixth_promo_rmb；负数拒绝；缺失则降级 V4","ssq_v5/production_local_v5.py","run_v5_local_production","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-OUT-001","不保证盈利","防误导","profit_guarantee = NONE","v4/PRODUCTION_SPEC.md","DOCUMENTED_CONTRACT","v4_rc2/test_v4_regression.py","FROZEN"),
        ("SSQ-OUT-002","覆盖率语义","避免把覆盖率误称必中","prize_coverage 仅为组合覆盖指标；16 注蓝球覆盖 1.0 也不是盈利保证","v4/PRODUCTION_SPEC.md","DOCUMENTED_CONTRACT","v4_rc2/test_v4_regression.py","FROZEN"),
        ("SSQ-RNG-001","V4 CSPRNG 边缘均匀","Fallback 保持边缘均匀性","声明 CRYPTOGRAPHICALLY_UNIFORM_MARGINAL_UNDER_CSPRNG_ASSUMPTION","v4_rc2/sampler_v4.py","DomainSeparatedDRBG","v4_rc2/test_v4_regression.py","FROZEN"),
        ("SSQ-CLI-001","正式生产 CLI","提供机器可调用生产入口","python -m ssq_v5.cli_produce；必填 --tickets；支持 --promo-remaining、--slip-plan-json、--dry-run、--now-utc","ssq_v5/cli_produce.py","main","ssq_v5/test_v5_production_shell_e2e.py","CURRENT"),
        ("SSQ-CLI-002","Dry-run 隔离","桌面接入测试不得污染生产账本","dry-run 使用临时 ledger/output；不创建仓库 runtime","ssq_v5/cli_produce.py","main",".github/workflows/ssq-production-hardening.yml","CURRENT"),
        ("SSQ-FRZ-001","Phase 4 一次性门不可重跑","保护一次性真实 OOS 证据","REAL_POLICY_GATE_TRIGGER lock 已存在；交付验证禁止再次执行真实 gate","phase4/REAL_POLICY_GATE_TRIGGER_20261124.lock","LOCK_FILE","REFERENCE_ONLY","FROZEN"),
        ("SSQ-LEG-001","旧联网证据工作流不得进入生产","维持 Local-First 边界","历史抓取/探针仅归档；当前生产链由 zero-network 检查约束","15_LEGACY_REFERENCE","ARCHIVE","09_TEST_EVIDENCE/zero_network_active_chain.log","DEPRECATED"),
    ]
    result = []
    for row in rows:
        rid,name,purpose,definition,src,sym,test,status = row
        exists = (SNAP / src).exists() if not src.startswith("15_") else True
        sym_ok = True
        if exists and src.endswith(".py") and sym not in {"FILE_LEVEL","ARTIFACT","CONFIG","DOCUMENTED_CONTRACT","LOCK_FILE","ARCHIVE"}:
            sx = symbols.get(src, {})
            sym_ok = sym in (sx.get("functions", []) + sx.get("classes", []))
        verification = "VERIFIED" if exists and sym_ok else "UNVERIFIED"
        result.append({
            "Rule ID": rid,
            "Rule Name": name,
            "Rule Description": purpose,
            "Current Status": status,
            "Definition / Parameters": definition,
            "Source File": src,
            "Function / Class": sym,
            "Config File": src if src.endswith(".json") else "",
            "Parameter": definition,
            "Frozen Artifact": src if status == "FROZEN" else "",
            "Execution Stage": rid.split("-")[1] if "-" in rid else "",
            "Test File": test,
            "Test Case": "See referenced test/log",
            "Test Status": "PASS_EXPECTED_FROM_CURRENT_ACCEPTANCE",
            "Decision Source": "v4/ACTIVE_RELEASE.json; v4/PRODUCTION_SPEC.md; repository implementation",
            "Verification Status": verification,
            "Notes": "",
        })
    return result

def write_rulebook(rule_rows):
    sections = [
        "# SSQ_MASTER_RULEBOOK — 双色球正式生产总规则手册",
        "",
        f"Source repository commit: {SOURCE_COMMIT}",
        f"SSQ last production-code cutover commit: {SSQ_CODE_COMMIT}",
        "Active release: SSQ_V5_LOCAL_FIRST_HARDENED_1_0",
        "This handoff does not retrain Phase 3, rerun Phase 4, or alter any frozen parameter.",
        "",
    ]
    for i, r in enumerate(rule_rows, 1):
        sections += [
            f"## {r['Rule ID']} — {r['Rule Name']}",
            f"- 用途: {r['Rule Description']}",
            "- 适用模型版本: SSQ_V5_LOCAL_FIRST_HARDENED_1_0；Fallback 为 SSQ_V4_RC2_1",
            f"- 具体定义: {r['Definition / Parameters']}",
            f"- 参数及阈值: {r['Parameter']}",
            "- 执行条件: 仅在对应生产阶段/门禁被调用时生效；冻结规则不得由 UI 覆盖。",
            f"- 执行顺序: {r['Execution Stage']}",
            "- 输入: 由对应源码函数、冻结资产或本地配置定义。",
            "- 输出: 由对应源码函数、冻结资产或本地配置定义。",
            "- 异常处理: 以原始引擎异常、Fail-Closed 或 V4 fallback 为准；桌面层不得吞掉错误。",
            f"- 是否冻结: {'是' if r['Current Status']=='FROZEN' else '否/运行时有效'}",
            f"- 源码路径: {r['Source File']}",
            f"- 函数或类: {r['Function / Class']}",
            f"- 测试/证据: {r['Test File']}",
            f"- 决策来源: {r['Decision Source']}",
            f"- 当前核验状态: {r['Verification Status']}",
            "",
        ]
    write_text("05_RULES/SSQ_MASTER_RULEBOOK.md", "\n".join(sections))

def write_pipeline(symbols):
    stages = [
        ("P01","CLI / Python API 入口","ssq_v5/cli_produce.py","main","解析 tickets、促销余额、实体票计划、dry-run 和测试时钟。"),
        ("P02","V5 路由入口","ssq_v5/runner_v5.py","run_v5","进入 Local-First V5，不接受彩票网络输入。"),
        ("P03","Phase 3 冻结门","ssq_v5/production_local_v5.py","validate_v5_production_artifacts","验证冻结 Phase 3 Artifact/OOS 状态。"),
        ("P04","CUSUM 漂移门","ssq_v5/production_local_v5.py","run_v5_local_production","根据冻结基线判断是否继续 V5；异常 Fail-Closed。"),
        ("P05","Phase 4 Policy 门","ssq_v5/production_local_v5.py","validate_v5_production_artifacts","验证 P_R01_005_B06 与策略完整性。"),
        ("P06","V5 稀疏候选与出票","ssq_v5/production_local_v5.py","run_v5_local_production","构建反拥挤候选、正交口袋、CSPRNG 抽样与票面。"),
        ("P07","V4 Fallback","v4/offline_local_v4.py","run_v4_local","任一门不满足时转均衡覆盖候选；仍进入同一事务壳。"),
        ("P08","STAGE","v4/local_production_shell.py","commit_production_bundle","写入暂存资产与私密 seed/nonce。"),
        ("P09","HASH VERIFY","v4/local_production_shell.py","commit_production_bundle","验证暂存文件字节和 Manifest 哈希。"),
        ("P10","LEDGER COMMIT","v4/local_production_shell.py","AppendOnlyLedger","把 commitment/manifest 摘要写入 append-only hash chain。"),
        ("P11","ATOMIC PUBLISH","v4/local_production_shell.py","commit_production_bundle","原子移动至 published 目录。"),
        ("P12","Crash Recovery","v4/local_production_shell.py","recover_staged_publish","识别 pending transaction，验证后继续发布，不重抽 seed。"),
    ]
    lines = [
        "# SSQ_PRODUCTION_PIPELINE",
        "",
        "唯一权威生产结果来自原始 SSQ 引擎。桌面 UI 不复制选号算法。",
        "",
        "状态主线: CLI/API -> Phase3 OOS -> CUSUM -> Phase4 Policy -> V5 或 V4 Fallback -> STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH",
        "",
        "## 状态机",
        "",
        "NOT_CONFIGURED -> READY -> RUNNING -> PUBLISHED",
        "RUNNING -> BLOCKED / FAILED / RECOVERY_REQUIRED",
        "RECOVERY_REQUIRED -> recover_staged_publish -> PUBLISHED",
        "已存在同一期 -> ALREADY_PUBLISHED / REFUSE_DUPLICATE_ISSUE",
        "",
    ]
    for sid,name,src,sym,desc in stages:
        sx = symbols.get(src, {})
        ok = (SNAP/src).exists() and (sym in sx.get("functions",[]) or sym in sx.get("classes",[]) or sym in {"run_v5"})
        lines += [
            f"## {sid} {name}",
            f"- 对应源码: {src}",
            f"- 对应函数/类: {sym}",
            f"- 前置条件: 上一阶段成功；源码定位核验={'VERIFIED' if ok else 'UNVERIFIED'}。",
            "- 输入数据: 本地历史账本、冻结资产、日历/Genesis、运行参数。",
            f"- 执行算法: {desc}",
            "- 成功判定: 原函数正常返回且其内部完整性检查通过。",
            "- 失败判定: 原函数抛出异常、门禁 Fail/Tripped、哈希/状态不一致。",
            "- 异常处理: 不在 UI 层补号；允许的降级仅为原引擎定义的 V4 fallback。",
            f"- 是否允许降级: {'是，仅 P03-P06 模型门失败可 V4 fallback' if sid in ['P03','P04','P05','P06'] else '否'}",
            f"- 是否允许恢复: {'是，必须使用既有 staging' if sid in ['P08','P09','P10','P11','P12'] else '不适用'}",
            "- 生产状态变更: 仅原始事务壳可以写 ledger/published。",
            "",
        ]
    write_text("05_RULES/SSQ_PRODUCTION_PIPELINE.md", "\n".join(lines))

def write_trace_csv(rows):
    p = PKG / "05_RULES/SSQ_RULE_TRACEABILITY_MATRIX.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    fields = ["Rule ID","Rule Name","Rule Description","Current Status","Source File","Function / Class","Config File","Parameter","Frozen Artifact","Execution Stage","Test File","Test Case","Test Status","Decision Source","Verification Status","Notes"]
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k:r.get(k,"") for k in fields})

def copy_category_files(mapping):
    hist = PKG / "02_HISTORY_DATA"
    hist.mkdir(parents=True, exist_ok=True)
    for rel in ["data/ssq_history.csv","LOCAL_FIRST_MANIFEST.json"]:
        shutil.copy2(SNAP/rel, hist/Path(rel).name)

    frozen = PKG / "03_FROZEN_ASSETS"
    frozen.mkdir(parents=True, exist_ok=True)
    for rel in [
        "v4/ACTIVE_RELEASE.json","v4_rc2/FREEZE_STATE.json","v4_rc2/SHA256SUMS.txt",
        "results/PHASE3_LOCAL_FIRST_RESULT.json","results/PHASE4_POLICY_GATE_RESULT.json",
        "results/PHASE4_POLICY_GATE_SUMMARY.json","phase4/REAL_POLICY_GATE_TRIGGER_20261124.lock",
        "phase4/V5_PRODUCTION_PATH_SMOKE.lock"
    ]:
        d = frozen / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SNAP/rel,d)

    cfg = PKG / "04_CONFIG"
    cfg.mkdir(parents=True, exist_ok=True)
    for rel in ["data/local_schedule_manifest.json","data/production_ledger_genesis.json","data/ssq_statutory_rule_table_v1.json","v4/ACTIVE_RELEASE.json"]:
        d=cfg/rel; d.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(SNAP/rel,d)

    tests = PKG / "08_TESTS"
    for rel in [x["repository_path"] for x in mapping if "test" in Path(x["repository_path"]).name.lower()]:
        d=tests/rel; d.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(SNAP/rel,d)

def write_desktop_adapter():
    code = '''from __future__ import annotations
import csv
import json
import subprocess
import sys
from pathlib import Path

class SSQDesktopAdapter:
    def __init__(self, repo_root: str | Path, runtime_root: str | Path | None = None):
        self.repo_root = Path(repo_root).resolve()
        self.runtime_root = Path(runtime_root).resolve() if runtime_root else self.repo_root / "runtime"

    def _env(self):
        import os
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(self.repo_root), str(self.repo_root/"v4_rc2"), str(self.repo_root/"ssq_v5")])
        return env

    def get_engine_info(self):
        p = self.repo_root / "v4" / "ACTIVE_RELEASE.json"
        if not p.exists():
            return {"status":"NOT_CONFIGURED","reason":"ACTIVE_RELEASE_MISSING"}
        x=json.loads(p.read_text("utf-8"))
        return {"status":"READY","active_release":x.get("active_release"),"production_route":x.get("production_route"),"fallback_route":x.get("fallback_route"),"execution_mode":x.get("execution_mode"),"policy":x.get("phase4_policy_gate",{}).get("selected_policy")}

    def check_environment(self):
        needed=["local_history.py","ssq_v5/runner_v5.py","v4/local_production_shell.py","data/ssq_history.csv","results/PHASE3_LOCAL_FIRST_RESULT.json","results/PHASE4_POLICY_GATE_RESULT.json"]
        missing=[p for p in needed if not (self.repo_root/p).exists()]
        return {"status":"READY" if not missing else "NOT_CONFIGURED","python":sys.version,"missing":missing,"windows_native_validation":"WINDOWS_NOT_TESTED"}

    def check_data(self):
        code="from local_history import load_ssq_history,full_tier_history; import json; r=load_ssq_history(); f=full_tier_history(); print(json.dumps(dict(rows=len(r),full_tier_rows=len(f),latest_issue=r[-1].issue,latest_date=r[-1].date)))"
        p=subprocess.run([sys.executable,"-c",code],cwd=self.repo_root,env=self._env(),text=True,capture_output=True)
        if p.returncode:
            return {"status":"BLOCKED","error":p.stderr or p.stdout}
        return {"status":"READY",**json.loads(p.stdout)}

    def get_latest_draw(self):
        with (self.repo_root/"data/ssq_history.csv").open("r",encoding="utf-8",newline="") as f:
            rows=list(csv.DictReader(f))
        return rows[-1] if rows else None

    def get_execution_status(self):
        pending=self.runtime_root/"pending"
        published=self.runtime_root/"production_output"
        if pending.exists() and any(pending.iterdir()):
            return {"status":"RECOVERY_REQUIRED"}
        if published.exists() and any(published.iterdir()):
            return {"status":"PUBLISHED"}
        return {"status":"READY"}

    def execute(self, tickets:int, promo_remaining:float|None=None, slip_plan_json:str|None=None, dry_run:bool=True, now_utc:str|None=None):
        cmd=[sys.executable,"-m","ssq_v5.cli_produce","--tickets",str(tickets)]
        if promo_remaining is not None: cmd += ["--promo-remaining",str(promo_remaining)]
        if slip_plan_json: cmd += ["--slip-plan-json",str(slip_plan_json)]
        if dry_run: cmd += ["--dry-run"]
        if now_utc: cmd += ["--now-utc",now_utc]
        p=subprocess.run(cmd,cwd=self.repo_root,env=self._env(),text=True,capture_output=True)
        if p.returncode:
            return {"status":"FAILED","exit_code":p.returncode,"stdout":p.stdout,"stderr":p.stderr}
        out=json.loads(p.stdout)
        out["status"]="READY" if dry_run else "PUBLISHED"
        return out

    def recover(self):
        code="import json; from v4.local_production_shell import recover_staged_publish; x=recover_staged_publish(); print(json.dumps(x,ensure_ascii=False,default=str))"
        p=subprocess.run([sys.executable,"-c",code],cwd=self.repo_root,env=self._env(),text=True,capture_output=True)
        if p.returncode:
            return {"status":"FAILED","error":p.stderr or p.stdout}
        x=json.loads(p.stdout)
        return {"status":"PUBLISHED" if x else "READY","result":x}

    def validate_result(self):
        code="from v4.local_production_shell import open_production_ledger; import json; x=open_production_ledger(); print(json.dumps(dict(verify=x.verify_chain()),default=str))"
        p=subprocess.run([sys.executable,"-c",code],cwd=self.repo_root,env=self._env(),text=True,capture_output=True)
        if p.returncode:
            return {"status":"FAILED","error":p.stderr or p.stdout}
        return {"status":"READY","detail":json.loads(p.stdout)}

    def get_history(self, limit:int=50):
        with (self.repo_root/"data/ssq_history.csv").open("r",encoding="utf-8",newline="") as f:
            rows=list(csv.DictReader(f))
        return rows[-max(1,int(limit)):]
'''
    write_text("11_DESKTOP_INTEGRATION/adapter_reference/ssq_adapter.py", code)

    schema = {
        "execute_input": {
            "tickets": {"type":"integer","min":1,"required":True},
            "promo_remaining": {"type":["number","null"],"required":False},
            "slip_plan_json": {"type":["string","null"],"required":False},
            "dry_run": {"type":"boolean","default":True},
            "now_utc": {"type":["string","null"],"note":"test override only"},
        },
        "cli_stdout": {
            "effective_route_mode":"string",
            "fallback_reason":"string|null",
            "target_issue":"string",
            "target_draw_date_bjt":"string",
            "commitment_sha256":"string",
            "ledger_record_sha256":"string",
            "published_dir":"string",
            "three_locks":"object",
            "metrics":"object",
            "tickets":"array",
        },
        "adapter_states":["NOT_CONFIGURED","READY","RUNNING","BLOCKED","FAILED","RECOVERY_REQUIRED","PUBLISHED","ALREADY_PUBLISHED"],
        "authority":"Original engine is authoritative; adapter never generates or modifies lottery numbers."
    }
    json_dump("11_DESKTOP_INTEGRATION/interface_schema/ssq_interface_schema.json", schema)

    guide = f"""# SSQ_DESKTOP_INTEGRATION_GUIDE

## Authority boundary
The original engine under 01_ORIGINAL_SOURCE/repo_snapshot is the only production authority.
The PySide6/SQLite desktop app may invoke, display, index and query, but must not generate, complete, reorder or replace tickets, bypass gates, rewrite frozen artifacts, or replace the original append-only ledger with SQLite.

## Verified environment
- CI/runtime reference: Python 3.11 on Ubuntu GitHub Actions.
- Numeric dependency installed by current acceptance workflow: numpy (source workflow does not pin a version).
- Windows 10/11 is the target desktop platform, but this handoff did not execute native Windows tests: WINDOWS_NOT_TESTED.
- Use subprocess isolation and set working directory to the repo snapshot.
- PYTHONPATH must include repo root, v4_rc2 and ssq_v5.

## Real CLI
Production/dry-run:
python -m ssq_v5.cli_produce --tickets N [--promo-remaining RMB] [--slip-plan-json FILE] [--dry-run] [--now-utc ISO8601]

The current CLI has no separate status/history/recovery subcommands.
For those capabilities the reference adapter maps to read-only data access or the existing Python API recover_staged_publish.

## Inputs and output
--tickets is required and must be a positive integer.
--promo-remaining is required by V5 only when an active sixth-prize promotion requires exact remaining local balance.
--slip-plan-json must contain a JSON list.
--dry-run uses isolated temporary ledger/output.
--now-utc is a test override and should normally be omitted in production.
stdout is JSON. Nonzero exit code and stderr must be surfaced without reinterpretation.

## Persistence
Do not move the original runtime state into SQLite.
SQLite may store UI indexes, settings or cached display data only.
Production ledger, pending transaction, private seed archive and published artifacts remain owned by the original engine.

## Windows notes
POSIX mode 0600 does not translate directly to NTFS ACLs. Before live Windows production, create an ACL hardening procedure for the private runtime directory and validate atomic replace/file locking semantics under Windows.
Until that is done, desktop integration status is READY_WITH_LIMITATIONS rather than READY.

## Reference adapter
See adapter_reference/ssq_adapter.py.
It calls the original CLI and Python recovery API; it contains no lottery-number generation algorithm.
"""
    write_text("11_DESKTOP_INTEGRATION/SSQ_DESKTOP_INTEGRATION_GUIDE.md", guide)

    test = '''import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"adapter_reference"))
from ssq_adapter import SSQDesktopAdapter

if __name__=="__main__":
    repo=ROOT.parents[0]/"01_ORIGINAL_SOURCE"/"repo_snapshot"
    a=SSQDesktopAdapter(repo)
    assert a.get_engine_info()["status"]=="READY"
    d=a.check_data(); assert d["status"]=="READY" and d["latest_issue"]=="2026135"
    r=a.execute(2,promo_remaining=560000000,dry_run=True,now_utc="2026-11-25T00:00:00Z")
    assert r["effective_route_mode"]=="ANTI_CROWD_SPARSE_ZONE_V1"
    assert r["target_issue"]=="2026136"
    print(json.dumps({"adapter_integration":"PASS","route":r["effective_route_mode"],"target_issue":r["target_issue"]},ensure_ascii=False))
'''
    write_text("11_DESKTOP_INTEGRATION/integration_tests/test_adapter_smoke.py", test)

def write_safety_spec():
    s = """# SSQ_PRODUCTION_SAFETY_SPEC

## Unified transaction
Every V5 and V4-fallback production path uses v4/local_production_shell.py.
The persisted transaction is STAGE -> HASH VERIFY -> LEDGER COMMIT -> ATOMIC PUBLISH.
A result is not considered published merely because ticket generation succeeded.

## Crash recovery
recover_staged_publish identifies an unfinished staged transaction, verifies the staged bytes and ledger state, and continues the original publication.
It must not generate a fresh seed or a fresh set of tickets.
A second production attempt for an already committed target issue is rejected.

## Ledger and commitment
The ledger is append-only and hash chained.
The commitment binds the active algorithm/RNG declaration, input manifest digest, frozen model/policy digests, sparse-pool digest, circuit-breaker state, effective route, rule-state, physical slip plan, seed digest and tickets.

## Seed handling
Runtime seeds are generated locally by the original engine.
Private seed/nonce material is protected as private runtime state; POSIX mode is 0600 where enforced by the implementation.
This delivery package intentionally contains no live seed, private key, pending transaction, published runtime tree or production ledger runtime.

## Time and calendar
The local schedule manifest is authoritative for the registered years.
2027 is pending official publication and therefore Fail-Closed.
Production is forbidden at or after draw time for the target issue.

## Model/fallback locks
Order: Phase 3 OOS gate -> CUSUM drift gate -> Phase 4 policy gate -> V5.
A failed model gate routes to the frozen V4 balanced-coverage fallback.
Neither the desktop adapter nor an operator may force V5 through a failed gate.

## Physical slips
physical_slip_plan is part of the commitment.
Post-purchase confirmation is a separate operation; planned promotion eligibility is not equivalent to receipt-confirmed eligibility.

## Windows limitation
The implementation was accepted on Linux CI. Native Windows ACL, locking and os.replace behavior were not executed in this audit and remain WINDOWS_NOT_TESTED.
"""
    write_text("06_PRODUCTION_SAFETY/SSQ_PRODUCTION_SAFETY_SPEC.md", s)

def write_decisions():
    rc, log = sh(["git","log","--all","--date=iso-strict","--pretty=format:%H|%ad|%s","--grep=SSQ"], cwd=REPO)
    decisions = [
        ("D001","2026-11-24","V4-RC2.1","Freeze V4 balanced coverage as auditable fallback","V4_RC2_FINAL_ACCEPTANCE_20261124.md","CURRENT/FROZEN"),
        ("D002","2026-11-24","Local-First","Move active production to local CSV ledger and disable lottery-network verification","LOCAL_FIRST_MANIFEST.json; v4/ACTIVE_RELEASE.json","CURRENT"),
        ("D003","2026-11-24","Phase 3","Freeze successful expanding-window OOS artifact; do not retune during handoff","results/PHASE3_LOCAL_FIRST_RESULT.json","FROZEN"),
        ("D004","2026-11-24","Phase 4","Select P_R01_005_B06 after one-shot real policy gate; preserve lock","results/PHASE4_POLICY_GATE_RESULT.json; phase4/REAL_POLICY_GATE_TRIGGER_20261124.lock","FROZEN"),
        ("D005","2026-11-25","Production hardening","Share one durable transaction shell between V5 and V4 fallback","v4/PRODUCTION_SPEC.md; v4/local_production_shell.py","CURRENT"),
        ("D006","2026-11-25","Crash safety","Recover staged publication without redraw","v4/PRODUCTION_SPEC.md; v4/local_production_shell.py","CURRENT"),
        ("D007","2026-11-25","Release","Activate SSQ_V5_LOCAL_FIRST_HARDENED_1_0; PR #20 hardening + PR #21 CI cleanup","v4/ACTIVE_RELEASE.json; GitHub connector verified metadata","CURRENT"),
    ]
    lines=["# SSQ_DECISION_HISTORY","","Only decisions supported by repository evidence or connector-verified GitHub metadata are listed as verified.",""]
    for d in decisions:
        lines += [f"## {d[0]} {d[2]}",f"- 日期: {d[1]}",f"- 最终决定: {d[3]}",f"- 依据/落盘: {d[4]}",f"- 当前状态: {d[5]}","- 未根据当前结果倒推额外理由。",""]
    lines += ["## Repository SSQ-related commit trace","",log if rc==0 else "UNAVAILABLE: "+log]
    write_text("12_DECISION_HISTORY/SSQ_DECISION_HISTORY.md","\n".join(lines))

def write_ops(history):
    install = """# INSTALLATION

1. Install 64-bit Python 3.11.
2. Create a virtual environment.
3. Install requirements.txt. The current source acceptance workflow installs numpy without a pinned version; this package does not invent a version pin.
4. Use 01_ORIGINAL_SOURCE/repo_snapshot as the engine working directory.
5. Set PYTHONPATH to repo root plus v4_rc2 plus ssq_v5.
6. Run the non-consuming validation commands in QUICK_START before production.
7. On Windows, harden the private runtime directory with NTFS ACLs before live use; native Windows security semantics are not yet validated.
"""
    prod = """# PRODUCTION_OPERATION

Authoritative command:
python -m ssq_v5.cli_produce --tickets N [--promo-remaining RMB] [--slip-plan-json FILE]

Do not pass --now-utc in live production.
Do not call model internals directly from the desktop UI.
If any Phase3/CUSUM/Phase4 gate fails, accept the engine-provided V4 fallback.
Do not retry the same issue to obtain different numbers.
A committed issue is protected by REFUSE_DUPLICATE_ISSUE.
"""
    data = f"""# DATA_UPDATE

Current ledger: data/ssq_history.csv
Current records: {history['record_count']}
Frozen model window ends at issue 2026135.

The current repository does not expose a dedicated SSQ data-import CLI.
Therefore manual/CSV update is an operational procedure, not a claimed built-in feature:
1. Back up the CSV byte-for-byte.
2. Append only verified new official rows using the exact existing header.
3. Never edit or reorder the frozen historical prefix.
4. Run local_history validation and all non-consuming regressions.
5. Confirm duplicate count, illegal-number count and active full-tier blanks are zero.
6. If validation fails, restore the byte-for-byte backup.
7. Do not use random or synthetic rows to fill gaps.
"""
    recovery = """# RECOVERY

If a transaction is staged but not fully published:
1. Do not start a fresh production execution.
2. Use the original Python API v4.local_production_shell.recover_staged_publish.
3. The recovery path must verify staged bytes and use the already staged seed/result.
4. If the staged bytes, ledger chain or target issue state is inconsistent, stop and audit; do not redraw.
5. The desktop reference adapter exposes recover() as a thin wrapper over this API.
"""
    trouble = """# TROUBLESHOOTING

REFUSE_DUPLICATE_ISSUE: target issue already committed/published; do not rerun.
Schedule/year blocked: update only from an official suspension notice; do not guess.
Promotion balance missing while active: V5 may fall back to V4; provide exact local balance only if authoritative.
Hash/ledger mismatch: stop production and preserve all files for audit.
RECOVERY_REQUIRED: call the existing recovery API; never delete pending state to force a redraw.
Windows permission/locking issue: live production remains blocked until ACL and atomic-file semantics are validated.
"""
    for n,c in [("INSTALLATION.md",install),("PRODUCTION_OPERATION.md",prod),("DATA_UPDATE.md",data),("RECOVERY.md",recovery),("TROUBLESHOOTING.md",trouble)]:
        write_text("13_OPERATION_MANUAL/"+n,c)

def write_gap_register(test_results, history, sensitive):
    gaps = [
        ("GAP-001","P1","WINDOWS_NOT_TESTED","Native Windows 10/11 ACL, file locking, multi-process and atomic replace were not executed in this audit.","Desktop integration","No for Linux/reference; yes for live Windows","Run native Windows acceptance before live production.","No"),
        ("GAP-002","P1","Unpinned dependency","Repository has no authoritative pinned requirements/lock file for SSQ; current CI installs numpy without a version pin.","Reproducible deployment","Potentially","Pin only after a separate compatibility validation; handoff requirements intentionally says numpy without invented pin.","Yes, before formal pinning"),
        ("GAP-003","P1","2027 schedule pending","Local manifest marks 2027 official lottery suspension publication pending and Fail-Closed.","Future 2027 production","Yes for 2027 only","Update manifest from official notice, then rerun non-consuming acceptance.","No"),
        ("GAP-004","P2","No dedicated SSQ history-import CLI","Source has read/validation path but no stable dedicated append/import command was found.","Desktop data management","No if handled procedurally","Keep update as controlled append+validation or add a separately reviewed adapter utility later.","Yes if implementing write UI"),
        ("GAP-005","P2","Historical conversation/attachments not fully machine-auditable","This audit can inspect current repo plus supplied task and Git history, not every deleted chat attachment.","Decision archaeology","No current-code impact","Treat repository + frozen artifacts as authority; retain this limitation.","No"),
        ("GAP-006","P2","One-shot OOS external raw CI evidence may exceed repository archive","Repository contains result JSON and lock files; not every historical external runner log is necessarily preserved in main.","Research provenance","No current runtime impact","Keep existing result/lock assets; never rerun one-shot OOS merely to recreate missing logs.","No"),
    ]
    for t in test_results:
        if t["status"]=="FAIL":
            gaps.append((f"GAP-TEST-{t['name']}","P0","Current non-consuming test failed",t["name"],"Production acceptance","Yes","Inspect log and stop handoff READY status.","Yes"))
    if sensitive["secret_pattern_findings"]:
        gaps.append(("GAP-SECRET","P0","Potential secret pattern found",json.dumps(sensitive["secret_pattern_findings"],ensure_ascii=False),"Package security","Yes","Exclude and manually review before distribution.","Yes"))
    lines=["# SSQ_GAP_REGISTER","", "|ID|严重级别|问题|具体内容|影响范围|影响正式生产|建议|需确认|","|---|---|---|---|---|---|---|---|"]
    for g in gaps:
        lines.append("|"+"|".join(str(x).replace("|","/") for x in g)+"|")
    write_text("14_GAP_REGISTER/SSQ_GAP_REGISTER.md","\n".join(lines))
    return gaps

def write_read_first(history, test_results, gaps):
    active=json.loads((SNAP/"v4/ACTIVE_RELEASE.json").read_text(UTF8))
    readme=f"""# SSQ V5 Full Desktop Handoff

This package is a read-only production handoff generated from repository commit {SOURCE_COMMIT}.
The SSQ production cutover code last changed at {SSQ_CODE_COMMIT}; later main commits through SOURCE_COMMIT are DLT-side work and do not alter the SSQ paths verified here.

Active release: {active['active_release']}
Production route: {active['production_route']}
Fallback: {active['fallback_route']}
Policy: {active['phase4_policy_gate']['selected_policy']}
History: {history['record_count']} rows, {history['first_issue']} through {history['latest_issue']}
Full-tier window: {history['full_tier_count']} rows, {history['full_tier_first_issue']} through {history['full_tier_latest_issue']}

Read SSQ_RELEASE_BASELINE.md, then 05_RULES, 06_PRODUCTION_SAFETY, 11_DESKTOP_INTEGRATION and 14_GAP_REGISTER.
Do not merge the handoff builder branch into production merely to use this ZIP.
"""
    write_text("00_READ_FIRST/README.md",readme)

    baseline=f"""# SSQ_RELEASE_BASELINE

- Current repository main HEAD observed before handoff branch: {SOURCE_COMMIT}
- SSQ last production-code/CI cutover commit: {SSQ_CODE_COMMIT}
- Active release: {active['active_release']}
- Base V4 algorithm release: {active['base_algorithm_release']}
- V5 live entrypoint: {active['v5_live_entrypoint']}
- V4 fallback entrypoint: {active['v4_fallback_entrypoint']}
- Production shell: {active['production_shell']}
- Phase 3 Artifact SHA-256: {active['phase3']['artifact_sha256']}
- Phase 4 Policy: {active['phase4_policy_gate']['selected_policy']}
- Phase 4 Integrity Summary SHA-256: {active['phase4_policy_gate']['integrity_summary_sha256']}
- Local history SHA-256: {history['sha256']}
- Latest history issue/date: {history['latest_issue']} / {history['latest_date']}
- Test status in this handoff: {sum(t['status']=='PASS' for t in test_results)} PASS, {sum(t['status']=='FAIL' for t in test_results)} FAIL
- Windows status: WINDOWS_NOT_TESTED
"""
    write_text("00_READ_FIRST/SSQ_RELEASE_BASELINE.md",baseline)
    qs="""# QUICK_START

From 01_ORIGINAL_SOURCE/repo_snapshot:

Linux/macOS shell:
export PYTHONPATH=.:v4_rc2:ssq_v5
python -m unittest v4_rc2.test_v4_regression v4_rc2.test_v4_real_integration -v
python -m unittest ssq_v5.test_v5_policy_patch ssq_v5.test_v5_regression_local -v
python -m unittest ssq_v5.test_v5_production_shell_e2e -v
python -m ssq_v5.cli_produce --tickets 2 --promo-remaining 560000000 --dry-run --now-utc 2026-11-25T00:00:00Z

The last command is a historical dry-run example for the frozen 2026135 ledger state. Do not use its test clock in live production.
For WorkBuddy, call 11_DESKTOP_INTEGRATION/adapter_reference/ssq_adapter.py rather than reimplementing the engine.
"""
    write_text("00_READ_FIRST/QUICK_START.md",qs)

def write_test_report(test_results):
    lines=["# SSQ_FINAL_TEST_REPORT","","本次仅执行可重复、非消耗性测试。真实一次性 Phase 3/Phase 4 OOS 明确标记 PROHIBITED_TO_RERUN。","",
           "|Test|Status|Exit|Count|Command|Log|","|---|---|---:|---:|---|---|"]
    for t in test_results:
        lines.append(f"|{t['name']}|{t['status']}|{t['exit_code']}|{t['test_count'] or ''}|{t['command'].replace('|','/')}|{t['log']}|")
    lines += ["","## Historical acceptance evidence","- V4: 54/54 PASS (repository acceptance evidence).","- V5 frozen model: 15/15 PASS.","- Production-shell boundary E2E: 5/5 PASS.","- P0/P1 at the historical hardening closeout: 0/0. These are historical evidence, not silently substituted for this run.","","## Prohibited","- Phase 3 real OOS: PROHIBITED_TO_RERUN.","- Phase 4 one-shot real policy gate: PROHIBITED_TO_RERUN.","- Live production draw/commit: NOT_APPLICABLE to handoff validation."]
    write_text("09_TEST_EVIDENCE/SSQ_FINAL_TEST_REPORT.md","\n".join(lines))

def write_oos_archive():
    p=PKG/"07_OOS_ARCHIVE"; p.mkdir(parents=True,exist_ok=True)
    for rel in ["results/PHASE3_LOCAL_FIRST_RESULT.json","results/PHASE4_POLICY_GATE_RESULT.json","results/PHASE4_POLICY_GATE_SUMMARY.json","phase4/REAL_POLICY_GATE_TRIGGER_20261124.lock","phase4/V5_PRODUCTION_PATH_SMOKE.lock"]:
        d=p/rel; d.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(SNAP/rel,d)
    write_text("07_OOS_ARCHIVE/README.md","""# OOS archive

These files are copied byte-for-byte from the source commit.
The Phase 4 REAL_POLICY_GATE_TRIGGER lock records the one-shot gate and its historical repair retries.
No OOS job is executed by this handoff workflow.
Missing historical external runner logs, if any, are recorded in the GAP register rather than recreated by rerunning OOS.
""")

def write_legacy(legacy_paths):
    root=PKG/"15_LEGACY_REFERENCE"
    for rel in legacy_paths:
        d=root/rel; d.parent.mkdir(parents=True,exist_ok=True); d.write_bytes(git_bytes(rel))
    write_text("15_LEGACY_REFERENCE/README.md","""# Legacy/reference only

This directory contains historical SSQ evidence-fetch/probe workflows and scripts retained for provenance.
They are not part of the active Local-First production chain.
Do not enable them as a production dependency.
The current production acceptance explicitly checks the active chain for zero HTTP lottery dependency.
""")

def write_audit(active_paths, mapping, history, symbols, sensitive):
    audit={
        "source_repository":"wei670025200-boop/-",
        "source_commit":SOURCE_COMMIT,
        "ssq_code_commit":SSQ_CODE_COMMIT,
        "audited_active_paths":active_paths,
        "source_mapping_count":len(mapping),
        "history":history,
        "symbol_index":symbols,
        "sensitive_scan":sensitive,
        "github_connector_verified":{
            "pr20":"SSQ V5 production shell hardening and final cutover; merged",
            "pr21":"CI cleanup: retire obsolete Local-First smoke; merged to 91563513bc560fb7cbd0e48f8abf3803b77a575b",
            "post_ssq_main_commits":"Current HEAD includes later DLT work; current HEAD commit message explicitly says no SSQ changes."
        }
    }
    json_dump("10_AUDIT/SSQ_AUDIT_EVIDENCE.json",audit)

def write_frozen_manifest():
    assets=[]
    candidates=[
        ("03_FROZEN_ASSETS/v4/ACTIVE_RELEASE.json","release descriptor",True,False),
        ("03_FROZEN_ASSETS/v4_rc2/FREEZE_STATE.json","V4 freeze state",True,False),
        ("03_FROZEN_ASSETS/v4_rc2/SHA256SUMS.txt","V4 frozen code hashes",True,False),
        ("03_FROZEN_ASSETS/results/PHASE3_LOCAL_FIRST_RESULT.json","Phase 3 frozen OOS result",True,False),
        ("03_FROZEN_ASSETS/results/PHASE4_POLICY_GATE_RESULT.json","Phase 4 full policy-gate result",True,False),
        ("03_FROZEN_ASSETS/results/PHASE4_POLICY_GATE_SUMMARY.json","Phase 4 policy summary",True,False),
        ("03_FROZEN_ASSETS/phase4/REAL_POLICY_GATE_TRIGGER_20261124.lock","one-shot real gate lock",True,False),
        ("03_FROZEN_ASSETS/phase4/V5_PRODUCTION_PATH_SMOKE.lock","engineering smoke lock",False,False),
    ]
    for rel,stage,prod,regen in candidates:
        p=PKG/rel
        assets.append({"file_path":rel,"file_name":p.name,"file_size":p.stat().st_size,"sha256":sha256_file(p),"model_version":"SSQ_V5_LOCAL_FIRST_HARDENED_1_0","research_stage":stage,"production_required":prod,"allow_modify":False,"allow_regenerate":regen,"load_method":"Read by original engine/config or audit tooling as applicable","verification":"SHA-256 plus referenced source/test gates"})
    json_dump("SSQ_FROZEN_ASSETS_MANIFEST.json",assets)

def requirements():
    write_text("requirements.txt","""# Current SSQ GitHub acceptance installs the following runtime numeric dependency without a source-pinned version.
# No version pin is invented by this handoff.
numpy
""")

def finalize_manifests(mapping, gaps):
    exclusions=[
        "Live runtime seeds / PRIVATE_SEED_AND_NONCE.json",
        "Live private keys",
        "runtime/pending",
        "runtime/published or runtime/production_output",
        "Live production ledger runtime",
        "GitHub/API tokens and .env credentials",
    ]
    # Remove caches produced by tests.
    for d in list(PKG.rglob("__pycache__")):
        shutil.rmtree(d,ignore_errors=True)
    for f in list(PKG.rglob("*.pyc")):
        f.unlink(missing_ok=True)

    all_files=[p for p in PKG.rglob("*") if p.is_file()]
    manifest={
        "Package Name":PKG_NAME,
        "Package Version":"SSQ_HANDOFF_1.0",
        "Created At":datetime.now(timezone.utc).isoformat(),
        "Source Repository":"wei670025200-boop/-",
        "Source Commit":SOURCE_COMMIT,
        "SSQ Production Cutover Commit":SSQ_CODE_COMMIT,
        "Production Algorithm Version":"SSQ_V5_LOCAL_FIRST_HARDENED_1_0 / ANTI_CROWD_SPARSE_ZONE_V1",
        "Production Model Version":"Phase3 frozen + Phase4 P_R01_005_B06",
        "Frozen Policy":"P_R01_005_B06",
        "History Data Version":"through 2026135",
        "History Data Count":history_report()["record_count"],
        "Total File Count":len(all_files),
        "Original Source File Count":sum(1 for p in all_files if "01_ORIGINAL_SOURCE" in p.parts),
        "Documentation File Count":sum(1 for p in all_files if p.suffix.lower() in {".md",".txt",".csv",".json"} and "01_ORIGINAL_SOURCE" not in p.parts),
        "Test File Count":sum(1 for p in all_files if "08_TESTS" in p.parts),
        "Frozen Asset Count":len(json.loads((PKG/"SSQ_FROZEN_ASSETS_MANIFEST.json").read_text(UTF8))),
        "Excluded Assets":exclusions,
        "Known Issues":[g[0]+": "+g[2] for g in gaps],
        "Files":[],
    }
    # FINAL_PACKAGE_MANIFEST and SHA256SUMS exclude themselves from cyclic hashing.
    excluded_self={"FINAL_PACKAGE_MANIFEST.json","SHA256SUMS.txt"}
    for p in sorted(all_files):
        rel=str(p.relative_to(PKG)).replace("\\","/")
        if rel in excluded_self:
            continue
        manifest["Files"].append({
            "File Path":rel,
            "File Size":p.stat().st_size,
            "SHA-256":sha256_file(p),
            "File Category":rel.split("/",1)[0],
            "Production Required":rel.startswith("01_ORIGINAL_SOURCE/") or rel.startswith("03_FROZEN_ASSETS/"),
            "Verification Status":"VERIFIED_LOCAL_PACKAGE",
        })
    json_dump("FINAL_PACKAGE_MANIFEST.json",manifest)

    lines=[
        "# SHA256SUMS excludes SHA256SUMS.txt and FINAL_PACKAGE_MANIFEST.json to avoid recursive self-hash dependency."
    ]
    for item in manifest["Files"]:
        lines.append(f"{item['SHA-256']}  {item['File Path']}")
    write_text("SHA256SUMS.txt","\n".join(lines))

def verify_package_folder():
    failures=[]
    sums=(PKG/"SHA256SUMS.txt").read_text(UTF8).splitlines()
    for line in sums:
        if not line or line.startswith("#"): continue
        digest,rel=line.split("  ",1)
        p=PKG/rel
        if not p.exists() or sha256_file(p)!=digest:
            failures.append(rel)
    # Source blob equality against SOURCE_COMMIT
    mapping=json.loads((PKG/"SOURCE_FILE_MAPPING.json").read_text(UTF8))
    blob_mismatch=[]
    for x in mapping:
        p=PKG/x["delivery_path"]
        if sha256_file(p)!=x["sha256"]:
            blob_mismatch.append(x["repository_path"])
    return {"sha256sum_failures":failures,"source_copy_mismatches":blob_mismatch,"status":"PASS" if not failures and not blob_mismatch else "FAIL"}

def zip_and_verify():
    zip_path=OUT_ROOT/f"{PKG_NAME}.zip"
    if zip_path.exists(): zip_path.unlink()
    with zipfile.ZipFile(zip_path,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(PKG.rglob("*")):
            if p.is_file():
                z.write(p,Path(PKG_NAME)/p.relative_to(PKG))
    zsha=sha256_file(zip_path)
    with tempfile.TemporaryDirectory(prefix="ssq_final_zip_verify_") as td:
        with zipfile.ZipFile(zip_path,"r") as z:
            bad=z.testzip()
            z.extractall(td)
        root=Path(td)/PKG_NAME
        failures=[]
        for line in (root/"SHA256SUMS.txt").read_text(UTF8).splitlines():
            if not line or line.startswith("#"): continue
            digest,rel=line.split("  ",1)
            if sha256_file(root/rel)!=digest: failures.append(rel)
        v={"zip_testzip_bad_file":bad,"sha256sum_failures":failures,"extracted_file_count":sum(1 for p in root.rglob("*") if p.is_file()),"status":"PASS" if bad is None and not failures else "FAIL"}
    (OUT_ROOT/f"{PKG_NAME}.zip.sha256").write_text(f"{zsha}  {zip_path.name}\n",UTF8)
    (OUT_ROOT/"FINAL_ZIP_VALIDATION.json").write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n",UTF8)
    return zip_path,zsha,v

def main():
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    SNAP.mkdir(parents=True,exist_ok=True)

    active,legacy=source_paths()
    mapping=[write_git_file(rel,SNAP) for rel in active]
    json_dump("SOURCE_FILE_MAPPING.json",mapping)
    symbols=parse_symbols(active)
    history=history_report()
    manifest=json.loads((SNAP/"LOCAL_FIRST_MANIFEST.json").read_text(UTF8))
    history["manifest_sha_match"]=history["sha256"]==manifest.get("history_sha256")
    json_dump("02_HISTORY_DATA/SSQ_HISTORY_AUDIT.json",history)

    write_legacy(legacy)
    copy_category_files(mapping)
    write_oos_archive()
    test_results=run_tests()
    write_test_report(test_results)
    sensitive=sensitive_scan(active,legacy)
    json_dump("10_AUDIT/SENSITIVE_ASSET_SCAN.json",sensitive)
    rule_rows=rules(symbols)
    write_rulebook(rule_rows)
    write_pipeline(symbols)
    write_trace_csv(rule_rows)
    write_safety_spec()
    write_desktop_adapter()
    write_decisions()
    write_ops(history)
    gaps=write_gap_register(test_results,history,sensitive)
    write_read_first(history,test_results,gaps)
    write_audit(active,mapping,history,symbols,sensitive)
    write_frozen_manifest()
    requirements()

    # Baseline report and final handoff report before manifest.
    pass_n=sum(t["status"]=="PASS" for t in test_results)
    fail_n=sum(t["status"]=="FAIL" for t in test_results)
    write_text("10_AUDIT/SSQ_PRODUCTION_BASELINE_REPORT.md",f"""# SSQ production baseline audit

Current main source commit captured: {SOURCE_COMMIT}
SSQ production cutover commit: {SSQ_CODE_COMMIT}
Active release: SSQ_V5_LOCAL_FIRST_HARDENED_1_0
Policy: P_R01_005_B06
Phase3 artifact: 2d8a9c0ce6e32d653311356f20d1329d9c2ed8f6c7c544899b5b6d6ccf0f957a
Phase4 integrity: 41229a251054e53f64b0ae339675675f689678f90bb4331891e22d82624f9b23
History: {history['record_count']} rows through {history['latest_issue']} / {history['latest_date']}
Current non-consuming audit tests: {pass_n} PASS, {fail_n} FAIL.
Windows: WINDOWS_NOT_TESTED.
""")

    write_text("FINAL_HANDOFF_REPORT.md",f"""# FINAL_HANDOFF_REPORT

## A. 正式生产基线
- Current repository main commit captured: {SOURCE_COMMIT}
- SSQ last production cutover commit: {SSQ_CODE_COMMIT}
- Formal model/release: SSQ_V5_LOCAL_FIRST_HARDENED_1_0
- Production algorithm route: ANTI_CROWD_SPARSE_ZONE_V1
- Fallback: UNIFORM_MARGINAL_BALANCED_COVERAGE / SSQ_V4_RC2_1
- Policy: P_R01_005_B06
- Latest history: {history['latest_issue']} / {history['latest_date']}
- History rows: {history['record_count']}

## B. 全量资产
See FINAL_PACKAGE_MANIFEST.json and SOURCE_FILE_MAPPING.json.

## C. 完整性审计
- Source copies are extracted byte-for-byte from SOURCE_COMMIT with git-show and mapped to original paths.
- Frozen artifacts are duplicated into 03_FROZEN_ASSETS and hashed.
- Rules, pipeline, safety, operations, traceability and desktop adapter documents are generated without altering original code.
- Legacy online evidence workflows are isolated under 15_LEGACY_REFERENCE.

## D. 真实测试
Current non-consuming: {pass_n} PASS / {fail_n} FAIL.
One-shot real OOS: PROHIBITED_TO_RERUN.

## E. 敏感资产
Live seeds, private keys, runtime ledgers, pending/published state and tokens are excluded by policy. See 10_AUDIT/SENSITIVE_ASSET_SCAN.json.

## F. 缺失及风险
See 14_GAP_REGISTER/SSQ_GAP_REGISTER.md. Native Windows status is WINDOWS_NOT_TESTED.

## G. 桌面接入结论
READY_WITH_LIMITATIONS if all current tests pass: the engine and adapter are handoff-ready, but native Windows ACL/locking/atomic-replace validation and a pinned dependency lock remain outstanding.
If any current non-consuming test fails, treat status as BLOCKED.

## H. ZIP
ZIP SHA-256 is intentionally emitted outside the ZIP after sealing to avoid a circular self-hash.
""")

    write_frozen_manifest()
    finalize_manifests(mapping,gaps)
    folder_verify=verify_package_folder()
    json_dump("10_AUDIT/PRE_ZIP_PACKAGE_VALIDATION.json",folder_verify)
    # PRE_ZIP file changes hashes; finalize one last time.
    finalize_manifests(mapping,gaps)
    folder_verify=verify_package_folder()
    if folder_verify["status"]!="PASS":
        raise SystemExit("PACKAGE_FOLDER_VERIFY_FAIL: "+json.dumps(folder_verify))
    zip_path,zsha,zip_verify=zip_and_verify()
    status="READY_WITH_LIMITATIONS" if fail_n==0 and not sensitive["secret_pattern_findings"] else "BLOCKED"
    summary={
        "package":zip_path.name,
        "package_dir":PKG_NAME,
        "zip_sha256":zsha,
        "zip_size":zip_path.stat().st_size,
        "zip_validation":zip_verify,
        "handoff_status":status,
        "tests":{"pass":pass_n,"fail":fail_n,"prohibited_to_rerun":2},
        "source_commit":SOURCE_COMMIT,
        "ssq_code_commit":SSQ_CODE_COMMIT,
        "history":history,
    }
    (OUT_ROOT/"HANDOFF_SUMMARY.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",UTF8)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
