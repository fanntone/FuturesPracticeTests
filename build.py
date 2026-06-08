# -*- coding: utf-8 -*-
"""
build.py — 把考古題 PDF 解析成結構化題庫，並內嵌進 index.html。

用法：
    python build.py            # 解析所有 PDF、驗證、產生 index.html
    python build.py --check    # 只解析＋驗證＋印樣本，不產生 HTML

命名規則：題目檔 xxxxx.pdf（例 11401），答案檔 xxxxxa.pdf。
"""
import re
import sys
import glob
import json
import subprocess
from pathlib import Path

# 主控台預設 cp950，改成 UTF-8 才能印中文與 ✓/✗
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
HTML_OUT = ROOT / "index.html"
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


def pdftext(path: Path) -> str:
    # -layout 保留物理排版：短選項並排在同一行時順序才不會被打亂
    r = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", "-layout", str(path), "-"],
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


def parse_one(num: int, chunk: str) -> dict:
    # 選項可能兩欄排列 (A)(C)/(B)(D)，順序不一定 A<B<C<D；
    # 因此抓出四個標記的「位置」，依位置排序後，每個選項取到下一個標記為止。
    pos = {}
    for L in "ABCD":
        i = chunk.find(f"({L})")
        if i < 0:
            raise ValueError(f"第 {num} 題找不到選項 ({L})")
        pos[L] = i
    order = sorted("ABCD", key=lambda L: pos[L])
    stem = clean(chunk[: pos[order[0]]])
    opts = {}
    for idx, L in enumerate(order):
        start = pos[L] + 3
        end = pos[order[idx + 1]] if idx + 1 < len(order) else len(chunk)
        opts[L] = clean(chunk[start:end])
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


# ---------- 單一考試組裝＋驗證 ----------
def build_exam(code: str):
    qpdf = ROOT / f"{code}.pdf"
    apdf = ROOT / f"{code}a.pdf"
    if not apdf.exists():
        raise FileNotFoundError(f"缺少答案檔 {apdf.name}")

    year, session = int(code[:3]), int(code[3:])
    label = f"{year}年第{session}次"

    subjects = dict(split_subjects(pdftext(qpdf)))
    answers = parse_answers(pdftext(apdf))

    exam = {"code": code, "label": label, "subjects": {}}
    report = []
    for subj in ("法規", "理實"):
        if subj not in subjects:
            raise ValueError(f"{code}: 題目檔缺少科目「{subj}」")
        if subj not in answers:
            raise ValueError(f"{code}: 答案檔缺少科目「{subj}」")
        qs = parse_questions(subjects[subj])
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
