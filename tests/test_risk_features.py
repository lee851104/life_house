"""風險原始運算的測試。

這裡刻意同時驗證兩件事：
  1. src/features/risk.py 的純函式行為正確；
  2. 核心.py（正式查詢路徑）確實是呼叫這些函式，而不是自己抄了一份。

第 2 點是重點。這個檔案曾經只做第 1 點，於是 10 個測試全綠、但正式路徑上
的公式沒有任何一行被測到——死亡權重從 20 改成 4 時，核心.py 的說明沒跟著
改也沒有任何測試會紅。
"""
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.features.risk import (
    accident_severity,
    epanechnikov_weight,
    epanechnikov_weights,
    involves_large_vehicle,
    is_level_crossing,
    is_pedestrian_accident,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load(
    (ROOT / "configs" / "analysis.yaml").read_text(encoding="utf-8"))["analysis"]


# ----------------------------------------------------------------- 距離核
def test_epanechnikov_weight_is_zero_outside_radius():
    assert epanechnikov_weight(500, 500) == 0
    assert epanechnikov_weight(600, 500) == 0
    assert epanechnikov_weight(0, 500) == 1


def test_epanechnikov_weight_validates_radius():
    with pytest.raises(ValueError):
        epanechnikov_weight(1, 0)
    with pytest.raises(ValueError):
        epanechnikov_weights([1.0], 0)


def test_vectorised_kernel_matches_scalar_reference():
    """核心.py 的分子與分母都走向量版，它必須等於純量版的定義。"""
    distances = [0.0, 1.0, 123.4, 250.0, 499.9, 500.0, 700.0]
    expected = [epanechnikov_weight(d, 500.0) for d in distances]
    assert np.allclose(epanechnikov_weights(distances, 500.0), expected)


def test_vectorised_kernel_never_returns_negative_weight():
    """半徑外的距離必須是 0，不能是負數——負權重會把風險往下扣。"""
    assert (epanechnikov_weights([501.0, 900.0, 1e6], 500.0) == 0).all()


# ----------------------------------------------------------------- 嚴重度
def test_accident_severity_matches_configured_weight():
    """用 configs/analysis.yaml 的實際值驗證，設定改了但文件沒改就會紅。"""
    weight = CONFIG["fatality_weight"]
    assert accident_severity(0, weight) == 1.0          # A2
    assert accident_severity(1, weight) == 1.0 + weight  # A1 死亡 1 人
    assert accident_severity(1, weight) == 5.0, (
        "README 與 configs 都宣稱 A1 嚴重度為 5；改動權重時兩邊都要同步更新")


def test_accident_severity_accepts_arrays():
    assert np.allclose(accident_severity(np.array([0, 1, 2]), 4), [1.0, 5.0, 9.0])


# ------------------------------------------------------------- 行人判準
def test_pedestrian_predicate_accepts_official_type_and_parties():
    assert is_pedestrian_accident("人與車", "行人、自小客車")
    assert is_pedestrian_accident("車與車", "行人")      # 型態沒標但當事人有行人
    assert not is_pedestrian_accident("車與車", "自小客車、機車")
    assert not is_pedestrian_accident("車輛本身", "")


def test_pedestrian_predicate_excludes_level_crossings():
    """前端 isLevelCrossing() 不畫平交道事故，後端也必須不算。"""
    assert is_level_crossing("平交道事故", "")
    assert is_level_crossing("人與車", "平交道")
    assert not is_pedestrian_accident("平交道事故", "行人")
    assert not is_pedestrian_accident("人與車", "行人", "平交道")


# ------------------------------------------- 正式路徑確實共用同一份定義
def test_core_severity_delegates_to_shared_primitive():
    import 核心

    assert 核心.FATAL_W == CONFIG["fatality_weight"]
    assert np.allclose(核心.sev([0, 1, 2]),
                       accident_severity(np.array([0.0, 1.0, 2.0]), 核心.FATAL_W))


def test_core_radius_matches_config():
    import 核心

    assert 核心.R_WALK == CONFIG["walk_radius_m"]
    assert 核心.MONTHS == CONFIG["months"]


def test_index_builder_keeps_pedestrian_inside_truncated_parties():
    """parties 欄只存 4 種車種，含「行人」的必須排進去。

    核心.py 判斷行人事故只看得到這個字串；行人被截掉的話，那件事故在分數、
    圈內件數與地圖上會同時消失。
    """
    import 建立索引

    veh = ["自小客車", "普通重型機車", "自小貨車", "大客車", "行人"]
    text = 建立索引.parties_text(veh)
    assert "行人" in text
    assert len(text.split("、")) == 4
    assert is_pedestrian_accident("車與車", text)


def test_index_builder_and_core_agree_on_pedestrian_rule():
    import 建立索引

    crossing = {"atype": "人與車", "rtype": "平交道", "veh": ["行人"]}
    normal = {"atype": "人與車", "rtype": "交岔路", "veh": ["行人", "自小客車"]}
    assert not 建立索引.is_pedestrian_accident(crossing)
    assert 建立索引.is_pedestrian_accident(normal)


# ------------------------------------------------------- 大型車涉入
def test_large_vehicle_covers_buses_and_coaches():
    """公車與客運是大型車。原本只比對「大客車／大貨車／聯結車」，把它們全漏了。"""
    for parties in ["自用大客車", "營業用-大貨車", "聯結車", "曳引車",
                    "遊覽車", "民營客運", "公營客運", "民營公車", "公營公車",
                    "拖車(架)"]:
        assert involves_large_vehicle(parties), parties


def test_large_vehicle_does_not_match_small_trucks():
    """小貨車不是大型車——所以比對「大貨車」而不是「貨車」。"""
    for parties in ["自用-小貨車(含客、貨兩用)", "租賃小貨車", "普通重型",
                    "自用", "行人", "計程車", "微型電動二輪車", ""]:
        assert not involves_large_vehicle(parties), parties


def test_large_vehicle_matches_inside_joined_parties():
    assert involves_large_vehicle("行人、普通重型、民營公車")
    assert not involves_large_vehicle("行人、普通重型、自用")
