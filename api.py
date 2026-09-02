# -*- coding: utf-8 -*-
"""Life House 安居指數 後端 API。

    POST /api/v3/analyze        走路生活圈分析
    GET  /api/v3/intersection   單一路口的個別事故點
    GET  /api/v3/meta           資料期間與筆數
    GET  /                      index.html（同源提供，免處理 CORS）

啟動
    .venv\\Scripts\\python.exe -m uvicorn api:app --port 8000
    → http://127.0.0.1:8000

回應結構刻意對齊 index.html 的示範資料，前端除了 API_BASE 之外不需要改。
"""
import os
import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import 地名查詢 as geoq
import 核心 as core
from src.features.risk import involves_large_vehicle

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "index.html")

SHOW_X = 12          # 回傳（並畫在地圖上）的路口數上限
HI_COUNT = 6         # 行人事故路口分布約第 95 百分位；與前端 tierOf() 一致
NEAR_HOT = 300.0     # 「鄰近事故集中路口」的距離門檻（公尺）
# 資料窗長度的中文標籤，由 configs/analysis.yaml 的 months 推導。
# 寫死害過一次：資料窗從 2 年改成 5 年時，前端 12 處都改了，只有 risk_factors()
# 裡的兩句沒改，UI 會拿五年的件數配上「近兩年」的說明。
_CN_NUM = "零一二三四五六七八九十"
_years = round(core.MONTHS / 12)
# 用中文數字，跟 index.html 的 12 處「近五年」對齊。寫成「近5年」不會出錯，
# 但同一個畫面上兩種數字寫法並存，看起來就是沒對過稿。
WINDOW_LABEL = "近%s年" % (_CN_NUM[_years] if _years <= 10 else _years)
# 尖峰時段的常數隨 ratios() 一起移除：時段篩選由前端送 time_start/time_hours
# 決定，後端只需要「夜間」這一個固定區間來算 risk_factors 的夜間事故占比。
NIGHT_H = set(range(18, 24)) | set(range(0, 6))   # 夜間 18–06
D: core.Data = None
GEO: geoq.Geo = None


# ------------------------------------------------------------------ 啟動
def _load():
    """索引、路網、基準一次載入常駐記憶體，之後每次查詢都是 numpy 運算。"""
    global D, GEO
    if D is not None:
        return
    t0 = time.time()
    D = core.Data()
    if D.ref is None:
        raise RuntimeError("找不到 基準.npz，請先執行： python 建立基準.py")
    # 地名索引是選配：沒有它只是不能打字搜地址，地圖點選照常運作
    GEO = geoq.Geo()
    print("[Life House] 載入完成 %.1fs ｜ 事故 %s 件 ‧ 路口 %s 處 ‧ 步行網 %.0f km ｜ 地名 %s"
          % (time.time() - t0, D.meta["accidents"], D.meta["intersections"],
             D.w_len.sum() / 1000,
             ("%d 筆" % GEO.n) if GEO.ok else "未建立"))


@asynccontextmanager
async def lifespan(_app):
    _load()
    yield


app = FastAPI(title="Life House 安居指數 API", version="3.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def on_invalid(_req, exc):
    """pydantic 的 422 也要用前端認得的 {error:{code,message}} 形狀。

    FastAPI 預設回 {"detail":[…]}，前端的 `if (d && d.error)` 攔不到，會直接
    把它當成分析結果丟進 UI.render()，然後在 d.score 上炸 TypeError。
    """
    loc = ".".join(str(x) for x in exc.errors()[0].get("loc", [])[1:])
    # 訊息會直接顯示給使用者（前端以 error.message 為優先），不要把 pydantic
    # 的英文原文倒出去。
    return fail("DATA_UNAVAILABLE",
                "請求參數不正確%s，請重新選點。" % ("（%s）" % loc if loc else ""), 422)


# ------------------------------------------------------------------ 請求
class Analyze(BaseModel):
    mode: str = Field("walk", pattern="^walk$")
    # ge/le 不只是擋離譜的值，主要是擋 nan 與 inf：pydantic 預設接受 "nan"
    # 字串，pyproj 轉換也不會拋錯，一路傳到 cKDTree 才炸
    # 「'x' must be finite」，變成沒有訊息的 500。nan 與所有比較都回 False，
    # 有了 ge/le 就會在驗證階段被擋下來，回前端認得的 422。
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    time_start: int = Field(0, ge=0, le=23)
    time_hours: int = Field(24, ge=1, le=24)


# ------------------------------------------------------------------ 小工具
def fail(code, message, status=400):
    """前端 UI.error 認得的錯誤形狀：{error:{code,message}}。

    它已經備好 OUT_OF_COVERAGE / INSUFFICIENT_DATA / DATA_UNAVAILABLE
    的中文文案，照這個 code 回就會顯示正確訊息。
    """
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}})


