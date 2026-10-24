"""Prompt và schema của bước 4 (spec mục 11.3, 13). Nằm trong code để băm vào freeze.

LLM chỉ viết lại kết quả đã xác thực thành các khẳng định ngắn, mỗi khẳng định dẫn mã nguồn. Tiêu
đề, bảng trạng thái, danh sách nguồn do code dựng; LLM không quyết định trạng thái, không tính số.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

REPORT_PROMPT_VERSION = 1
SECTIONS = (
    "anomaly",
    "quantitative",
    "top_hypotheses",
    "evidence",
    "contradictions",
    "missing",
    "conclusion",
)

REPORT_SYSTEM = (
    "Bạn viết báo cáo giải thích một bất thường trong báo cáo tài chính của doanh nghiệp thép Việt "
    "Nam, CHỈ từ dữ liệu JSON đã được hệ thống kiểm chứng. Bạn không có nguồn nào khác.\n"
    "Trả về danh sách khẳng định (claims). Mỗi khẳng định là một hoặc hai câu tiếng Việt (tên kỹ "
    "thuật giữ tiếng Anh), thuộc một mục:\n"
    "- anomaly: bất thường là gì;\n"
    "- quantitative: các quan sát định lượng chính;\n"
    "- top_hypotheses: tóm tắt các giả thuyết hàng đầu và trạng thái của chúng;\n"
    "- evidence: bằng chứng ủng hộ của từng giả thuyết;\n"
    "- contradictions: bằng chứng phản bác (nếu có);\n"
    "- missing: dữ liệu/bằng chứng còn thiếu và vì sao giả thuyết chưa xác thực được;\n"
    "- conclusion: kết luận CÓ ĐIỀU KIỆN.\n"
    "Quy tắc bắt buộc:\n"
    "1. Mỗi khẳng định phải có `sources`: các mã N…, E…, M…, H… lấy từ JSON. Không dẫn mã "
    "không có.\n"
    "2. Mọi con số phải lấy từ `display` của mã N đã dẫn, chép NGUYÊN VĂN (kể cả dấu phẩy thập "
    "phân và đơn vị), hoặc từ chính văn bản của mã E/M/H đã dẫn. Không tự tính, không làm tròn "
    'lại, không đổi đơn vị, không quy đổi sang "lần".\n'
    "3. Trạng thái giả thuyết (SUPPORTED/PARTIAL/UNVERIFIED/CONTRADICTED) do hệ thống quyết định: "
    "chỉ được nhắc lại đúng trạng thái trong JSON. UNVERIFIED nghĩa là chưa kiểm được, không "
    "phải sai.\n"
    "4. Khẳng định về một giả thuyết cụ thể đặt `hypothesis_id` (id giả thuyết, không phải mã H); "
    "mã E dẫn phải thuộc đúng giả thuyết đó. Khẳng định chung để chuỗi rỗng.\n"
    "5. Không khẳng định quan hệ nhân quả chắc chắn trừ khi giả thuyết là SUPPORTED; dùng cách nói "
    'có điều kiện ("có thể", "phù hợp với", "nếu"). Mục conclusion luôn phải có điều kiện.\n'
    '6. Cấm: khuyến nghị mua/bán/đầu tư, nhận định định giá, kết luận gian lận, các từ "chắc '
    'chắn", "đã chứng minh".\n'
    "7. Nếu `used_drafts` là true, nói rõ đây là kết quả chạy thử trên giả thuyết chưa được "
    "duyệt.\n"
    "8. Giả thuyết không có bằng chứng văn bản (`has_verified_text` false) thì nói rõ là chỉ dựa "
    "trên số liệu định lượng."
)

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": list(SECTIONS)},
                    "hypothesis_id": {"type": "string"},
                    "text": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["section", "hypothesis_id", "text", "sources"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


def report_user(input_json: str, feedback: list[str] | None = None, attempt: int = 1) -> str:
    parts = ["DỮ LIỆU ĐÃ KIỂM CHỨNG (JSON):", input_json]
    if feedback:
        parts += [
            "",
            f"LẦN VIẾT THỨ {attempt}. BẢN NHÁP TRƯỚC BỊ BỘ KIỂM TỪ CHỐI vì các lỗi sau. Viết lại "
            "TOÀN BỘ danh sách khẳng định, sửa hết các lỗi này:",
            *(f"- {x}" for x in feedback),
        ]
    return "\n".join(parts)


def report_prompt_fingerprint() -> str:
    """Băm prompt và schema của báo cáo, để eval_freeze phát hiện thay đổi (spec mục 15.1)."""
    blob = json.dumps(
        {
            "v": REPORT_PROMPT_VERSION,
            "system": REPORT_SYSTEM,
            "schema": REPORT_SCHEMA,
            "user_template": report_user("{input}", ["{feedback}"], 2),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
