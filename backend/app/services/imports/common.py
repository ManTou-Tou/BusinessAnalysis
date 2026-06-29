"""Excel 导入公共工具（Phase 9）：表头映射、单元格强转、错误收集。

约定：表头取首行，列名 **小写去空格** 后映射到字段。强转失败抛 ValueError（由驱动
捕获为行级错误）。金额一律 Decimal，禁 float。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

# xlsx 本质是 zip，文件头魔数（入口与扩展名双校验）。
MAGIC_ZIP = b"PK\x03\x04"


class RowErrorCollector:
    """行级错误收集：带行号，明细上限 cap，超限只计总数（不静默吞）。"""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.errors: list[dict] = []
        self.count = 0

    def add(self, row_no: int, msg: str) -> None:
        self.count += 1
        if len(self.errors) < self.cap:
            self.errors.append({"row": row_no, "msg": msg})


def map_header(header_cells) -> dict[str, int]:
    """表头单元格 → {列名(小写去空格): 列索引}。重复列名保留首个。"""
    mapping: dict[str, int] = {}
    for idx, val in enumerate(header_cells):
        if val is None:
            continue
        key = str(val).strip().lower()
        if key and key not in mapping:
            mapping[key] = idx
    return mapping


def missing_columns(mapping: dict[str, int], required) -> list[str]:
    return [c for c in required if c not in mapping]


def raw(row, mapping: dict[str, int], name: str):
    """取某列原始单元格值（缺列/越界返回 None）。"""
    idx = mapping.get(name)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def opt_str(v) -> str | None:
    if _blank(v):
        return None
    return str(v).strip()


def req_str(v, field: str) -> str:
    if _blank(v):
        raise ValueError(f"{field} is required")
    return str(v).strip()


def req_decimal(v, field: str) -> Decimal:
    if _blank(v):
        raise ValueError(f"{field} is required")
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} is not a number: {v!r}")


def opt_decimal(v, default: Decimal) -> Decimal:
    if _blank(v):
        return default
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"not a number: {v!r}")


def opt_int(v, field: str, default: int | None = None) -> int | None:
    if _blank(v):
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return int(float(v))  # 容忍 "3.0"
        except (ValueError, TypeError):
            raise ValueError(f"{field} is not an integer: {v!r}")


def req_int(v, field: str) -> int:
    out = opt_int(v, field, default=None)
    if out is None:
        raise ValueError(f"{field} is required")
    return out


def rating(v) -> int | None:
    if _blank(v):
        return None
    out = opt_int(v, "rating")
    if out is None or not (1 <= out <= 5):
        raise ValueError(f"rating must be 1..5: {v!r}")
    return out