def trend_of(gidx):
    mi = D.g_mi[gidx]
    n = np.bincount(mi[mi >= 0], minlength=core.MONTHS)[:core.MONTHS]
    # 固定資料窗為 2021-07～2026-06，共 60 個完整月份。
    return [{"ym": s, "n": int(v), "partial": False}
            for s, v in zip(D.ym_labels, n)]


def confidence(n):
    return ("high" if n >= 30 else "mid" if n >= 12 else "low")


def hour_mask(start=0, hours=24):
    """選取連續時段；以模數處理 22:00–06:00 這類跨午夜範圍。"""
    if hours >= 24:
        return np.ones(D.g_hour.shape, dtype=bool)
    return ((D.g_hour.astype(np.int16) - int(start)) % 24) < int(hours)


def time_label(start, hours):
    if hours >= 24:
        return "全天"
    end = (start + hours) % 24
    return "%02d:00–%s%02d:00" % (start, "隔日 " if start + hours >= 24 else "", end)


def intersection_stats(mask):
    """依查詢時段即時計算各路口的件數與行人事故負擔。"""
    size = len(D.x_info)
    xids = D.g_xid[mask]
    count = np.bincount(xids, minlength=size).astype(np.int32)
    ped_mask = mask & D.g_ped
    a1_mask = mask & D.g_a1ped
    ped_count = np.bincount(D.g_xid[ped_mask], minlength=size).astype(np.int32)
    a1_count = np.bincount(D.g_xid[a1_mask], minlength=size).astype(np.int32)
    ped_weight = np.bincount(
        D.g_xid[ped_mask], weights=D.g_sev[ped_mask], minlength=size)
    return count, ped_count, a1_count, ped_weight


def pedestrian_indices(gidx):
    """只保留官方事故型態為「人與車」的事故。"""
    return gidx[D.g_ped[gidx]]


def pedestrian_rank_map(rows, stats=None):
    """路口行人事故負擔在本次 500m 生活圈內的相對百分位。"""
    if not rows:
        return {}
    xids = np.asarray([int(D.x_keep[k]) for k, _dist in rows], dtype=np.int64)
    ped_count_all = D.x_ped_cnt if stats is None else stats[1]
    a1_count_all = D.x_a1ped_cnt if stats is None else stats[2]
    ped_weight_all = D.x_ped_weight if stats is None else stats[3]
    weights = ped_weight_all[xids]
    ranked = np.sort(weights)
    out = {}
    for xid, weight in zip(xids, weights):
        ped_count = int(ped_count_all[xid])
        a1_count = int(a1_count_all[xid])
        if ped_count:
            lo = float(np.searchsorted(ranked, weight, side="left"))
            hi = float(np.searchsorted(ranked, weight, side="right"))
            higher_than = int(round(100.0 * (lo + hi) / (2.0 * len(ranked))))
            higher_than = max(1, min(99, higher_than))
            top_percent = max(1, 100 - higher_than)
        else:
            higher_than, top_percent = 0, None
        out[int(xid)] = {
            "higher_than_percent": higher_than,
            "top_percent": top_percent,
            "basis_intersections": int(len(ranked)),
            "pedestrian_accidents": ped_count,
            "a1_pedestrian_accidents": a1_count,
            "severity_weight": round(float(weight), 1),
        }
    return out


