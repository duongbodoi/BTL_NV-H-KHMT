"""Công cụ Phase 12a cho nhóm: xem ứng viên, kiểm ca + nhãn, kappa, đóng băng (spec mục 15).

Chạy:
  python scripts/eval_tools.py candidates        # liệt kê bất thường để NHÓM chọn ca
  python scripts/eval_tools.py check             # kiểm cases.yaml, nhãn, adjudication; in kappa
  python scripts/eval_tools.py freeze --by "Họ Tên"   # đóng băng (cần ca hợp lệ, đủ số dev/test)
  python scripts/eval_tools.py check-freeze      # so trạng thái hiện tại với eval_freeze.json
Không gọi API, không sinh nội dung ca/nhãn.
"""

from __future__ import annotations

import argparse
import csv
import sys

from steel_xai.config import PROJECT_ROOT, load_config
from steel_xai.eval import (
    FreezeError,
    build_manifest,
    diff_manifest,
    disagreements,
    final_references,
    load_case_context,
    load_cases,
    load_eval_settings,
    load_label_sets,
    load_rag_index,
    read_freeze,
    reference_agreement,
    reference_kappa,
    status_kappa,
    taxonomy_from_library,
    validate_adjudication,
    write_freeze,
)
from steel_xai.eval.cases import read_yaml
from steel_xai.hypotheses import load_library
from steel_xai.hypotheses.library import load_library_settings, person_name_problem


def _print(title: str, items: list[str]) -> None:
    if items:
        print(f"{title} ({len(items)}):")
        for x in items:
            print(f"  - {x}")


def cmd_candidates() -> int:
    path = PROJECT_ROOT / load_config("dataset")["paths"]["processed"] / "anomaly_candidates.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    print("Bất thường do engine phát hiện (candidate=true: vượt ngưỡng). NHÓM chọn ca.")
    for r in rows:
        aid = f"{r['company']}|{r['period']}|{r['type']}|{r['direction']}"
        mark = "*" if r["candidate"] == "true" else " "
        print(f" {mark} {aid:45s} deviation={r['deviation']:>9s} rules={r['rules_hit']}")
    n = sum(r["candidate"] == "true" for r in rows)
    print(f"{n} ứng viên vượt ngưỡng / {len(rows)} dòng")
    return 0


def _final_cases(strict_counts: bool = False):
    """(settings, ca kèm đáp án cuối nếu đủ, danh sách lỗi). In mọi lỗi/cảnh báo/kappa."""
    s = load_eval_settings()
    cc = load_cases(s, load_case_context(), strict_counts=strict_counts)
    _print("Lỗi danh sách ca", list(cc.errors))
    _print("Cảnh báo danh sách ca", list(cc.warnings))
    if not cc.cases:
        return s, (), list(cc.errors) or ["không có ca hợp lệ"]
    n_split = {sp: sum(c.split == sp for c in cc.cases) for sp in ("dev", "test")}
    print(f"Ca hợp lệ: {len(cc.cases)} ({n_split})")
    taxonomy = taxonomy_from_library(load_library().reviewed)
    lib_s = load_library_settings()
    sets, errs, warns = load_label_sets(cc.cases, taxonomy, s, lib_s, load_rag_index())
    _print("Lỗi nhãn", errs)
    _print("Cảnh báo nhãn", warns)
    if len(sets) != 2:
        return s, cc.cases, [*cc.errors, *errs]
    a, b = sets
    k_ref = reference_kappa(a, b, cc.cases, taxonomy, s.other_id)
    print(f"Kappa đáp án ({a.annotator} / {b.annotator}): {k_ref.to_dict()}")
    print(f"Đồng thuận theo ca: {reference_agreement(a, b)}")
    print(f"Kappa trạng thái: {status_kappa(a, b).to_dict()}")
    dis = disagreements(a, b)
    _print("Bất đồng cần người thứ ba", [f"{c}: {'; '.join(w)}" for c, w in dis.items()])
    raw = read_yaml(s.adjudication_path) if s.adjudication_path.is_file() else None
    _, decisions, adj_errs = validate_adjudication(
        raw, cc.cases, [a.annotator, b.annotator], taxonomy, s, lib_s
    )
    _print("Lỗi adjudication", adj_errs)
    final, ferrs = final_references(cc.cases, a, b, decisions)
    _print("Chưa có đáp án cuối", ferrs)
    print(f"Ca có đáp án cuối: {len(final)}/{len(cc.cases)}")
    all_errs = [*cc.errors, *errs, *adj_errs, *ferrs]
    return s, (final if not ferrs else cc.cases), all_errs


def cmd_check() -> int:
    _, _, errs = _final_cases()
    return 1 if errs else 0


def cmd_freeze(by: str, overwrite: bool) -> int:
    if (why := person_name_problem(by, load_library_settings())) is not None:
        print(f"--by {by!r} {why}")
        return 2
    s, cases, errs = _final_cases(strict_counts=True)
    if errs:
        print("Không đóng băng được: sửa hết lỗi ở trên (ca, nhãn, đáp án cuối).")
        return 1
    try:
        body = write_freeze(s.freeze_file, build_manifest(s, cases), by, overwrite=overwrite)
    except FreezeError as e:
        print(f"Không đóng băng được: {e}")
        return 1
    n_files, n_test = len(body["files"]), len(body["test"]["cases"])
    print(f"Đã đóng băng {n_files} file, {n_test} ca test → {s.freeze_file}")
    print(f"manifest_sha256 = {body['manifest_sha256']} (commit file này ngay)")
    return 0


def cmd_check_freeze() -> int:
    s = load_eval_settings()
    frozen = read_freeze(s.freeze_file)
    if frozen is None:
        print("Chưa đóng băng.")
        return 1
    _, cases, _ = _final_cases()
    try:
        diffs = diff_manifest(frozen, build_manifest(s, cases))
    except FreezeError as e:
        print(f"Không kiểm được: {e}")
        return 1
    _print("Lệch so với freeze", diffs)
    if not diffs:
        print(f"Khớp freeze ({frozen.get('frozen_at')}, {frozen.get('frozen_by')}).")
    return 1 if diffs else 0


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("candidates")
    sub.add_parser("check")
    f = sub.add_parser("freeze")
    f.add_argument("--by", required=True, help="họ tên người đóng băng")
    f.add_argument("--overwrite", action="store_true")
    sub.add_parser("check-freeze")
    args = ap.parse_args(argv)
    if args.cmd == "candidates":
        return cmd_candidates()
    if args.cmd == "check":
        return cmd_check()
    if args.cmd == "freeze":
        return cmd_freeze(args.by, args.overwrite)
    return cmd_check_freeze()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
