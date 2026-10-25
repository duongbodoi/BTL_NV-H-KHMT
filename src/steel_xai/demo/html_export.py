"""Bản HTML dự phòng của demo (Phase 13): mỗi ca một file TỰ CHỨA (CSS, biểu đồ SVG nằm trong
file; không JavaScript, không tải gì từ mạng). Mở bằng trình duyệt bất kỳ khi máy trình bày không
chạy được Streamlit. Nội dung giống trang demo, dựng từ cùng `CaseView`. Mọi chữ đều được escape.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from pathlib import Path

from steel_xai.demo.text import CONFIG_VI, DIR_VI, TYPE_VI
from steel_xai.demo.view import (
    STATUS_ORDER,
    STATUS_VI,
    CaseView,
    CheckView,
    report_label,
    status_counts,
)

E = html.escape
STATUS_CSS = {
    "SUPPORTED": "#1b7f3b",
    "PARTIAL": "#b26a00",
    "UNVERIFIED": "#5f6368",
    "CONTRADICTED": "#b3261e",
}
CSS = """
:root{--fg:#1f2328;--muted:#59636e;--line:#d0d7de;--bg:#ffffff;--soft:#f6f8fa}
@media (prefers-color-scheme:dark){:root{--fg:#e6edf3;--muted:#9198a1;--line:#3d444d;
--bg:#0d1117;--soft:#151b23}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:1.6em;margin:.4em 0}h2{font-size:1.25em;margin:1.4em 0 .5em;border-bottom:1px solid
var(--line);padding-bottom:.2em}h3{font-size:1.05em;margin:1em 0 .3em}
.disc{background:#fff4ce;color:#5c4400;border:1px solid #e6c766;padding:8px 12px;border-radius:6px}
.warn{background:#fde7e9;color:#8c1d18;border:1px solid #f1aeb5;padding:8px 12px;border-radius:6px;
margin:6px 0}.muted{color:var(--muted);font-size:.9em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px}
.card{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--soft)}
.card b{display:block;font-size:1.25em}
table{border-collapse:collapse;width:100%;margin:6px 0;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
th{background:var(--soft)}.badge{font-weight:700}
blockquote{margin:6px 0;padding:4px 12px;border-left:4px solid var(--line);color:var(--fg)}
details{border:1px solid var(--line);border-radius:8px;padding:6px 10px;margin:8px 0}
summary{cursor:pointer;font-weight:600}code{background:var(--soft);padding:0 3px;border-radius:3px}
svg{max-width:100%;height:auto;background:#fff;border-radius:6px}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
"""


def _badge(status: str) -> str:
    color = STATUS_CSS.get(status, "#5f6368")
    return (
        f'<span class="badge" style="color:{color}">{E(status)}</span> · '
        f"{E(STATUS_VI.get(status, ''))}"
    )


def _checks(title: str, checks: tuple[CheckView, ...]) -> str:
    if not checks:
        return ""
    out = [f"<h3>{E(title)}</h3><ul>"]
    for c in checks:
        vals = ", ".join(f"<code>{E(k)}</code> = {E(v)}" for k, v in c.values.items())
        out.append(
            f"<li><code>{E(c.check_id)}</code> ({E(c.importance)}, {E(c.kind)})"
            + (f": {vals}" if vals else "")
        )
        if c.detail and c.detail not in ("FOUND", "UNVERIFIED"):
            out.append(f'<div class="muted">{E(c.detail[:300])}</div>')
        for q in c.quotes:
            page = f", trang {q.page}" if q.page is not None else ""
            out.append(f"<blockquote>“{E(q.text)}”<br><i>— {E(q.doc_id)}{E(page)}</i></blockquote>")
        out.append("</li>")
    out.append("</ul>")
    return "".join(out)


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _inline(text: str) -> str:
    t = E(text)
    t = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", t)
    return _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", t)


def markdown_to_html(md: str) -> str:
    """Chuyển Markdown của báo cáo (do code dựng: tiêu đề, gạch đầu dòng, bảng, trích dẫn) sang
    HTML. Không phải bộ chuyển đầy đủ: chỉ các dạng `report/render.py` sinh ra; còn lại là đoạn."""
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        if line.startswith("#"):
            # báo cáo nằm trong mục 4 của trang: hạ 2 cấp tiêu đề (# → h3)
            level = min(len(line) - len(line.lstrip("#")) + 2, 6)
            out.append(f"<h{level}>{_inline(line.lstrip('#').strip())}</h{level}>")
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                head, *body = rows
                out.append(
                    "<table><tr>"
                    + "".join(f"<th>{_inline(c)}</th>" for c in head)
                    + "</tr>"
                    + "".join(
                        "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                        for r in body
                    )
                    + "</table>"
                )
        elif line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
        elif line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(_inline(lines[i].lstrip("> ").rstrip()))
                i += 1
            out.append("<blockquote>" + "<br>".join(quote) + "</blockquote>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
            i += 1
    return "\n".join(out)


def render_html(
    view: CaseView,
    disclaimer: str,
    *,
    top: int = 3,
    series_svg: str = "",
    role: str = "",
    role_note: str = "",
) -> str:
    t = TYPE_VI.get(view.anomaly_type, view.anomaly_type)
    title = f"{t} {DIR_VI.get(view.direction, view.direction)} — {view.company} {view.period}"
    p: list[str] = [
        "<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{E(view.case_id)} · {E(title)}</title><style>{CSS}</style></head><body><main>",
        f'<p class="disc">{E(disclaimer)}</p>',
        f"<h1>{E(title)}</h1>",
        f'<p class="muted">Ca {E(view.case_id)} · {E(view.split)} · {E(view.run_id)} · mốc as-of '
        f"{E(view.asof_date or 'không rõ')} · LLM {E(view.model)} · chỉ báo do code tính"
        + (f" · <b>{E(role)}</b>" if role else "")
        + "</p>",
    ]
    if role_note:
        p.append(f'<p class="muted">{E(role_note)}</p>')
    p += [f'<div class="warn">Cảnh báo dữ liệu: {E(w)}</div>' for w in view.warnings]
    # 1. Bất thường
    p.append("<h2>1. Bất thường</h2><div class='grid'>")
    p += [f'<div class="card">{E(i.label)}<b>{E(i.display)}</b></div>' for i in view.indicators]
    p.append("</div>")
    if view.rules_hit:
        p.append(
            "<p>Luật vượt ngưỡng: "
            + ", ".join(f"<code>{E(r)}</code>" for r in view.rules_hit)
            + "</p>"
        )
    if view.flags:
        p.append('<p class="muted">Cờ dữ liệu: ' + ", ".join(E(f) for f in view.flags) + "</p>")
    # 2. Giả thuyết
    counts = status_counts(view)
    p.append("<h2>2. Giả thuyết và trạng thái kiểm chứng</h2>")
    p.append(
        '<p class="muted">Giả thuyết lấy từ thư viện do nhóm soạn; trạng thái do luật trong '
        "code quyết định (LLM chỉ phân loại đoạn văn bản và viết lại).</p>"
    )
    p.append(
        "<div class='grid'>"
        + "".join(
            f'<div class="card">{E(STATUS_VI[s])}<b>{counts[s]}</b></div>' for s in STATUS_ORDER
        )
        + "</div>"
    )
    p.append(
        "<table><tr><th>Hạng</th><th>Mã</th><th>Giả thuyết</th><th>Trạng thái</th>"
        "<th>Check critical đạt</th></tr>"
    )
    for h in view.hypotheses:
        p.append(
            f"<tr><td>{h.rank}</td><td>{E(h.code)}</td><td>{E(h.claim)}</td>"
            f"<td>{_badge(h.status)}</td><td>{E(h.critical)}</td></tr>"
        )
    p.append("</table>")
    # 3. Bằng chứng
    p.append("<h2>3. Bằng chứng, trích dẫn và phần còn thiếu</h2>")
    for h in view.hypotheses:
        detailed = h.rank <= top
        if not detailed and not h.contradictions:
            continue
        p.append(
            f"<details{' open' if h.rank == 1 else ''}><summary>#{h.rank} {E(h.code)} "
            f"{E(h.claim)}</summary><p>{_badge(h.status)} · check critical đạt "
            f"{E(h.critical)}</p>"
        )
        if detailed:
            p.append(_checks("Bằng chứng ủng hộ", h.evidence))
        p.append(_checks("Bằng chứng phản bác", h.contradictions))
        if detailed:
            p.append(_checks("Kỳ vọng không xảy ra", h.failed))
            p.append(_checks("Còn thiếu / chưa kiểm được", h.missing))
        p.append("</details>")
    if view.series and series_svg:
        p.append("<h3>Chuỗi giá đầu vào mà các check đã dùng</h3>" + series_svg)
    # 4. Giải thích cuối
    p.append("<h2>4. Giải thích cuối</h2>")
    p.append(f'<p class="muted">{E(report_label(view.report_status))}</p>')
    p.append(
        markdown_to_html(view.report_markdown) if view.report_markdown else "<p>(không có)</p>"
    )
    # 5. So sánh
    if view.compare:
        p.append("<h2>5. So sánh cấu hình (cùng ca, cùng lần chạy)</h2>")
        p.append(
            '<p class="muted">Số liệu bản nháp đầu của mỗi cấu hình (C còn được bộ kiểm cho '
            "sinh lại).</p><div class='cols'>"
        )
        for b in view.compare:
            items = "".join(f"<li>{E(x)}</li>" for x in b.hypotheses)
            p.append(
                f'<div class="card"><h3>{E(CONFIG_VI.get(b.config, b.config))}</h3><ol>{items}</ol>'
                f'<p class="muted">{b.claims} khẳng định · không nguồn: {b.unsupported} · lệch '
                f"số: {b.numeric_mismatches} · trích dẫn qua kiểm nguyên văn: {b.citations_ok}/"
                f"{b.citations_total}</p></div>"
            )
        p.append("</div>")
    p.append(f'<p class="disc" style="margin-top:24px">{E(disclaimer)}</p></main></body></html>')
    return "\n".join(x for x in p if x)


def render_index(entries: Sequence[tuple[str, str, str, str]], disclaimer: str) -> str:
    """Trang mục lục: [(tên file, ca, vai trò, mô tả)]."""
    rows = "".join(
        f"<tr><td><a href='{E(f)}'>{E(c)}</a></td><td>{E(r)}</td><td>{E(n)}</td></tr>"
        for f, c, r, n in entries
    )
    return (
        "<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Demo dự phòng</title><style>{CSS}</style></head><body><main>"
        f'<p class="disc">{E(disclaimer)}</p>'
        "<h1>Demo dự phòng: giải thích bất thường BCTC thép</h1>"
        "<p class='muted'>Bản tĩnh xuất từ kết quả đã chạy (không cần mạng hay Python).</p>"
        f"<table><tr><th>Ca</th><th>Vai trò</th><th>Mô tả</th></tr>{rows}</table>"
        f'<p class="disc" style="margin-top:24px">{E(disclaimer)}</p></main></body></html>'
    )


def write_case_files(
    out_dir: Path,
    views: Sequence[tuple[CaseView, str, str]],
    disclaimer: str,
    *,
    top: int = 3,
) -> list[Path]:
    """Ghi mỗi ca một file HTML + `index.html`. views: [(view, vai trò, mô tả)]."""
    from steel_xai.eval.export import svg_series

    out_dir.mkdir(parents=True, exist_ok=True)
    paths, entries = [], []
    for v, role, note in views:
        name = f"{v.case_id}_{v.run_id}.html"
        svg = svg_series(v.series, f"Chuỗi giá — {v.anomaly_id}") if v.series else ""
        (out_dir / name).write_text(
            render_html(v, disclaimer, top=top, series_svg=svg, role=role, role_note=note),
            encoding="utf-8",
        )
        paths.append(out_dir / name)
        entries.append((name, v.case_id, role, note))
    (out_dir / "index.html").write_text(render_index(entries, disclaimer), encoding="utf-8")
    return [out_dir / "index.html", *paths]