def points_of(xid, limit=400, mask=None):
    """某路口的個別事故點；資料源已去識別化，回傳公開的原始座標。"""
    selected = D.g_xid == xid
    if mask is not None:
        selected &= mask
    sel = np.flatnonzero(selected)[:limit]
    out = []
    for i in sel:
        cat, rtype, atype, cause, parties, ymd, hms = D.a_txt[D.g_ix[i]]
        out.append({
            "lat": float(D.g_lat[i]), "lon": float(D.g_lon[i]),
            "occurred_at": "%s-%s-%s %s:%s" % (ymd[:4], ymd[4:6], ymd[6:8],
                                               hms[:2], hms[2:4]),
            "category": cat, "parties": parties,
            "fatalities": int(D.g_fat[i]), "injuries": int(D.g_inj[i]),
            "road_type": rtype, "accident_type": atype, "main_cause": cause,
        })
    return out


def pack_x(rows, with_points=True, ped_ranks=None, stats=None, mask=None):
    """rows = [(x_keep 內的位置, 距離公尺)]"""
    out = []
    # 第一層圓圈只顯示行人事故件數；全部事故仍保留在 points 供第二層算占比。
    counts = D.x_ped_cnt if stats is None else stats[1]
    for k, dist in rows:
        xi = int(D.x_keep[k])
        lat, lon, name, cls = D.x_info[xi]
        cnt = int(counts[xi])
        item = {
            "lat": lat, "lon": lon, "count": cnt, "dist": int(round(dist)),
            "name": name, "cls": cls,
            "detail": "%d 件 ‧ 距離 %d m ‧ %s" % (cnt, round(dist), cls),
        }
        if ped_ranks is not None:
            item["pedestrian_rank"] = ped_ranks.get(xi)
        if with_points:
            item["points"] = points_of(xi, mask=mask)
        out.append(item)
    return out


def meta_block():
    return {"range": {"from": D.meta["range_from"], "to": D.meta["range_to"]},
            "source": D.meta["source"]}


