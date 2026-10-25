"""Phase 12: chạy A/B/C, chạy phụ, ablation; sinh bảng cho người; tính metric (spec 14, 15).

Chạy:
  python scripts/run_eval.py estimate --split dev            # KHÔNG gọi API: đếm lời gọi LLM tối đa
  python scripts/run_eval.py run --split dev                 # gọi API (TỐN TOKEN), 3 lần mỗi ca
  python scripts/run_eval.py run --split dev --configs A,C --cases C01 --runs 1
  python scripts/run_eval.py run --split test                # chỉ chạy khi freeze khớp
  python scripts/run_eval.py run --split test --unfreeze-reason "..."   # lý do ghi vào kết quả
  python scripts/run_eval.py sheets --split dev              # bảng cho người điền
  python scripts/run_eval.py score --split dev --provisional "Nguyễn Tùng Dương"   # chấm tạm (dev)
  python scripts/run_eval.py score --split test              # chấm bằng đáp án cuối
Kết quả: outputs/eval/<split>/ (runs, keys, results). Bảng cho người: data/eval/sheets/<split>/.
"""

from __future__ import annotations

import argparse
import sys

from steel_xai.config import load_all_configs, load_config
from steel_xai.eval import FreezeError, require_frozen
from steel_xai.eval import export as X
from steel_xai.eval.labels import taxonomy_from_library
from steel_xai.eval.references import load_references, provisional_cases
from steel_xai.eval.runner import build_context, estimate_calls, load_records, run_eval
from steel_xai.eval.settings import ALL_CONFIGS, load_runner_settings
from steel_xai.hypotheses import load_library
from steel_xai.hypotheses.library import load_library_settings


def _pick(split: str, cases_arg: str | None, refs_cases):
    cases = [c for c in refs_cases if c.split == split]
    if cases_arg:
        want = {x.strip() for x in cases_arg.split(",") if x.strip()}
        unknown = want - {c.case_id for c in cases}
        if unknown:
            raise SystemExit(f"ca không có trong tập {split}: {sorted(unknown)}")
        cases = [c for c in cases if c.case_id in want]
    if not cases:
        raise SystemExit(f"không có ca nào trong tập {split}")
    return cases


def _configs(arg: str | None, default: tuple[str, ...]) -> list[str]:
    if not arg:
        return list(default)
    out = [x.strip() for x in arg.split(",") if x.strip()]
    bad = [x for x in out if x not in ALL_CONFIGS]
    if bad:
        raise SystemExit(f"cấu hình lạ {bad}; hợp lệ: {list(ALL_CONFIGS)}")
    return out


def cmd_estimate(args) -> int:
    rs = load_runner_settings()
    refs = load_references()
    cases = _pick(args.split, args.cases, refs.cases)
    configs = _configs(args.configs, rs.configs)
    from steel_xai.report.settings import load_report_settings

    attempts = load_report_settings().max_attempts
    ctx = build_context(rs, None)
    runs = args.runs or rs.runs_per_case
    total = 0
    for c in cases:
        a = ctx.anomalies.get(c.anomaly_id)
        if a is None:
            print(f"{c.case_id}: engine không phát hiện {c.anomaly_id}")
            continue
        per = {cfg: estimate_calls(ctx, a, cfg, attempts) for cfg in configs}
        total += sum(per.values()) * runs
        print(f"{c.case_id} {c.anomaly_id}: {per} × {runs} lần")
    print(f"Tổng số lời gọi LLM TỐI ĐA: {total} (cache có thể giảm). Không gọi API.")
    return 0


def cmd_run(args) -> int:
    rs = load_runner_settings()
    refs = load_references()
    freeze = None
    if args.split == "test":
        if not refs.final:
            print("Tập test cần đáp án cuối của mọi ca (hai người gán + người thứ ba):")
            for e in refs.errors:
                print(f"  - {e}")
            return 1
        try:
            freeze = require_frozen(refs.settings, refs.cases, args.unfreeze_reason).to_dict()
        except FreezeError as e:
            print(f"Từ chối chạy tập test: {e}")
            return 1
        if freeze["differences"]:
            print(
                f"CẢNH BÁO: chạy dù lệch freeze; lý do ghi vào kết quả: {freeze['unfreeze_reason']}"
            )
    cases = _pick(args.split, args.cases, refs.cases)
    configs = _configs(args.configs, rs.configs)
    from steel_xai.llm import LLMClient, load_llm_settings, make_transport

    load_all_configs()  # kiểm mọi config trước khi tiêu token
    llm_cfg = load_config("llm")
    client = LLMClient(load_llm_settings(llm_cfg), make_transport(llm_cfg))
    ctx = build_context(rs, client)
    if args.runs > rs.runs_per_case:
        print(
            f"--runs {args.runs} > runs_per_case {rs.runs_per_case} (configs/eval.yaml): dùng "
            f"{rs.runs_per_case}"
        )
    run_ids = rs.run_ids()[: args.runs] if args.runs else rs.run_ids()
    recs = run_eval(ctx, cases, configs, run_ids, freeze=freeze, force=args.force, progress=print)
    n_err = sum(r.status != "ok" for r in recs)
    t = client.totals()
    print(f"Xong {len(recs)} lần chạy ({n_err} lỗi) → {rs.output_dir / args.split}")
    print(
        f"LLM: {t.calls} lời gọi (cache {t.cache_hits}), token {t.input_tokens}/{t.output_tokens}"
    )
    for w in client.warnings:
        print("CẢNH BÁO:", w)
    return 1 if n_err else 0


def _codes() -> dict[str, str]:
    return {h.id: h.code or "" for h in load_library().reviewed}


