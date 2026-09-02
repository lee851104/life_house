"""Pure risk primitives; I/O and spatial indexes remain outside these functions.

這裡是「風險怎麼算」的單一定義。核心.py（查詢與基準）、建立索引.py（建庫）
都必須呼叫這裡的函式，不可以自己再寫一份——同一條公式寫兩份的下場，這個
專案已經付過兩次代價：

  ‧ 死亡權重從 20 改成 4 時，核心.py 的 docstring 沒跟著改，說明與實際
    行為差了 5 倍。
  ‧ 平交道事故：建立索引.py 排除、前端排除，核心.py 沒排除，於是分數與
    圈內件數含有地圖上畫不出來的事故。

函式保持無 I/O、無空間索引，才能被 tests/ 直接驗證。
"""
import numpy as np


def epanechnikov_weight(distance_m: float, radius_m: float) -> float:
    """Return a bounded distance weight for one observation."""
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    if distance_m >= radius_m:
        return 0.0
    return max(0.0, 1.0 - (distance_m / radius_m) ** 2)


def epanechnikov_weights(distances_m, radius_m: float) -> np.ndarray:
    """epanechnikov_weight 的向量版；核心.py 的分子與分母都走這一支。

    分子（事故嚴重度）與分母（路網長度）必須用同一個核，risk 才是
    「每公里有效路網的事故負擔」而不是尺度不一致的比值。共用這支函式就是
    在結構上保證這件事，不必靠兩處註解互相提醒。
    """
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    d = np.asarray(distances_m, dtype=np.float64)
    return np.clip(1.0 - (d / radius_m) ** 2, 0.0, None)


def accident_severity(fatalities, fatality_weight: float):
    """Map an accident's fatality count to its configured risk weight.

    fatalities 可以是純量或 numpy 陣列。權重值來自 configs/analysis.yaml 的
    fatality_weight（目前為 4：A2 = 1，A1 通常死亡 1 人 → 5）。
    """
    return 1.0 + fatalities * fatality_weight


def is_level_crossing(accident_type: str, road_type: str = "") -> bool:
    """平交道事故。

    號誌環境與肇因結構和一般路口不同，且鐵路平交道不是「走路生活圈」要
    描述的對象，主要分數與地圖一律不納入。
    """
    return "平交道" in (accident_type or "") or "平交道" in (road_type or "")


def is_pedestrian_accident(accident_type: str, parties: str,
                           road_type: str = "") -> bool:
    """官方事故型態為「人與車」，或當事車種字串含「行人」；平交道除外。

    parties 是「、」連接的當事車種字串，與 事故索引.db 的 parties 欄同形。
    建立索引.py 與 核心.py 都呼叫這一支，兩邊對「什麼算行人事故」的認定
    才不會分岔。
    """
    if is_level_crossing(accident_type, road_type):
        return False
    return (accident_type or "") == "人與車" or "行人" in (parties or "")

# 政府資料的「當事者區分-子類別名稱-車種」欄沒有「大型車」這個統一說法，
# 而是分散在十幾個具體車種名稱裡。原本只比對「大客車／大貨車／聯結車」，
# 於是遊覽車、民營客運、民營公車、公營公車、公營客運、拖車全部漏掉——
# 桃園市這些加起來有一千多筆，而「大型車涉入」是佔 15% 權重的計分因子。
#
# 刻意用「大貨車」而不是「貨車」：小貨車（自用-小貨車(含客、貨兩用)、
# 租賃小貨車）不是大型車，比對「貨車」會把它們一起吃進來。
LARGE_VEHICLE_WORDS = (
    "大客車", "大貨車", "聯結車", "曳引車", "拖車",
    "公車", "客運", "遊覽車",
)


def involves_large_vehicle(parties: str) -> bool:
    """當事車種字串裡有沒有大型車。"""
    text = parties or ""
    return any(word in text for word in LARGE_VEHICLE_WORDS)