# ------------------------------------------------------------------ 走路
def analyze_walk(lat, lon, time_start=0, time_hours=24):
    x, y = D.to_xy(lat, lon)
    mask = hour_mask(time_start, time_hours)
    selected_ped = mask & D.g_ped
    selected_share = float(D.g_sev[selected_ped].sum() /
                           max(D.g_sev[D.g_ped].sum(), 1.0))
    risk, n, wsum, km, gidx = D.walk_risk(x, y, accident_mask=mask)
    # 先除以全市同時段事故嚴重度占比，再套用全天基準；這樣 1 小時的件數不會
    # 因觀測時間較短而被誤判成特別安全。
    normalized_wsum = wsum / max(selected_share, 1e-6)
    score = D.percentile(D.shrink(normalized_wsum, km, "walk"), km, "walk")
    local_pct, local_candidates, local_regions = D.local_walk_percentile(
        x, y, accident_mask=mask, prior_scale=selected_share)

    ped_gidx = pedestrian_indices(gidx)
    fat = int(D.g_fat[ped_gidx].sum()) if ped_gidx.size else 0
    inj = int(D.g_inj[ped_gidx].sum()) if ped_gidx.size else 0

    xstats = intersection_stats(mask)
    kk = D.x_tree.query_ball_point([x, y], core.R_WALK)
    rows = []
    for k in kk:
        d = float(np.hypot(D.x_xy[D.x_keep[k], 0] - x, D.x_xy[D.x_keep[k], 1] - y))
        if xstats[1][D.x_keep[k]] > 0:
            rows.append((k, d))
    ped_ranks = pedestrian_rank_map(rows, stats=xstats)
    # 第一層只顯示行人事故路口，並依時間衰減後的行人事故負擔排序。
    rows.sort(key=lambda r: (-xstats[3][D.x_keep[r[0]]],
                             -xstats[0][D.x_keep[r[0]]]))
    # 距離門檻要跟 pack_x 顯示的值用同一個四捨五入，否則 300.44 m 的路口
    # popup 寫「距離 300 m」，摘要卻不把它算進「300 公尺內的熱點」。
    hot = [r for r in rows
           if round(r[1]) <= NEAR_HOT and xstats[1][D.x_keep[r[0]]] >= HI_COUNT]
    xs = pack_x(rows[:SHOW_X], ped_ranks=ped_ranks, stats=xstats, mask=mask)

    plain = "這裡走路，比桃園同類地區 <b>%d%%</b> 的地方安全。" % score
    plain += (("<br>此位置的歷史事故風險低於地圖中附近 <b>%d%%</b> 的比較格；底色顯示各格位置。"
               % local_pct)
              if local_candidates else
              "<br>附近步行路網資料不足，暫不提供局部安全百分位。")

    hotN = len(hot)
    return {
        "mode": "walk", "demo": False,
        "score": {"value": score, "plain": plain, "confidence": confidence(n),
                  "confidence_reason": "分數依圈內 %d 件行人事故計算" % n,
                  "total_accidents": int(n),
                  "local_percentile": local_pct,
                  "local_candidates": local_candidates,
                  "local_grid_m": int(core.LOCAL_RANK_GRID),
                  "local_regions": local_regions},
        "stats": {"accidents": int(ped_gidx.size), "fatalities": fat, "injuries": inj,
                  "scope": "人與車事故"},
        "accident_types": accident_types_of(gidx),
        "factors": risk_factors(gidx, score, hotN),
        "intersections": xs,
        "trend": trend_of(ped_gidx),
        "time_filter": {"start": int(time_start), "hours": int(time_hours),
                        "end": int((time_start + time_hours) % 24),
                        "crosses_midnight": bool(time_hours < 24 and time_start + time_hours >= 24),
                        "label": time_label(time_start, time_hours)},
        "data_meta": dict(meta_block(), exposure_km=round(km, 1),
                          risk=round(risk, 2),
                          band=D.band_of(km, "walk") + 1),
    }


def clamp_score(value):
    return int(round(max(0, min(100, value))))


def accident_types_of(gidx):
    """第一層只呈現行人事故的 A1／A2 嚴重程度組成。"""
    specs = [
        ("ped", "🔴", "A1 死亡事故", "造成人員當場或 24 小時內死亡"),
        ("vehicle", "🟠", "A2 受傷事故", "造成人員受傷或超過 24 小時死亡"),
    ]
    counts = {key: 0 for key, *_rest in specs}
    for i in gidx:
        category = str(D.a_txt[D.g_ix[i]][0] or "")
        counts["ped" if category.startswith("A1") else "vehicle"] += 1
    total = sum(counts.values())
    shares = {key: 0 for key in counts}
    if total:
        raw = {key: 100 * value / total for key, value in counts.items()}
        shares = {key: int(value) for key, value in raw.items()}
        remainder = 100 - sum(shares.values())
        order = sorted(counts, key=lambda key: (raw[key] - shares[key], counts[key]), reverse=True)
        for key in order[:remainder]:
            shares[key] += 1
    return [
        {"cls": key, "icon": icon, "label": label, "note": note,
         "count": counts[key], "share": shares[key]}
        for key, icon, label, note in specs
    ]