def cmd_sheets(args) -> int:
    rs = load_runner_settings()
    refs = load_references()
    records = load_records(rs.output_dir, args.split)
    if not records:
        print(f"chưa có kết quả chạy tập {args.split}")
        return 1
    try:
        msgs = X.write_sheets(
            records, refs.cases, rs, args.split, _codes(), overwrite=args.overwrite
        )
    except (FileExistsError, ValueError) as e:
        print(e)
        return 1
    for m in msgs:
        print(m)
    if args.split == "test" and refs.final:
        try:
            p, n = X.write_failure_sheet(records, refs.cases, rs, refs.settings.other_id)
        except ValueError as e:
            print(f"chưa sinh được bảng ca sai: {e}")
            return 1
        print(f"ca sai của C: {n} ca → {p}")
    print("Người điền: chép *_TEMPLATE.csv thành <tên bảng>_<họ tên>.csv (ánh xạ, chấm: 2 người).")
    return 0


def cmd_score(args) -> int:
    rs = load_runner_settings()
    refs = load_references()
    header = []
    if args.provisional:
        if args.split != "dev":
            print("--provisional chỉ dùng cho tập dev (tập test chấm bằng đáp án cuối)")
            return 2
        try:
            cases = provisional_cases(refs, args.provisional)
        except ValueError as e:
            print(e)
            return 2
        header.append(f"ĐÁP ÁN TẠM: chỉ nhãn của {args.provisional} (chưa có người gán thứ hai)")
    elif refs.final:
        cases = tuple(c for c in refs.cases if c.split == args.split)
        header.append(f"Đáp án cuối (kappa đáp án: {refs.info.get('kappa_reference')})")
    else:
        print("Chưa có đáp án cuối; dùng --provisional <họ tên> để chấm tạm trên dev:")
        for e in refs.errors:
            print(f"  - {e}")
        return 1
    records = load_records(rs.output_dir, args.split)
    if not records:
        print(f"chưa có kết quả chạy tập {args.split}")
        return 1
    froz = {str(r.freeze.get("current_sha256")) for r in records if r.freeze}
    fps = {r.fingerprint for r in records}
    errors = [r for r in records if r.status != "ok"]
    if args.split == "test" and (errors or len(fps) > 1 or len(froz) > 1):
        print(
            f"Không chấm tập test: {len(errors)} lần chạy lỗi, {len(fps)} phiên bản code/config, "
            f"{len(froz)} manifest freeze. Chạy lại cho đủ (run --split test) trên cùng phiên bản."
        )
        return 1
    if len(fps) > 1:
        header.append(
            f"CẢNH BÁO: kết quả trộn {len(fps)} phiên bản code/config/prompt (nên chạy lại)"
        )
    if args.split == "test":
        unfrozen = [r for r in records if not r.freeze]
        if unfrozen:
            print(f"{len(unfrozen)} lần chạy test không có thông tin freeze: không chấm")
            return 1
        reasons = {
            r.freeze.get("unfreeze_reason") for r in records if r.freeze.get("unfreeze_reason")
        }
        header.append(
            f"Freeze manifest: {sorted(froz)}; bỏ qua freeze: {sorted(reasons) or 'không'}"
        )
    models = sorted(
        {f"{r.llm.get('provider')}/{r.llm.get('model')}" for r in records if r.llm.get("model")}
    )
    header.append(f"LLM: {', '.join(models) or '–'}; {rs.runs_per_case} lần chạy/ca")
    header.append(f"`other` tính là trúng: {rs.other_counts_as_hit}")
    lib_s = load_library_settings()
    taxonomy = taxonomy_from_library(load_library().reviewed)
    mapping, m_info = X.final_mapping(
        rs, args.split, cases, taxonomy, refs.settings.other_id, lib_s
    )
    ratings, ckey, r_info = X.final_ratings(rs, args.split, lib_s)
    human = {
        "blind_mapping": m_info,
        "citation_rating": r_info,
        "unverified": X.unverified_quality(rs, args.split, lib_s),
    }
    if args.split == "test" and refs.final:
        human["failures"] = X.failure_summary(
            rs, records, cases, refs.settings.other_id, refs.settings.failure_causes, lib_s
        )
    results = X.compute_results(
        records,
        cases,
        rs,
        refs.settings.other_id,
        mapping=mapping,
        ratings=ratings,
        citation_key=ckey,
        human=human,
        header=header,
    )
    for p in X.write_results(results, records, rs, args.split, _codes()):
        print(f"→ {p}")
    for line in results["header"]:
        print(line)
    return 0


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows: tránh UnicodeEncodeError
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("estimate", "run", "sheets", "score"):
        p = sub.add_parser(name)
        p.add_argument("--split", choices=("dev", "test"), required=True)
        if name in ("estimate", "run"):
            p.add_argument("--configs", help="danh sách, ví dụ A,B,C (mặc định: mọi cấu hình)")
            p.add_argument("--cases", help="danh sách case_id (mặc định: cả tập)")
            p.add_argument("--runs", type=int, default=0, help="số lần chạy (mặc định: config)")
        if name == "run":
            p.add_argument("--force", action="store_true", help="chạy lại cả bản đã có")
            p.add_argument("--unfreeze-reason", default=None)
        if name == "sheets":
            p.add_argument("--overwrite", action="store_true")
        if name == "score":
            p.add_argument("--provisional", help="họ tên người gán: chấm TẠM trên dev")
    args = ap.parse_args(argv)
    return {"estimate": cmd_estimate, "run": cmd_run, "sheets": cmd_sheets, "score": cmd_score}[
        args.cmd
    ](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
