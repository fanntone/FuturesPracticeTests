# -*- coding: utf-8 -*-
"""
build.py — 把考古題 PDF 解析成結構化題庫，並內嵌進 index.html。

用法：
    python build.py            # 解析所有 PDF、驗證、產生 index.html
    python build.py --check    # 只解析＋驗證＋印樣本，不產生 HTML

命名規則：題目檔 xxxxx.pdf（例 11401），答案檔 xxxxxa.pdf。
"""
import os
import re
import sys
import glob
import json
import shutil
import subprocess
from pathlib import Path

# 主控台預設 cp950，改成 UTF-8 才能印中文與 ✓/✗
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
HTML_OUT = ROOT / "index.html"


def find_pdftotext() -> str:
    """在 PATH 找 pdftotext；找不到就試 Git for Windows 內建的 mingw64 版本。

    直接雙擊 .bat 或從 cmd/PowerShell 執行時，PATH 只含 Git\\cmd，
    不含 Git\\mingw64\\bin，即使 Git Bash 裡用得到也會找不到指令。
    """
    found = shutil.which("pdftotext")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        candidate = Path(base) / "Git" / "mingw64" / "bin" / "pdftotext.exe"
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(
        "找不到 pdftotext（需要 Poppler）。請安裝 poppler 並加入 PATH，"
        "或確認已安裝 Git for Windows（內附 pdftotext.exe）。"
    )


PDFTOTEXT = find_pdftotext()
TEMPLATE = ROOT / "template.html"

SUBJECT_CANON = {
    "期貨交易法規": "法規",
    "期貨交易理論與實務": "理實",
}

# 題目檔裡要清掉的雜訊行（頁首、應試說明等）
NOISE = [
    re.compile(r"期貨商業務員資格測驗試題"),
    re.compile(r"^\s*請填應試號碼"),
    re.compile(r"考生請在"),
    re.compile(r"^\s*為單一選擇題"),
    re.compile(r"^\s*※"),
]