def risk_factors(gidx, score, hot_n):
    """把事故資料可直接支持的原因拆成可讀指標。

    不以事故資料猜測人行道、號誌等尚未收錄的道路設施；「行人涉入」與
    「大型車涉入」只描述本次範圍內的事故紀錄。
    """
    n = max(1, int(gidx.size))
    parties = [D.a_txt[D.g_ix[i]][4] or "" for i in gidx]
    ped = sum("行人" in s for s in parties)
    large = sum(involves_large_vehicle(s) for s in parties)
    night = float(np.isin(D.g_hour[gidx], list(NIGHT_H)).mean()) if gidx.size else 0.0

    factors = [
        {"icon": "💥", "label": "事故風險", "weight": 30, "score": clamp_score(score),
         "reason": "相較桃園市同類地區的加權事故風險"},
        {"icon": "🚦", "label": "路口風險", "weight": 22,
         "score": clamp_score(90 - hot_n * 14),
         "reason": ("300 公尺內有 %d 處事故集中的路口" % hot_n if hot_n
                    else "300 公尺內未見事故集中的路口")},
        {"icon": "🚚", "label": "大型車涉入", "weight": 15,
         "score": clamp_score(82 - large / n * 230),
         "reason": "%s %d／%d 件事故有大型車涉入" % (WINDOW_LABEL, large, int(gidx.size))},
        {"icon": "🌙", "label": "夜間事故", "weight": 13,
         "score": clamp_score(84 - night * 70),
         "reason": "夜間（18–06）事故占 %d%%" % round(night * 100)},
        {"icon": "🚶", "label": "行人涉入", "weight": 20,
         "score": clamp_score(88 - ped / n * 260),
         "reason": "%s %d／%d 件事故有行人涉入" % (WINDOW_LABEL, ped, int(gidx.size))},
    ]
    for item in factors:
        item["tone"] = "bad" if item["score"] < 60 else "mid" if item["score"] < 75 else "ok"
        item["status"] = "風險偏高" if item["score"] < 60 else "需注意" if item["score"] < 75 else "尚可"
    return factors


# ------------------------------------------------------------------ 路由
@app.post("/api/v3/analyze")
def analyze(req: Analyze):
    if req.lat is None or req.lon is None:
        return fail("DATA_UNAVAILABLE", "walk 模式需要 lat / lon")
    if not D.inside(req.lat, req.lon):
        return fail("OUT_OF_COVERAGE", "選定位置不在桃園市範圍內。")
    return analyze_walk(req.lat, req.lon, req.time_start, req.time_hours)


@app.get("/api/v3/intersection")
def intersection(lat: float = Query(..., ge=-90, le=90),
                 lon: float = Query(..., ge=-180, le=180),
                 r: float = Query(60.0, ge=5, le=300),
                 time_start: int = Query(0, ge=0, le=23),
                 time_hours: int = Query(24, ge=1, le=24)):
    x, y = D.to_xy(lat, lon)
    kk = D.x_tree.query_ball_point([x, y], r)
    if not kk:
        return []
    k = max(kk, key=lambda i: D.x_cnt[D.x_keep[i]])
    return points_of(int(D.x_keep[k]), mask=hour_mask(time_start, time_hours))


@app.get("/api/v3/geocode")
def geocode(q: str = Query(..., min_length=1, max_length=60),
            limit: int = Query(8, ge=1, le=20),
            lat: float | None = Query(None, ge=-90, le=90),
            lon: float | None = Query(None, ge=-180, le=180)):
    """地址／地標／路口的模糊搜尋，給前端的輸入框做 autocomplete。

    lat/lon 是目前的地圖中心（選填）。桃園 12 個區裡有 9 個都有中山路，
    有了它就能把使用者眼前那一條排前面。
    """
    if GEO is None or not GEO.ok:
        return fail("DATA_UNAVAILABLE",
                    "地名索引尚未建立，請執行： python 建立地名.py", 503)
    near = D.to_xy(lat, lon) if (lat is not None and lon is not None) else None
    return {"q": q, "results": GEO.search(q, limit, near)}


@app.get("/api/v3/meta")
def meta():
    return dict(meta_block(),
                accidents=int(D.meta["accidents"]),
                intersections=int(D.meta["intersections"]),
                walk_network_km=round(float(D.w_len.sum()) / 1000, 1),
                # 是各層樣本數的總和，不是 len(dict)——後者永遠回 3
                # （edges / bands / med 三個 key）。
                baseline_walk_cells=sum(len(b) for b in D.ref["walk"]["bands"]),
                geocode=({"places": GEO.n, "addresses": GEO.n_addr}
                         if GEO and GEO.ok else None))


@app.get("/")
def root():
    return FileResponse(HTML, media_type="text/html; charset=utf-8")
