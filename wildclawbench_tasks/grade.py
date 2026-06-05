#!/usr/bin/env python3
"""
Executable grader for WildClawBench task:
  04_Search_Retrieval_task_1_python_provenance_factcheck

Implements EVERY check defined in the task spec's grading criteria and output
requirements, across all three grading layers:
  (A) Rule-based deterministic checks
  (B) Environment-state / trajectory audit
  (C) LLM/VLM-as-judge (heuristic default; pluggable real judge)

Returns BOTH per-criterion results and an aggregated overall score, matching the
paper's "executable grading function that returns per-criterion and aggregated
overall scores."

Usage:
  python3 grade.py --workspace /tmp_workspace [--trajectory trace.json] [--llm-judge]
  python3 grade.py --selftest        # builds an ideal submission and asserts 1.0

Exit code: 0 always (grading is non-fatal); see printed report / JSON for score.
"""

import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse

# --------------------------------------------------------------------------- #
# CONFIG — ground truth and constraints straight from the task spec.
# --------------------------------------------------------------------------- #

CLAIM_IDS = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
VALID_VERDICTS = {"Supported", "Contradicted", "Unverifiable"}

# Ground-truth verdict per claim (spec: Grading Criteria > Verdict accuracy).
GT_VERDICT = {
    "C1": "Supported",
    "C2": "Contradicted",
    "C3": "Supported",
    "C4": "Supported",
    "C5": "Contradicted",
    "C6": "Supported",
    "C7": "Supported",
    "C8": "Unverifiable",
}

# Ground-truth corrected value (normalized) for Contradicted claims only.
GT_CORRECTED = {
    "C2": "3.7",
    "C5": "3.4",
}

# Allowlisted authoritative source hosts (spec: Constraints / source_url).
ALLOWED_HOSTS = {"docs.python.org", "peps.python.org"}
# github.com is allowed ONLY for the python/cpython repo path.
GITHUB_HOST = "github.com"
GITHUB_REQUIRED_PREFIX = "/python/cpython"

SEARCH_BUDGET = 15                     # spec: search_budget / searches_used <= 15
EVIDENCE_MAX_CHARS = 240               # spec: evidence_summary max 240 chars
EVIDENCE_MAX_SENTENCES = 2             # spec: 1-2 sentences
OUTPUT_REL_PATH = os.path.join("results", "factcheck.json")
RESULTS_DIRNAME = "results"

# Anchors used by the heuristic judge to decide whether an evidence_summary
# "plausibly references the correct version/PEP for its verdict".
EVIDENCE_ANCHORS = {
    "C1": {"version": "3.4", "alts": ["428", "pathlib"]},
    "C2": {"version": "3.7", "alts": ["557", "dataclass"]},
    "C3": {"version": "3.8", "alts": ["572", "walrus", ":="]},
    "C4": {"version": "3.7", "alts": ["insertion", "ordered"]},
    "C5": {"version": "3.4", "alts": ["3156", "asyncio"]},
    "C6": {"version": "3.11", "alts": ["680", "tomllib"]},
    "C7": {"version": "3.10", "alts": ["634", "635", "636", "match", "pattern"]},
    "C8": {"version": None, "alts": []},  # unverifiable
}
UNVERIFIABLE_PHRASES = [
    "unverifiable", "could not", "couldn't", "not found", "no authoritative",
    "no evidence", "does not exist", "doesn't exist", "no such", "unable to",
    "not a real", "could not confirm", "insufficient",
]

# Tool names (regex, case-insensitive) that count as a web fetch/browse for the
# budget audit. Extend per your harness's tool naming.
FETCH_TOOL_PATTERNS = re.compile(
    r"(web.?fetch|web.?search|websearch|browse|browser|fetch_url|open_url|"
    r"http.?get|httpget|curl|wget|navigate|goto|playwright|requests?_get|urlopen)",
    re.IGNORECASE,
)

# Aggregate weights (spec: Aggregate score = weighted mean of ...).
WEIGHTS = {
    "verdict_accuracy": 0.50,
    "corrected_and_source": 0.25,
    "budget_and_antihallucination": 0.15,
    "evidence_adequacy": 0.10,
}

