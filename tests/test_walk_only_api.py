from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from src.serving import api


def test_analyze_request_accepts_walk_only():
    assert api.Analyze(mode="walk", lat=24.9, lon=121.2).mode == "walk"
    with pytest.raises(ValidationError):
        api.Analyze(mode="route", lat=24.9, lon=121.2)


def test_time_filter_validates_one_to_twenty_four_hours():
    assert api.Analyze(mode="walk", lat=24.9, lon=121.2,
                       time_start=22, time_hours=8).time_hours == 8
    with pytest.raises(ValidationError):
        api.Analyze(mode="walk", lat=24.9, lon=121.2, time_hours=0)
    with pytest.raises(ValidationError):
        api.Analyze(mode="walk", lat=24.9, lon=121.2, time_start=24)


def test_hour_mask_supports_ranges_crossing_midnight(monkeypatch):
    fake = SimpleNamespace(g_hour=np.array([21, 22, 23, 0, 5, 6, 7], dtype=np.int8))
    monkeypatch.setattr(api, "D", fake)

    selected = api.hour_mask(22, 8)

    assert selected.tolist() == [False, True, True, True, True, False, False]
    assert api.hour_mask(7, 24).all()


def test_points_use_public_original_coordinates(monkeypatch):
    fake = SimpleNamespace(
        g_xid=np.array([7]),
        g_ix=np.array([0]),
        g_lat=np.array([24.912345]),
        g_lon=np.array([121.298765]),
        g_fat=np.array([0]),
        g_inj=np.array([1]),
        a_txt=[("A2", "交岔路", "人與車", "未注意車前狀況", "行人",
               "20260801", "123000")],
    )
    monkeypatch.setattr(api, "D", fake)

    point = api.points_of(7)[0]

    assert point["lat"] == 24.912345
    assert point["lon"] == 121.298765
    assert point["accident_type"] == "人與車"


def test_pedestrian_indices_use_official_accident_type(monkeypatch):
    fake = SimpleNamespace(
        g_ix=np.array([0, 1, 2]),
        g_ped=np.array([True, False, False]),
        a_txt=[
            ("A2", "交岔路", "人與車", "原因", "行人", "20260801", "120000"),
            ("A2", "交岔路", "車與車", "原因", "行人", "20260801", "120000"),
            ("A2", "交岔路", "車輛本身", "原因", "", "20260801", "120000"),
        ],
    )
    monkeypatch.setattr(api, "D", fake)

    selected = api.pedestrian_indices(np.array([0, 1, 2]))

    assert selected.tolist() == [0]


def test_pedestrian_rank_is_local_percentile_and_keeps_zero_separate(monkeypatch):
    fake = SimpleNamespace(
        x_keep=np.array([0, 1, 2]),
        x_ped_weight=np.array([0.0, 1.0, 21.0]),
        x_ped_cnt=np.array([0, 1, 1]),
        x_a1ped_cnt=np.array([0, 0, 1]),
    )
    monkeypatch.setattr(api, "D", fake)

    ranks = api.pedestrian_rank_map([(0, 10.0), (1, 20.0), (2, 30.0)])

    assert ranks[0]["top_percent"] is None
    assert ranks[1]["higher_than_percent"] == 50
    assert ranks[2]["top_percent"] == 17
    assert ranks[2]["a1_pedestrian_accidents"] == 1


def test_first_layer_marker_count_uses_pedestrian_accidents(monkeypatch):
    fake = SimpleNamespace(
        x_keep=np.array([0]),
        x_info=[(24.95, 121.24, "測試路口", "市區道路")],
        x_ped_cnt=np.array([2]),
    )
    monkeypatch.setattr(api, "D", fake)
    stats = (
        np.array([9]),   # 全部事故只供第二層計算占比
        np.array([2]),   # 第一層圓圈顯示行人事故
        np.array([0]),
        np.array([2.0]),
    )

    item = api.pack_x([(0, 18.0)], with_points=False, stats=stats)[0]

    assert item["count"] == 2
    assert item["detail"].startswith("2 件")


def test_window_label_uses_chinese_numeral_like_the_frontend():
    """index.html 有 12 處寫「近五年」；後端輸出「近5年」會讓同一畫面兩種寫法並存。"""
    assert api.WINDOW_LABEL == "近五年"


