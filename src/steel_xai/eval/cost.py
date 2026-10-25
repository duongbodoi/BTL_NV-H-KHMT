"""Ước tính chi phí chạy đánh giá bằng model tính phí (configs/cost/pricing.yaml). Không gọi API.

Hai nguồn số token:
- `prompt`: dựng đúng prompt mà runner sẽ gửi cho từng ca thật (A/B/chạy phụ chính xác; C và
  no_library xấp xỉ: truy vấn gốc chưa diễn đạt lại, báo cáo dựng từ phân tích không có bằng chứng
  văn bản), đổi ký tự → token theo khoảng `chars_per_token`; token ra theo giả định.
- `measured`: token đo thật trong RunRecord (sau khi chạy free tier). Token do tokenizer của model
  đã
  chạy đếm; model hãng khác đếm lệch (thường ±30%), nên vẫn báo khoảng.
Chỉ là ước tính để nhóm cân nhắc; mọi giả định in kèm kết quả.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from steel_xai.config import PROJECT_ROOT, ConfigError
from steel_xai.contracts import Anomaly, CaseRecord, RunRecord

PRICING_PATH = PROJECT_ROOT / "configs" / "cost" / "pricing.yaml"
PURPOSES = (
    "baseline_free",
    "ranking",
    "generate",
    "rephrase_queries",
    "classify_passages",
    "grounded_report",
)


@dataclass(frozen=True)
class Price:
    model: str
    provider: str
    input: float
    output: float
    free_tier: bool
    note: str = ""


Band = dict[str, float]


@dataclass(frozen=True)
class Assumptions:
    chars_per_token: Band  # low | mid | high; ít ký tự mỗi token thì nhiều token hơn
    output_tokens: dict[str, int]
    thinking_factor: float
    report_attempts: float
    retry_overhead: float
    output_band: dict[str, float]  # hệ số nhân token ra cho mức chi phí low | mid | high


def load_pricing(path: Path = PRICING_PATH) -> tuple[list[Price], Assumptions]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    prices = []
    for name, m in (raw.get("models") or {}).items():
        try:
            prices.append(
                Price(
                    name,
                    str(m["provider"]),
                    float(m["input"]),
                    float(m["output"]),
                    bool(m.get("free_tier", False)),
                    str(m.get("note", "")),
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ConfigError(
                f"pricing.models.{name}: thiếu/sai input/output/provider ({e})"
            ) from e
    if not prices:
        raise ConfigError("pricing.models rỗng")
    est = raw.get("estimate") or {}
    cpt = {k: float(v) for k, v in (est.get("chars_per_token") or {}).items()}
    out = {k: int(v) for k, v in (est.get("output_tokens") or {}).items()}
    if set(cpt) != {"low", "mid", "high"} or not all(v > 0 for v in cpt.values()):
        raise ConfigError("pricing.estimate.chars_per_token phải có low, mid, high > 0")
    if missing := set(PURPOSES) - set(out):
        raise ConfigError(f"pricing.estimate.output_tokens thiếu {sorted(missing)}")
    return prices, Assumptions(
        cpt,
        out,
        float(est.get("thinking_factor", 0)),
        float(est.get("report_attempts", 1)),
        float(est.get("retry_overhead", 1)),
        _output_band(est.get("output_band")),
    )


def _output_band(v) -> dict[str, float]:
    v = v or {"low": 1.0, "high": 1.0}
    try:
        band = {"low": float(v["low"]), "mid": 1.0, "high": float(v["high"])}
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError("pricing.estimate.output_band phải có low, high") from e
    if not 0 < band["low"] <= 1 <= band["high"]:
        raise ConfigError("pricing.estimate.output_band: cần 0 < low <= 1 <= high")
    return band


@dataclass
class Usage:
    """Số ký tự vào (theo tác vụ) và số token ra nhìn thấy, cộng dồn cho một lần chạy."""

    input_chars: float = 0.0
    output_tokens: float = 0.0
    calls: float = 0.0

    def add(self, chars: float, out: float, n: float = 1.0) -> None:
        self.input_chars += chars * n
        self.output_tokens += out * n
        self.calls += n


# ---------------- ước tính từ prompt (dữ liệu thật, không gọi API)


def _chars(system: str, user: str) -> int:
    return len(system) + len(user)


def prompt_usage(ctx: Any, a: Anomaly, config: str, asm: Assumptions) -> Usage:
    """Ký tự vào / token ra của MỘT lần chạy `config` trên bất thường `a` (dựng prompt thật)."""
    from steel_xai.eval import baselines as bl
    from steel_xai.eval.runner import applicable, indicator_input, retrieve
    from steel_xai.llm.prompts import (
        CLASSIFY_SYSTEM,
        REPHRASE_SYSTEM,
        classify_user,
        rephrase_user,
    )
    from steel_xai.report.facts import build_report_input
    from steel_xai.report.prompts import REPORT_SYSTEM, report_user
    from steel_xai.verification.analyze import analyze_anomaly

    o = asm.output_tokens
    u = Usage()
    inp = indicator_input(a, ctx.report_settings)
    lib = applicable(ctx.library, a)
    n = ctx.runner.max_hypotheses
    if config in ("A", "B"):
        passages = (
            retrieve(ctx, a, ctx.runner.baseline_queries.get(a.type, ())) if config == "B" else None
        )
        sys_ = (bl.FREE_SYSTEM_B if passages is not None else bl.FREE_SYSTEM_A).format(n=n)
        u.add(_chars(sys_, bl.user_prompt(a, inp, passages)), o["baseline_free"])
    elif config in ("A_lib", "B_lib", "no_verification"):
        queries = None
        if config == "B_lib":
            queries = ctx.runner.baseline_queries.get(a.type, ())
        elif config == "no_verification":
            queries = list(
                dict.fromkeys(
                    q for h in lib for c in h.checks if c.kind == "text" for q in c.queries
                )
            )
        passages = retrieve(ctx, a, queries) if queries is not None else None
        sys_ = bl.LIBRARY_SYSTEM.format(passages=bl._LIB_PASSAGES if passages is not None else "")
        u.add(_chars(sys_, bl.user_prompt(a, inp, passages, lib)), o["ranking"])
    elif config in ("C", "no_library"):
        per_rephrase, per_classify = _text_check_sizes(ctx, a, lib, rephrase_user, classify_user)
        if config == "C":
            for h in lib:
                checks = [c for c in h.checks if c.kind == "text"]
                if not checks:
                    continue
                u.add(len(REPHRASE_SYSTEM) + per_rephrase.get(h.id, 0), o["rephrase_queries"])
                for c in checks:
                    u.add(
                        len(CLASSIFY_SYSTEM) + per_classify.get((h.id, c.id), 0),
                        o["classify_passages"],
                    )
            analysis = analyze_anomaly(
                a, ctx.library, quant_ctx=ctx.quant_ctx(a), text_fn=None, rules=ctx.rules
            )
            rep = build_report_input(a, analysis, ctx.library, ctx.report_settings)
            u.add(
                _chars(REPORT_SYSTEM, report_user(rep.to_prompt())),
                o["grounded_report"],
                asm.report_attempts,
            )
        else:
            u.add(_chars(bl.GENERATE_SYSTEM.format(n=n), bl.user_prompt(a, inp)), o["generate"])
            avg_r = _avg(per_rephrase.values())
            avg_c = _avg(per_classify.values())
            u.add(len(REPHRASE_SYSTEM) + avg_r, o["rephrase_queries"], n)
            u.add(len(CLASSIFY_SYSTEM) + avg_c, o["classify_passages"], n)
    # no_rag: không gọi LLM
    return u


def _avg(xs: Any) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _text_check_sizes(ctx, a, lib, rephrase_user, classify_user):
    """Số ký tự user prompt của diễn đạt truy vấn (mỗi giả thuyết) và phân loại (mỗi check văn bản),
    với đoạn truy xuất bằng truy vấn GỐC (chưa diễn đạt lại: thiếu một ít so với thật)."""
    per_rephrase: dict[str, int] = {}
    per_classify: dict[tuple[str, str], int] = {}
    k = ctx.evidence_settings.max_passages_per_check
    for h in lib:
        checks = [c for c in h.checks if c.kind == "text"]
        if not checks:
            continue
        per_rephrase[h.id] = len(
            rephrase_user(
                h.claim,
                a.type,
                a.direction,
                [{"check_id": c.id, "queries": list(c.queries)} for c in checks],
            )
        )
        for c in checks:
            got = (
                ctx.retriever.retrieve_many(list(c.queries), company=a.company, asof=a.asof_date)[
                    :k
                ]
                if a.asof_date and ctx.retriever is not None
                else []
            )
            passages = [
                {"chunk_id": p.chunk_id, "page": p.page, "section": p.section, "text": p.text}
                for p in got
            ]
            per_classify[(h.id, c.id)] = (
                len(classify_user(h.claim, c.polarity, passages)) if passages else 0
            )
    return per_rephrase, per_classify


def tokens_from_usage(u: Usage, asm: Assumptions, band: str) -> tuple[float, float]:
    """(token vào, token ra kể cả suy luận) cho mức chi phí `band` (low | mid | high). Mức chi
    phí THẤP dùng NHIỀU ký tự/token (ít token) và hệ số token ra thấp."""
    cpt = asm.chars_per_token[{"low": "high", "mid": "mid", "high": "low"}[band]]
    tin = u.input_chars / cpt
    tout = u.output_tokens * (1 + asm.thinking_factor) * asm.output_band[band]
    return tin, tout


# ---------------- tổng hợp


@dataclass(frozen=True)
class SplitTokens:
    """Tổng token vào/ra cho cả một tập (mọi ca × cấu hình × lần chạy), theo khoảng low/mid/high."""

    split: str
    n_cases: int
    runs: int
    configs: tuple[str, ...]
    input: dict[str, float]  # band → token
    output: dict[str, float]
    by_config: dict[str, dict[str, float]]  # config → {input, output} (mid)
    source: str  # prompt | measured


def estimate_from_prompts(
    ctx: Any, cases: Sequence[CaseRecord], configs: Sequence[str], runs: int, asm: Assumptions
) -> SplitTokens:
    tin = defaultdict(float)
    tout = defaultdict(float)
    by_cfg: dict[str, dict[str, float]] = defaultdict(lambda: {"input": 0.0, "output": 0.0})
    for case in cases:
        a = ctx.anomalies.get(case.anomaly_id)
        if a is None:
            continue
        for cfg in configs:
            u = prompt_usage(ctx, a, cfg, asm)
            for band in ("low", "mid", "high"):
                i, o = tokens_from_usage(u, asm, band)
                tin[band] += i * runs * asm.retry_overhead
                tout[band] += o * runs * asm.retry_overhead
                if band == "mid":
                    by_cfg[cfg]["input"] += i * runs * asm.retry_overhead
                    by_cfg[cfg]["output"] += o * runs * asm.retry_overhead
    return SplitTokens(
        cases[0].split if cases else "",
        len(cases),
        runs,
        tuple(configs),
        dict(tin),
        dict(tout),
        dict(by_cfg),
        "prompt",
    )


def estimate_from_records(
    records: Sequence[RunRecord],
    n_cases: int,
    configs: Sequence[str],
    runs: int,
    split: str,
    cross_model_band: float = 0.3,
) -> SplitTokens | None:
    """Token đo thật (trung bình mỗi ca × cấu hình, bỏ lần chạy dùng cache) → ngoại suy cho đủ
    `n_cases` × `runs`. Khoảng ±`cross_model_band` cho tokenizer hãng khác."""
    acc: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for r in records:
        if r.status == "ok" and not r.llm.get("cache_hits") and r.config in configs:
            acc[r.config].append((r.tokens.get("input", 0), r.tokens.get("output", 0)))
    if not acc:
        return None
    by_cfg = {}
    for cfg in configs:
        xs = acc.get(cfg)
        if not xs:
            continue  # no_rag: 0 token; cấu hình chưa chạy: không ngoại suy
        mi = sum(x for x, _ in xs) / len(xs) * n_cases * runs
        mo = sum(y for _, y in xs) / len(xs) * n_cases * runs
        by_cfg[cfg] = {"input": mi, "output": mo}
    ti = sum(v["input"] for v in by_cfg.values())
    to = sum(v["output"] for v in by_cfg.values())
    f = {"low": 1 - cross_model_band, "mid": 1.0, "high": 1 + cross_model_band}
    return SplitTokens(
        split,
        n_cases,
        runs,
        tuple(configs),
        {b: ti * k for b, k in f.items()},
        {b: to * k for b, k in f.items()},
        by_cfg,
        "measured",
    )


def cost(tokens_in: float, tokens_out: float, p: Price) -> float:
    return (tokens_in * p.input + tokens_out * p.output) / 1e6


def cost_table(splits: Sequence[SplitTokens], prices: Sequence[Price]) -> list[dict[str, Any]]:
    rows = []
    for p in prices:
        row: dict[str, Any] = {
            "model": p.model,
            "provider": p.provider,
            "input": p.input,
            "output": p.output,
            "free_tier": p.free_tier,
            "note": p.note,
        }
        total = {"low": 0.0, "mid": 0.0, "high": 0.0}
        for s in splits:
            for band in total:
                c = cost(s.input[band], s.output[band], p)
                row[f"{s.split}_{band}"] = c
                total[band] += c
        row.update({f"total_{b}": v for b, v in total.items()})
        rows.append(row)
    return rows


def _usd(x: float) -> str:
    return f"${x:,.2f}" if x >= 0.01 else f"${x:,.4f}"


def render_markdown(
    splits: Sequence[SplitTokens],
    rows: Sequence[Mapping[str, Any]],
    asm: Assumptions,
    current_model: str,
    generated_on: str,
) -> str:
    src = {s.source for s in splits}
    lines = [
        "# Ước tính chi phí chạy đánh giá bằng model tính phí",
        "",
        f"_Sinh bởi `scripts/estimate_cost.py` ngày {generated_on}. Không gọi API. Giá trong "
        "`configs/cost/pricing.yaml` (nguồn và ngày tra ghi trong file). Đây là ước tính để cân "
        "nhắc, không phải hóa đơn._",
        "",
        f"**Đánh giá chính thức dùng `{current_model}` free tier: chi phí thật = 0 đồng** "
        "(giới hạn "
        "số lời gọi/phút, có thể gặp lỗi 429/503 và phải chạy lại). Bảng dưới trả lời câu hỏi: "
        "nếu chạy CÙNG khối lượng bằng model tính phí thì tốn bao nhiêu.",
        "",
        "## Khối lượng",
        "",
        "| Tập | Số ca | Lần chạy/ca | Cấu hình | Token vào (thấp–giữa–cao) "
        "| Token ra (thấp–giữa–cao) |",
        "|---|---|---|---|---|---|",
    ]
    for s in splits:
        lines.append(
            f"| {s.split} | {s.n_cases} | {s.runs} | {len(s.configs)} | "
            f"{s.input['low']:,.0f} – {s.input['mid']:,.0f} – {s.input['high']:,.0f} | "
            f"{s.output['low']:,.0f} – {s.output['mid']:,.0f} – {s.output['high']:,.0f} |"
        )
    lines += [
        "",
        "Token theo cấu hình (mức giữa, cả hai tập):",
        "",
        "| Cấu hình | Token vào | Token ra |",
        "|---|---|---|",
    ]
    agg: dict[str, dict[str, float]] = defaultdict(lambda: {"input": 0.0, "output": 0.0})
    for s in splits:
        for cfg, v in s.by_config.items():
            agg[cfg]["input"] += v["input"]
            agg[cfg]["output"] += v["output"]
    for cfg, v in agg.items():
        lines.append(f"| {cfg} | {v['input']:,.0f} | {v['output']:,.0f} |")
    names = [s.split for s in splits]
    lines += [
        "",
        "## Chi phí ước tính (USD, mức giữa; khoảng thấp–cao trong ngoặc)",
        "",
        "| Model | Giá vào / ra ($/1M) | "
        + " | ".join(f"Tập {n}" for n in names)
        + " | Tổng | Ghi chú |",
        "|---|---|" + "---|" * len(names) + "---|---|",
    ]
    for r in rows:
        cells = [f"{_usd(r[f'{n}_mid'])}" for n in names]
        total = f"**{_usd(r['total_mid'])}** ({_usd(r['total_low'])}–{_usd(r['total_high'])})"
        note = r["note"] + (" Có free tier." if r["free_tier"] and "free" not in r["note"] else "")
        lines.append(
            f"| `{r['model']}` | {r['input']:.2f} / {r['output']:.2f} | "
            + " | ".join(cells)
            + f" | {total} | {note.strip()} |"
        )
    lines += ["", "## Giả định", ""]
    if "prompt" in src:
        lines += [
            "- Token vào: dựng đúng prompt runner sẽ gửi trên dữ liệu thật của từng ca; "
            "đổi ký tự → "
            f"token với {asm.chars_per_token['high']} (thấp) / "
            f"{asm.chars_per_token['mid']} (giữa) / "
            f"{asm.chars_per_token['low']} (cao) ký tự mỗi token (tiếng Việt có dấu tốn token hơn "
            "tiếng Anh; mỗi hãng một tokenizer).",
            "- C và no_library: đoạn truy xuất bằng truy vấn gốc (thật còn thêm truy vấn diễn đạt "
            "lại), báo cáo dựng từ phân tích chưa có bằng chứng văn bản: có thể THIẾU một ít.",
            f"- Token ra nhìn thấy mỗi lời gọi: "
            f"{json.dumps(asm.output_tokens, ensure_ascii=False)}; "
            f"token suy luận ẩn = {asm.thinking_factor} × token ra (tính tiền như token ra); "
            f"mức thấp/cao nhân token ra với {asm.output_band['low']} / {asm.output_band['high']}.",
            f"- Báo cáo của C viết trung bình {asm.report_attempts} lần; hệ số chạy lại vì lỗi "
            f"{asm.retry_overhead}.",
        ]
    if "measured" in src:
        lines.append(
            "- Token đo thật từ lần chạy free tier (bỏ lần chạy lấy từ cache), ngoại suy cho đủ số "
            "ca × số lần; khoảng ±30% cho tokenizer của hãng khác."
        )
    lines += [
        "- Không tính prompt caching và Batch API (cả hai hãng giảm ~50% khi chạy batch); runner "
        "hiện gọi tuần tự.",
        "- Chưa tính chạy thử/chỉnh trên dev nhiều vòng: mỗi vòng chỉnh prompt trên dev tốn thêm "
        'một lần cột "Tập dev".',
        "",
        "_Kết quả là ước tính phục vụ khảo sát khả thi, không phải con số chính xác._",
        "",
    ]
    return "\n".join(lines)
