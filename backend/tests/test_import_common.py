"""导入公共工具单测（Phase 9）：表头映射 + 单元格强转 + 错误收集（纯函数，无 DB）。"""
from decimal import Decimal

import pytest

from app.services.imports import common as c


def test_map_header_case_and_space_insensitive():
    m = c.map_header([" SKU ", "Product_Name", None, "price", "sku"])
    assert m == {"sku": 0, "product_name": 1, "price": 3}  # 重复 sku 保留首个，None 跳过


def test_missing_columns():
    m = c.map_header(["sku", "price"])
    assert c.missing_columns(m, ("sku", "product_name", "price")) == ["product_name"]


def test_raw_handles_missing_and_oob():
    m = {"sku": 0, "price": 3}
    assert c.raw(("A",), m, "sku") == "A"
    assert c.raw(("A",), m, "price") is None  # 越界
    assert c.raw(("A",), m, "nope") is None  # 缺列


def test_req_and_opt_str():
    assert c.req_str("  x ", "f") == "x"
    assert c.opt_str("   ") is None
    assert c.opt_str(None) is None
    with pytest.raises(ValueError):
        c.req_str("  ", "f")


def test_decimals():
    assert c.req_decimal("12.50", "price") == Decimal("12.50")
    assert c.opt_decimal(None, Decimal("0")) == Decimal("0")
    assert c.opt_decimal("3", Decimal("0")) == Decimal("3")
    with pytest.raises(ValueError):
        c.req_decimal("abc", "price")
    with pytest.raises(ValueError):
        c.req_decimal(None, "price")


def test_ints_and_rating():
    assert c.req_int("3", "quantity") == 3
    assert c.opt_int("3.0", "stock") == 3  # 容忍 "3.0"
    assert c.opt_int(None, "stock", default=0) == 0
    assert c.rating("5") == 5
    assert c.rating(None) is None
    with pytest.raises(ValueError):
        c.rating("6")  # 越界
    with pytest.raises(ValueError):
        c.req_int(None, "quantity")


def test_row_error_collector_cap():
    col = c.RowErrorCollector(cap=2)
    for i in range(5):
        col.add(i, f"err{i}")
    assert col.count == 5  # 总数不静默
    assert len(col.errors) == 2  # 明细上限
    assert col.errors[0] == {"row": 0, "msg": "err0"}