@pytest.fixture
def busy_intersection(monkeypatch):
    # 行人事故全部排在第 400 筆以後，避免測試只驗到原本可見的明細。
    rows = ([('A2', '交岔路', '車與車', '原因', '')] * 410
            + [('A1', '交岔路', '人與車', '未禮讓行人', '行人')] * 420
            + [('A2', '交岔路', '車輛本身', '原因', '')]
            + [('A2', '交岔路', '未知', '原因', '')]
            + [('A1', '平交道', '人與車', '原因', '行人')])
    n = len(rows)
    fake = SimpleNamespace(
        g_xid=np.zeros(n, dtype=int), g_ix=np.arange(n),
        g_lat=np.full(n, 24.95), g_lon=np.full(n, 121.24),
        g_fat=np.zeros(n, dtype=int), g_inj=np.ones(n, dtype=int),
        a_txt=[(*r, '20260801', '220000') for r in rows],
        g_hour=np.array([12] * 410 + [22] * (n - 410)),
        x_keep=np.array([0]), x_info=[(24.95, 121.24, '測試路口', '市區道路')],
        x_ped_cnt=np.array([420]),
    )
    monkeypatch.setattr(api, 'D', fake)
    return fake


def test_busy_intersection_has_full_statistics_and_pedestrian_points(busy_intersection):
    item = api.pack_x([(0, 10.0)])[0]
    assert item['summary'] == {
        'total': 832, 'pedestrian': 420,
        'types': {'ped': 420, 'vehicle': 410, 'single': 1, 'other': 1},
    }
    assert len(item['points']) == 420
    assert all(p['main_cause'] == '未禮讓行人' for p in item['points'])
    assert item['count'] == item['summary']['pedestrian']


def test_full_intersection_statistics_respect_time_mask(busy_intersection):
    item = api.intersection_details(0, mask=api.hour_mask(22, 8))
    assert item['summary']['total'] == 422
    assert item['summary']['types']['vehicle'] == 0
    assert len(item['points']) == item['summary']['pedestrian'] == 420


def test_intersection_statistics_handle_no_matching_accidents(busy_intersection):
    item = api.intersection_details(0, mask=api.hour_mask(6, 1))
    assert item['summary']['total'] == 0
    assert sum(item['summary']['types'].values()) == 0
    assert item['points'] == []


def test_factors_report_observations_not_invented_scores(monkeypatch):
    fake = SimpleNamespace(
        g_hour=np.array([17, 18, 23, 0, 5, 6]), g_ix=np.arange(6),
        a_txt=[('', '', '', '', '行人、大客車', '', '')] * 6,
    )
    monkeypatch.setattr(api, 'D', fake)
    factors = {f['key']: f for f in api.risk_factors(np.arange(6), 2)}
    assert factors['night']['count'] == 4
    assert factors['night']['share'] == 66.7
    assert factors['large_vehicle']['count'] == 6
    assert factors['nearby_hotspots']['count'] == 2
    assert all('score' not in f and 'weight' not in f for f in factors.values())
    selected = api.risk_factors(np.array([1, 2]), 0)
    assert next(f for f in selected if f['key'] == 'night')['share'] == 100
    empty = api.risk_factors(np.array([], dtype=int), 0)
    assert all(f['count'] == 0 and f['share'] is None for f in empty)


def test_methodology_uses_current_comparison_group_and_config(monkeypatch):
    fake = SimpleNamespace(ref={'walk': {'bands': [np.zeros(2), np.zeros(5)]}},
                           band_of=lambda km, mode: 1)
    monkeypatch.setattr(api, 'D', fake)
    method = api.score_methodology(3.456, 17)
    assert method['city_candidates'] == 5
    assert method['city_band'] == method['city_bands'] == 2
    assert method['local_candidates'] == 17
    assert method['effective_network_km'] == 3.46
    assert method['fatality_weight'] == api.core.FATAL_W
    thresholds = method['confidence_thresholds']
    assert api.confidence(thresholds['high']) == 'high'
    assert api.confidence(thresholds['high'] - 1) == 'mid'
    assert api.confidence(thresholds['mid'] - 1) == 'low'
