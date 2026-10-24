"""Kiểm JSON theo một tập con nhỏ của JSON Schema (không thêm phụ thuộc).

Hỗ trợ: type (object/array/string/integer/number/boolean/null), properties, required,
additionalProperties (bool), items, enum. Đủ cho schema đầu ra có cấu trúc của Phase 9; giới hạn độ
dài/số lượng do code kiểm riêng vì API structured output không hỗ trợ mọi từ khóa.
"""

from __future__ import annotations

from typing import Any

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}
_ALLOWED_KEYS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "description",
}


def check_schema(schema: Any, path: str = "$") -> list[str]:
    """Lỗi của chính schema (từ khóa lạ, thiếu additionalProperties=false cho object)."""
    errs: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: schema phải là mapping"]
    for k in schema:
        if k not in _ALLOWED_KEYS:
            errs.append(f"{path}: từ khóa schema không hỗ trợ {k!r}")
    t = schema.get("type")
    if t not in _TYPES:
        errs.append(f"{path}: type phải thuộc {sorted(_TYPES)}")
    if t == "object":
        if schema.get("additionalProperties") is not False:
            errs.append(f"{path}: object phải đặt additionalProperties=false")
        props = schema.get("properties", {})
        for name, sub in props.items():
            errs += check_schema(sub, f"{path}.{name}")
        missing = [r for r in schema.get("required", []) if r not in props]
        if missing:
            errs.append(f"{path}: required không có trong properties: {missing}")
    if t == "array":
        errs += check_schema(schema.get("items"), f"{path}[]")
    return errs


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Danh sách lỗi (rỗng = hợp lệ). bool không được tính là integer/number."""
    errs: list[str] = []
    t = schema.get("type")
    py = _TYPES[t]
    if isinstance(value, bool) and t in ("integer", "number"):
        return [f"{path}: cần {t}, nhận boolean"]
    if not isinstance(value, py):
        return [f"{path}: cần {t}, nhận {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: giá trị {value!r} không thuộc {schema['enum']}")
    if t == "object":
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in value:
                errs.append(f"{path}: thiếu {r!r}")
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: trường lạ {k!r}")
    elif t == "array":
        for i, item in enumerate(value):
            errs += validate(item, schema["items"], f"{path}[{i}]")
    return errs