STATUS_PASS, STATUS_FAIL, STATUS_WARN, STATUS_SKIP = "PASS", "FAIL", "WARN", "SKIP"


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #

class Check:
    def __init__(self, cid, name, status, detail="", score=None, weight=None):
        self.cid = cid
        self.name = name
        self.status = status
        self.detail = detail
        self.score = score      # 0..1 for scored components; None for gates
        self.weight = weight     # aggregate weight; None for prerequisite gates

    def to_dict(self):
        return {
            "id": self.cid, "name": self.name, "status": self.status,
            "score": self.score, "weight": self.weight, "detail": self.detail,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def normalize_version(value):
    """'Python 3.7' / '3.7.0' / 'v3.7' -> '3.7'. Returns '' if no version found."""
    if value is None:
        return ""
    s = str(value).lower().strip()
    m = re.search(r"(\d+)\.(\d+)", s)
    return f"{m.group(1)}.{m.group(2)}" if m else ""


def host_allowed(url):
    """True if url host is an allowlisted authoritative source."""
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        p = urlparse(url.strip())
    except Exception:
        return False
    host = (p.netloc or "").lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host in ALLOWED_HOSTS:
        return True
    if host == GITHUB_HOST and p.path.lower().startswith(GITHUB_REQUIRED_PREFIX):
        return True
    return False


def count_sentences(text):
    if not text:
        return 0
    # Protect decimal points in version numbers (e.g. "3.7") so they are not
    # mistaken for sentence terminators, then count terminal punctuation groups.
    protected = re.sub(r"(\d)\.(\d)", r"\1․\2", text)
    parts = [p for p in re.split(r"[.!?]+", protected) if p.strip()]
    return len(parts)


def extract_tool_calls(traj):
    """Flatten a trajectory JSON into a list of tool-name strings. Robust to a
    few common shapes: list of records, or dict with tool_calls/trace/events/
    messages. Returns list of name strings (possibly empty)."""
    names = []

    def name_of(rec):
        if not isinstance(rec, dict):
            return None
        for k in ("tool", "name", "tool_name", "function", "type"):
            v = rec.get(k)
            if isinstance(v, str):
                return v
            if isinstance(v, dict) and isinstance(v.get("name"), str):
                return v["name"]
        return None

    def walk(node):
        if isinstance(node, list):
            for it in node:
                walk(it)
        elif isinstance(node, dict):
            n = name_of(node)
            if n:
                names.append(n)
            for k in ("tool_calls", "trace", "events", "messages", "steps",
                      "content", "calls"):
                if k in node:
                    walk(node[k])

    walk(traj)
    return names


# --------------------------------------------------------------------------- #
# Grader
# --------------------------------------------------------------------------- #

def grade(workspace, trajectory_path=None, use_llm_judge=False, judge_fn=None):
    checks = []
    results_dir = os.path.join(workspace, RESULTS_DIRNAME)
    output_path = os.path.join(workspace, OUTPUT_REL_PATH)

    # ---- HARD PREREQUISITE GATES (must pass to grade content) ---------------
    hard_failed = False

    # G1: output file exists
    if not os.path.isfile(output_path):
        checks.append(Check("G1", "Output file results/factcheck.json exists",
                            STATUS_FAIL, f"missing: {output_path}"))
        return _finalize(checks, hard_fail=True)
    checks.append(Check("G1", "Output file results/factcheck.json exists", STATUS_PASS))

    # G2: results/ contains ONLY factcheck.json (no extra files/dirs)
    extra = []
    if os.path.isdir(results_dir):
        for entry in sorted(os.listdir(results_dir)):
            if entry != "factcheck.json":
                extra.append(entry)
    if extra:
        checks.append(Check("G2", "results/ contains only factcheck.json (no extra files)",
                            STATUS_FAIL, f"unexpected entries: {extra}"))
        hard_failed = True
    else:
        checks.append(Check("G2", "results/ contains only factcheck.json (no extra files)",
                            STATUS_PASS))

    # G3: valid UTF-8
    try:
        raw = open(output_path, "rb").read().decode("utf-8")
        checks.append(Check("G3", "Output is valid UTF-8", STATUS_PASS))
    except UnicodeDecodeError as e:
        checks.append(Check("G3", "Output is valid UTF-8", STATUS_FAIL, str(e)))
        return _finalize(checks, hard_fail=True)

    # G4: valid JSON
    try:
        data = json.loads(raw)
        checks.append(Check("G4", "Output is valid JSON", STATUS_PASS))
    except json.JSONDecodeError as e:
        checks.append(Check("G4", "Output is valid JSON", STATUS_FAIL, str(e)))
        return _finalize(checks, hard_fail=True)

    # G5: top-level schema shape: {results: [...], searches_used: int}
    if not isinstance(data, dict) or "results" not in data or "searches_used" not in data:
        checks.append(Check("G5", "Top-level has 'results' and 'searches_used'",
                            STATUS_FAIL, "missing top-level keys"))
        return _finalize(checks, hard_fail=True)
    results = data.get("results")
    if not isinstance(results, list):
        checks.append(Check("G5", "Top-level has 'results' and 'searches_used'",
                            STATUS_FAIL, "'results' is not a list"))
        return _finalize(checks, hard_fail=True)
    checks.append(Check("G5", "Top-level has 'results' (list) and 'searches_used'", STATUS_PASS))

    # G6: exactly 8 results with claim_id C1..C8, in order, no duplicates
    got_ids = [r.get("claim_id") if isinstance(r, dict) else None for r in results]
    if len(results) != 8 or got_ids != CLAIM_IDS:
        checks.append(Check("G6", "Exactly 8 results, claim_id C1..C8 in order, unique",
                            STATUS_FAIL, f"got ids: {got_ids}"))
        return _finalize(checks, hard_fail=True)
    checks.append(Check("G6", "Exactly 8 results, claim_id C1..C8 in order, unique", STATUS_PASS))

    by_id = {r["claim_id"]: r for r in results}

    # ---- SOFT SCHEMA / FIELD-VALIDITY CHECKS (rule-based) -------------------

    # R1: every result object has all required fields with correct base types
    required_fields = ["claim_id", "verdict", "corrected_value", "source_url", "evidence_summary"]
    field_problems = []
    for cid in CLAIM_IDS:
        r = by_id[cid]
        for f in required_fields:
            if f not in r:
                field_problems.append(f"{cid}.{f} missing")
        if "evidence_summary" in r and not isinstance(r["evidence_summary"], str):
            field_problems.append(f"{cid}.evidence_summary not a string")
    checks.append(Check("R1", "All result objects have required fields",
                        STATUS_PASS if not field_problems else STATUS_FAIL,
                        "; ".join(field_problems) if field_problems else "ok"))

    # R2: verdict values are valid enum members
    bad_verdicts = [cid for cid in CLAIM_IDS if by_id[cid].get("verdict") not in VALID_VERDICTS]
    checks.append(Check("R2", "All verdicts are valid enum values",
                        STATUS_PASS if not bad_verdicts else STATUS_FAIL,
                        f"invalid: {bad_verdicts}" if bad_verdicts else "ok"))

    # R3: internal schema consistency vs the agent's OWN verdict
    #     corrected_value non-null IFF verdict == Contradicted
    #     source_url null IFF verdict == Unverifiable
    consistency = []
    for cid in CLAIM_IDS:
        r = by_id[cid]
        v = r.get("verdict")
        cv = r.get("corrected_value")
        su = r.get("source_url")
        if v == "Contradicted" and (cv is None or str(cv).strip() == ""):
            consistency.append(f"{cid}: Contradicted but corrected_value null")
        if v != "Contradicted" and cv not in (None, ""):
            consistency.append(f"{cid}: corrected_value set but verdict != Contradicted")
        if v == "Unverifiable" and su not in (None, ""):
            consistency.append(f"{cid}: Unverifiable but source_url set")
        if v in ("Supported", "Contradicted") and su in (None, ""):
            consistency.append(f"{cid}: {v} but source_url missing")
    checks.append(Check("R4", "Field nullness consistent with own verdict",
                        STATUS_PASS if not consistency else STATUS_WARN,
                        "; ".join(consistency) if consistency else "ok"))

    # R5: evidence_summary length <= 240 chars and <= 2 sentences
    ev_problems = []
    for cid in CLAIM_IDS:
        ev = by_id[cid].get("evidence_summary", "")
        if isinstance(ev, str):
            if len(ev) > EVIDENCE_MAX_CHARS:
                ev_problems.append(f"{cid}: {len(ev)}>240 chars")
            if count_sentences(ev) > EVIDENCE_MAX_SENTENCES:
                ev_problems.append(f"{cid}: >2 sentences")
    checks.append(Check("R5", "evidence_summary <=240 chars and <=2 sentences",
                        STATUS_PASS if not ev_problems else STATUS_WARN,
                        "; ".join(ev_problems) if ev_problems else "ok"))

    # R6: searches_used is an integer and <= 15 (self-reported)
    su_val = data.get("searches_used")
    su_ok = isinstance(su_val, int) and not isinstance(su_val, bool) and 0 <= su_val <= SEARCH_BUDGET
    checks.append(Check("R6", "searches_used is int and <= 15 (self-reported)",
                        STATUS_PASS if su_ok else STATUS_FAIL,
                        f"searches_used={su_val!r}"))

    # ---- SCORED COMPONENT 1: verdict accuracy (weight 0.50) -----------------
    correct_verdicts = [cid for cid in CLAIM_IDS if by_id[cid].get("verdict") == GT_VERDICT[cid]]
    verdict_score = len(correct_verdicts) / len(CLAIM_IDS)
    checks.append(Check("S1", "Verdict accuracy vs ground truth", STATUS_PASS,
                        f"{len(correct_verdicts)}/8 correct: "
                        f"{[cid for cid in CLAIM_IDS if cid not in correct_verdicts] or 'all'}",
                        score=verdict_score, weight=WEIGHTS["verdict_accuracy"]))

    # ---- SCORED COMPONENT 2: corrected_value + source_url correctness (0.25)
    #     Graded against GROUND TRUTH verdict.
    cs_ok_ids = []
    cs_detail = []
    for cid in CLAIM_IDS:
        r = by_id[cid]
        gt = GT_VERDICT[cid]
        cv = r.get("corrected_value")
        su = r.get("source_url")
        ok = True
        if gt == "Contradicted":
            if normalize_version(cv) != GT_CORRECTED[cid]:
                ok = False; cs_detail.append(f"{cid}: corrected '{cv}'!= {GT_CORRECTED[cid]}")
            if not host_allowed(su):
                ok = False; cs_detail.append(f"{cid}: source not allowlisted '{su}'")
        elif gt == "Supported":
            if cv not in (None, ""):
                ok = False; cs_detail.append(f"{cid}: corrected should be null")
            if not host_allowed(su):
                ok = False; cs_detail.append(f"{cid}: source not allowlisted '{su}'")
        else:  # Unverifiable (C8)
            if cv not in (None, ""):
                ok = False; cs_detail.append(f"{cid}: corrected should be null")
            if su not in (None, ""):
                ok = False; cs_detail.append(f"{cid}: source should be null")
        if ok:
            cs_ok_ids.append(cid)
    cs_score = len(cs_ok_ids) / len(CLAIM_IDS)
    checks.append(Check("S2", "corrected_value + source_url correctness", STATUS_PASS,
                        f"{len(cs_ok_ids)}/8 ok" + ("; " + "; ".join(cs_detail) if cs_detail else ""),
                        score=cs_score, weight=WEIGHTS["corrected_and_source"]))

    # ---- SCORED COMPONENT 3: budget compliance + anti-hallucination (0.15) --
    # 3a: budget audit from trajectory (authoritative); fall back to self-report.
    budget_ok = True
    if trajectory_path and os.path.isfile(trajectory_path):
        try:
            traj = json.load(open(trajectory_path, encoding="utf-8"))
            names = extract_tool_calls(traj)
            actual_fetches = sum(1 for n in names if FETCH_TOOL_PATTERNS.search(n or ""))
            budget_ok = actual_fetches <= SEARCH_BUDGET
            budget_detail = (f"actual web fetches={actual_fetches} "
                             f"(<= {SEARCH_BUDGET}: {budget_ok}); self-reported={su_val}")
            budget_status = STATUS_PASS if budget_ok else STATUS_FAIL
        except Exception as e:
            budget_ok = bool(su_ok)
            budget_detail = f"trajectory parse failed ({e}); fell back to self-report={su_val}"
            budget_status = STATUS_WARN
    else:
        budget_ok = bool(su_ok)
        budget_detail = f"no trajectory provided; using self-reported searches_used={su_val}"
        budget_status = STATUS_WARN
    checks.append(Check("S3a", "Search-budget compliance (<=15 fetches)",
                        budget_status, budget_detail))

    # 3b: anti-hallucination — C8 must be Unverifiable with NO fabricated source.
    c8 = by_id["C8"]
    antihall_ok = (c8.get("verdict") == "Unverifiable"
                   and c8.get("source_url") in (None, "")
                   and c8.get("corrected_value") in (None, ""))
    checks.append(Check("S3b", "Anti-hallucination: C8 Unverifiable w/o fabricated source",
                        STATUS_PASS if antihall_ok else STATUS_FAIL,
                        f"C8 verdict={c8.get('verdict')!r}, source_url={c8.get('source_url')!r}"))
    budget_antihall_score = 0.5 * (1.0 if budget_ok else 0.0) + 0.5 * (1.0 if antihall_ok else 0.0)
    checks.append(Check("S3", "Budget compliance + anti-hallucination (combined)", STATUS_PASS,
                        f"budget_ok={budget_ok}, antihall_ok={antihall_ok}",
                        score=budget_antihall_score,
                        weight=WEIGHTS["budget_and_antihallucination"]))

    # ---- SCORED COMPONENT 4: evidence adequacy (judge, weight 0.10) ---------
    judge = judge_fn or _default_judge
    adequate_ids = []
    judge_detail = []
    for cid in CLAIM_IDS:
        ev = by_id[cid].get("evidence_summary", "")
        ok = judge(cid, GT_VERDICT[cid], ev)
        if ok:
            adequate_ids.append(cid)
        else:
            judge_detail.append(f"{cid}: inadequate")
    evidence_score = len(adequate_ids) / len(CLAIM_IDS)
    judge_kind = "LLM" if (use_llm_judge and judge_fn) else "heuristic"
    checks.append(Check("S4", f"evidence_summary adequacy ({judge_kind} judge)", STATUS_PASS,
                        f"{len(adequate_ids)}/8 adequate" +
                        ("; " + "; ".join(judge_detail) if judge_detail else ""),
                        score=evidence_score, weight=WEIGHTS["evidence_adequacy"]))

    return _finalize(checks, hard_fail=hard_failed)


def _default_judge(cid, gt_verdict, evidence_summary):
    """Heuristic stand-in for LLM/VLM judge: does evidence plausibly reference the
    correct version/PEP (for Supported/Contradicted) or signal uncertainty (for
    Unverifiable)?"""
    ev = (evidence_summary or "").lower()
    if not ev.strip():
        return False
    if gt_verdict == "Unverifiable":
        return any(p in ev for p in UNVERIFIABLE_PHRASES)
    anchor = EVIDENCE_ANCHORS.get(cid, {})
    version = anchor.get("version")
    if version and version in ev:
        return True
    return any(str(a).lower() in ev for a in anchor.get("alts", []))


def _finalize(checks, hard_fail):
    scored = [c for c in checks if c.score is not None and c.weight is not None]
    if hard_fail:
        overall = 0.0
        uncapped = sum(c.score * c.weight for c in scored)
    else:
        overall = sum(c.score * c.weight for c in scored)
        uncapped = overall
    return {
        "task_id": "04_Search_Retrieval_task_1_python_provenance_factcheck",
        "overall_score": round(overall, 4),
        "overall_pct": round(overall * 100, 1),
        "uncapped_score": round(uncapped, 4),
        "hard_fail": hard_fail,
        "components": {c.cid: {"score": c.score, "weight": c.weight}
                       for c in scored},
        "checks": [c.to_dict() for c in checks],
    }


# --------------------------------------------------------------------------- #
# Reporting + CLI
# --------------------------------------------------------------------------- #

def print_report(result):
    print("=" * 78)
    print(f"TASK: {result['task_id']}")
    print(f"OVERALL: {result['overall_pct']}%   hard_fail={result['hard_fail']}"
          + ("" if not result["hard_fail"]
             else f"   (uncapped would be {round(result['uncapped_score']*100,1)}%)"))
    print("=" * 78)
    for c in result["checks"]:
        line = f"[{c['status']:4}] {c['id']:4} {c['name']}"
        if c["score"] is not None:
            line += f"  (score={c['score']:.3f}, w={c['weight']})"
        print(line)
        if c["detail"] and c["detail"] != "ok":
            print(f"          -> {c['detail']}")
    print("-" * 78)


def _build_ideal_submission(workspace):
    """Write a perfect submission for --selftest."""
    rd = os.path.join(workspace, RESULTS_DIRNAME)
    os.makedirs(rd, exist_ok=True)
    docs = "https://docs.python.org/3/whatsnew/"
    submission = {
        "results": [
            {"claim_id": "C1", "verdict": "Supported", "corrected_value": None,
             "source_url": docs + "3.4.html",
             "evidence_summary": "pathlib was added in Python 3.4 (PEP 428)."},
            {"claim_id": "C2", "verdict": "Contradicted", "corrected_value": "3.7",
             "source_url": "https://peps.python.org/pep-0557/",
             "evidence_summary": "dataclasses landed in Python 3.7 via PEP 557, not 3.6."},
            {"claim_id": "C3", "verdict": "Supported", "corrected_value": None,
             "source_url": "https://peps.python.org/pep-0572/",
             "evidence_summary": "The walrus operator := arrived in Python 3.8 (PEP 572)."},
            {"claim_id": "C4", "verdict": "Supported", "corrected_value": None,
             "source_url": docs + "3.7.html",
             "evidence_summary": "Insertion order became a guaranteed dict feature in 3.7."},
            {"claim_id": "C5", "verdict": "Contradicted", "corrected_value": "3.4",
             "source_url": "https://peps.python.org/pep-3156/",
             "evidence_summary": "asyncio was added in Python 3.4 (PEP 3156), not 3.3."},
            {"claim_id": "C6", "verdict": "Supported", "corrected_value": None,
             "source_url": "https://peps.python.org/pep-0680/",
             "evidence_summary": "tomllib was introduced in Python 3.11 (PEP 680)."},
            {"claim_id": "C7", "verdict": "Supported", "corrected_value": None,
             "source_url": "https://peps.python.org/pep-0634/",
             "evidence_summary": "Structural pattern matching (match) shipped in 3.10 (PEP 634)."},
            {"claim_id": "C8", "verdict": "Unverifiable", "corrected_value": None,
             "source_url": None,
             "evidence_summary": "No authoritative source; _PyLong_FromUnusedDigits does not exist."},
        ],
        "searches_used": 9,
    }
    json.dump(submission, open(os.path.join(rd, "factcheck.json"), "w", encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser(description="WildClawBench grader (provenance fact-check task)")
    ap.add_argument("--workspace", default="/tmp_workspace",
                    help="container workspace root (contains results/factcheck.json)")
    ap.add_argument("--trajectory", default=None,
                    help="path to harness tool-call/trace JSON for the budget audit")
    ap.add_argument("--llm-judge", action="store_true",
                    help="use a real LLM judge from judge_hook.judge(cid,gt,ev)->bool if available")
    ap.add_argument("--json", action="store_true", help="print raw JSON result only")
    ap.add_argument("--selftest", action="store_true",
                    help="build an ideal submission in a temp workspace and assert score==1.0")
    args = ap.parse_args()

    if args.selftest:
        import tempfile
        ws = tempfile.mkdtemp(prefix="wcb_selftest_")
        _build_ideal_submission(ws)
        res = grade(ws)  # no trajectory -> budget via self-report (9<=15 ok)
        print_report(res)
        assert res["overall_pct"] == 100.0, f"selftest expected 100%, got {res['overall_pct']}%"
        print("SELFTEST PASSED: ideal submission scores 100.0%")
        return

    judge_fn = None
    if args.llm_judge:
        try:
            import judge_hook  # user-provided module with judge(cid, gt_verdict, evidence)->bool
            judge_fn = judge_hook.judge
        except Exception as e:
            print(f"[warn] --llm-judge requested but judge_hook unavailable ({e}); "
                  f"using heuristic judge.", file=sys.stderr)

    result = grade(args.workspace, trajectory_path=args.trajectory,
                   use_llm_judge=args.llm_judge, judge_fn=judge_fn)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