def pdftext(path: Path, mode: str = "layout") -> str:
    # 不同年份排版不同：layout 適合單欄、raw 適合雙欄、default 為保底
    flags = {"layout": ["-layout"], "raw": ["-raw"], "default": []}[mode]
    r = subprocess.run(
        [PDFTOTEXT, "-enc", "UTF-8", *flags, str(path), "-"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext 失敗 {path.name}: {r.stderr.strip()}")
    return r.stdout


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\n", " ")).strip()


# ---------- 題目檔解析 ----------
def split_subjects(text: str):
    marks = list(re.finditer(r"專業科目[：:]\s*(期貨交易法規|期貨交易理論與實務)", text))
    if len(marks) != 2:
        raise ValueError(f"預期 2 個科目標題，實際找到 {len(marks)} 個")
    out = []
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((SUBJECT_CANON[m.group(1)], text[start:end]))
    return out


def remove_noise(block: str) -> str:
    return "\n".join(
        ln for ln in block.split("\n") if not any(p.search(ln) for p in NOISE)
    )


def find_marker(chunk: str, L: str):
    """回傳 (邊界start, 內容start)。邊界start 含左括號（給上一選項當結尾用）；找不到回傳 (-1,-1)。"""
    i = chunk.find(f"({L})")          # 正常標記 (X)
    if i >= 0:
        return i, i + 3
    m = re.search(r"(?:^|\n)\s*" + L + r"\)", chunk)  # 容錯：來源漏左括號、行首的「X)」
    if m:
        return m.end() - 2, m.end()
    return -1, -1


def parse_one(num: int, chunk: str) -> dict:
    # 選項可能兩欄排列 (A)(C)/(B)(D)，順序不一定 A<B<C<D；
    # 因此抓出四個標記的「位置」，依位置排序後，每個選項取到下一個標記為止。
    start, after = {}, {}
    for L in "ABCD":
        s, e = find_marker(chunk, L)
        if s < 0:
            raise ValueError(f"第 {num} 題找不到選項 ({L})")
        start[L], after[L] = s, e
    order = sorted("ABCD", key=lambda L: start[L])
    stem = clean(chunk[: start[order[0]]])
    opts = {}
    for idx, L in enumerate(order):
        end = start[order[idx + 1]] if idx + 1 < len(order) else len(chunk)
        opts[L] = clean(chunk[after[L]:end])
    return {"no": num, "stem": stem, "options": opts}


def parse_questions(block: str) -> list:
    block = remove_noise(block)
    markers = [
        (m.start(), int(m.group(1)), m.end())
        for m in re.finditer(r"(?m)^\s*(\d{1,2})\.", block)
    ]
    # 只保留遞增序號 1,2,3...（避開題幹中偶然出現的「數字.」）
    kept, expected = [], 1
    for pos, num, endpos in markers:
        if num == expected:
            kept.append((pos, num, endpos))
            expected += 1
    questions = []
    for i, (pos, num, endpos) in enumerate(kept):
        end = kept[i + 1][0] if i + 1 < len(kept) else len(block)
        questions.append(parse_one(num, block[endpos:end]))
    return questions


# ---------- 答案檔解析 ----------
def parse_answers(text: str) -> dict:
    marks = list(re.finditer(r"(期貨交易法規|期貨交易理論與實務)試題解答", text))
    if len(marks) != 2:
        raise ValueError(f"答案檔預期 2 個科目，實際找到 {len(marks)} 個")
    result = {}
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        subj = SUBJECT_CANON[m.group(1)]
        ans, pending = {}, None
        for tk in text[start:end].split():
            if re.fullmatch(r"\d{1,2}", tk):
                pending = int(tk)
            elif re.fullmatch(r"[ABCD]", tk):
                if pending is None:
                    raise ValueError(f"{subj} 答案出現無對應題號的選項 {tk}")
                ans[pending] = tk
                pending = None
        result[subj] = ans
    return result


# 不同年份排版不一（單欄／雙欄），逐一嘗試抽取模式，取第一個能完整解析 50×2 題的
EXTRACT_MODES = ["layout", "raw", "default"]


def robust_questions(qpdf: Path):
    errors = []
    for mode in EXTRACT_MODES:
        try:
            subs = dict(split_subjects(pdftext(qpdf, mode)))
            out = {}
            for subj in ("法規", "理實"):
                if subj not in subs:
                    raise ValueError(f"缺少科目「{subj}」")
                qs = parse_questions(subs[subj])
                if len(qs) != 50:
                    raise ValueError(f"{subj} 題數 {len(qs)}≠50")
                for q in qs:
                    if any(not q["options"][k] for k in "ABCD"):
                        raise ValueError(f"{subj} 第{q['no']}題有空選項")
                out[subj] = qs
            return out, mode
        except Exception as e:
            errors.append(f"{mode}={e}")
    raise ValueError("題目檔各排版模式皆無法解析（" + " ; ".join(errors) + "）")


def robust_answers(apdf: Path):
    errors = []
    for mode in EXTRACT_MODES:
        try:
            ans = parse_answers(pdftext(apdf, mode))
            for subj in ("法規", "理實"):
                if set(ans.get(subj, {})) != set(range(1, 51)):
                    raise ValueError(f"{subj} 答案題號不齊")
            return ans, mode
        except Exception as e:
            errors.append(f"{mode}={e}")
    raise ValueError("答案檔各排版模式皆無法解析（" + " ; ".join(errors) + "）")


# ---------- 單一考試組裝＋驗證 ----------
def build_exam(code: str):
    qpdf = ROOT / f"{code}.pdf"
    apdf = ROOT / f"{code}a.pdf"
    if not apdf.exists():
        raise FileNotFoundError(f"缺少答案檔 {apdf.name}")

    year, session = int(code[:3]), int(code[3:])
    label = f"{year}年第{session}次"

    subjects, qmode = robust_questions(qpdf)
    answers, amode = robust_answers(apdf)

    exam = {"code": code, "label": label, "qmode": qmode, "amode": amode, "subjects": {}}
    report = []
    for subj in ("法規", "理實"):
        qs = subjects[subj]
        ans = answers[subj]

        # 嚴格驗證
        errs = []
        if len(qs) != 50:
            errs.append(f"題數 {len(qs)}≠50")
        if set(ans) != set(range(1, 51)):
            errs.append(f"答案題號不齊 {sorted(set(range(1,51))-set(ans))}")
        for q in qs:
            if any(not q["options"][k] for k in "ABCD"):
                errs.append(f"第{q['no']}題有空選項")
            a = ans.get(q["no"])
            if a not in ("A", "B", "C", "D"):
                errs.append(f"第{q['no']}題答案異常({a})")
            q["answer"] = a
            q["id"] = f"{code}-{subj}-{q['no']}"
            q["year"] = code
            q["subject"] = subj

        report.append((subj, len(qs), errs))
        if errs:
            raise ValueError(f"{code} {subj} 驗證失敗：{'; '.join(errs)}")
        exam["subjects"][subj] = qs
    return exam, report


def discover_codes():
    codes = []
    for p in sorted(ROOT.glob("*.pdf")):
        m = re.fullmatch(r"(\d{5})", p.stem)
        if m:
            codes.append(m.group(1))
    return codes


def main():
    check_only = "--check" in sys.argv
    codes = discover_codes()
    if not codes:
        print("找不到任何題目 PDF（檔名應為 5 碼數字，例 11401.pdf）")
        sys.exit(1)

    exams, total = [], 0
    print("=" * 56)
    for code in codes:
        try:
            exam, report = build_exam(code)
        except Exception as e:
            print(f"✗ {code}  解析失敗：{e}")
            sys.exit(1)
        for subj, n, _ in report:
            name = "期貨交易法規" if subj == "法規" else "期貨交易理論與實務"
            print(f"  {code}  {name:<9}  {n} 題  ✓")
            total += n
        if (exam["qmode"], exam["amode"]) != ("layout", "layout"):
            print(f"        （{code} 排版：題目={exam['qmode']}、答案={exam['amode']}）")
        exams.append(exam)
    print("=" * 56)
    print(f"題庫解析完成：{len(exams)} 次考試 / {total} 題")

    # 印樣本供人工核對
    s = exams[0]["subjects"]["法規"][0]
    print("\n— 樣本（第一份．法規．第1題）—")
    print(f"  題目：{s['stem']}")
    for k in "ABCD":
        mark = "  ← 正解" if k == s["answer"] else ""
        print(f"   ({k}) {s['options'][k]}{mark}")

    bank = {
        "exams": [
            {
                "code": e["code"],
                "label": e["label"],
                "subjects": e["subjects"],
            }
            for e in exams
        ]
    }

    if check_only:
        (ROOT / "題庫.json").write_text(
            json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\n--check：已輸出 題庫.json 供檢視（未產生 HTML）")
        return

    if not TEMPLATE.exists():
        print("\n（尚無 template.html，先輸出 題庫.json）")
        (ROOT / "題庫.json").write_text(
            json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return

    html = TEMPLATE.read_text(encoding="utf-8")
    data_js = "window.QUESTION_BANK = " + json.dumps(bank, ensure_ascii=False) + ";"
    html = html.replace("/*__QUESTION_BANK__*/", data_js)
    HTML_OUT.write_text(html, encoding="utf-8")
    print(f"\n已寫入 {HTML_OUT.name}")


if __name__ == "__main__":
    main()
