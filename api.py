# -*- coding: utf-8 -*-
"""陸安安居指數 後端 API。

    POST /api/v3/analyze        走路生活圈 / 騎車路線分析
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

import 核心 as core

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "index.html")

SHOW_X = 12          # 回傳（並畫在地圖上）的路口數上限
HI_COUNT = 15        # 與前端 tierOf() 的 md 門檻一致＝「高風險路口」
NEAR_HOT = 300.0     # 「鄰近事故集中路口」的距離門檻（公尺）
PEAK_H = {7, 8, 9, 17, 18, 19, 20}          # 尖峰 07–10、17–21
NIGHT_H = set(range(18, 24)) | set(range(0, 6))   # 夜間 18–06
STEP = 20.0          # 沿路線取樣間距（公尺）
RIDE_KMH = 24.0      # 市區機車均速，用來估行駛時間

D: core.Data = None


# ------------------------------------------------------------------ 啟動
def _load():
    """索引、路網、基準一次載入常駐記憶體，之後每次查詢都是 numpy 運算。"""
    global D
    if D is not None:
        return
    t0 = time.time()
    D = core.Data()
    if D.ref is None:
        raise RuntimeError("找不到 基準.npz，請先執行： python 建立基準.py")
    print("[陸安] 載入完成 %.1fs ｜ 事故 %s 件 ‧ 路口 %s 處 ‧ 步行網 %.0f km"
          % (time.time() - t0, D.meta["accidents"], D.meta["intersections"],
             D.w_len.sum() / 1000))


@asynccontextmanager
async def lifespan(_app):
    _load()
    yield


app = FastAPI(title="陸安安居指數 API", version="3.0", lifespan=lifespan)


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
    mode: str = Field(pattern="^(walk|route)$")
    lat: float | None = None
    lon: float | None = None
    # min/max_length 是必要的：少了它，from=[24.99] 會在 D.inside(p[0], p[1])
    # 炸出 IndexError → HTTP 500 純文字，前端 r.json() 解析失敗後只能顯示一句
    # 英文的 SyntaxError。
    from_: list[float] | None = Field(None, alias="from", min_length=2, max_length=2)
    to: list[float] | None = Field(None, min_length=2, max_length=2)
    # 只有機車路網（建立路網.py 的 ride_*），沒有第二種可選。收下卻忽略的話，
    # vehicle='bicycle' 會靜靜地拿到機車的結果。
    vehicle: str = Field("motorcycle", pattern="^motorcycle$")

    model_config = {"populate_by_name": True}


# ------------------------------------------------------------------ 小工具
def star(x):
    return max(0.5, min(5.0, round(x / 20 * 2) / 2))


def fail(code, message, status=400):
    """前端 UI.error 認得的錯誤形狀：{error:{code,message}}。

    它已經備好 OUT_OF_COVERAGE / NO_ROUTE / INSUFFICIENT_DATA /
    DATA_UNAVAILABLE 的中文文案，照這個 code 回就會顯示正確訊息。
    """
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}})


def ratios(gidx):
    """夜間佔比、尖峰佔比。樣本太少時退回全市平均，避免 1 件事故就 100%。"""
    if gidx.size < 8:
        h = D.g_hour
    else:
        h = D.g_hour[gidx]
    night = float(np.isin(h, list(NIGHT_H)).mean())
    peak = float(np.isin(h, list(PEAK_H)).mean())
    return night, peak


def trend_of(gidx):
    mi = D.g_mi[gidx]
    n = np.bincount(mi[mi >= 0], minlength=core.MONTHS)[:core.MONTHS]
    # 期間內每個月的資料都是完整的（A2 原始檔已到 2026-08），故一律 partial=false
    return [{"ym": s, "n": int(v), "partial": False}
            for s, v in zip(D.ym_labels, n)]


def confidence(n):
    return ("high" if n >= 30 else "mid" if n >= 12 else "low")


def points_of(xid, limit=400):
    """某路口的個別事故點。座標用 ±25m 偏移值，不外流真實位置。"""
    sel = np.flatnonzero(D.g_xid == xid)[:limit]
    out = []
    for i in sel:
        cat, rtype, cause, parties, ymd, hms = D.a_txt[D.g_ix[i]]
        out.append({
            "lat": float(D.g_jlat[i]), "lon": float(D.g_jlon[i]),
            "occurred_at": "%s-%s-%s %s:%s" % (ymd[:4], ymd[4:6], ymd[6:8],
                                               hms[:2], hms[2:4]),
            "category": cat, "parties": parties,
            "fatalities": int(D.g_fat[i]), "injuries": int(D.g_inj[i]),
            "road_type": rtype, "main_cause": cause,
        })
    return out


def pack_x(rows, with_points=True):
    """rows = [(x_keep 內的位置, 距離公尺)]"""
    out = []
    for k, dist in rows:
        xi = int(D.x_keep[k])
        lat, lon, name, cls = D.x_info[xi]
        cnt = int(D.x_cnt[xi])
        item = {
            "lat": lat, "lon": lon, "count": cnt, "dist": int(round(dist)),
            "name": name, "cls": cls,
            "detail": "%d 件 ‧ 距離 %d m ‧ %s" % (cnt, round(dist), cls),
            "rank": D.count_rank(cnt, cls),
        }
        if with_points:
            item["points"] = points_of(xi)
        out.append(item)
    return out


def meta_block():
    return {"range": {"from": D.meta["range_from"], "to": D.meta["range_to"]},
            "source": D.meta["source"]}


# ------------------------------------------------------------------ 走路
def analyze_walk(lat, lon):
    x, y = D.to_xy(lat, lon)
    risk, n, wsum, km, gidx = D.walk_risk(x, y)
    score = D.percentile(D.shrink(wsum, km, "walk"), km, "walk")

    fat = int(D.g_fat[gidx].sum()) if gidx.size else 0
    inj = int(D.g_inj[gidx].sum()) if gidx.size else 0
    night, peak = ratios(gidx)

    # 行人死亡單獨算，這樣「無行人死亡事故」這句話才站得住
    ped_fat = 0
    for i in gidx:
        if D.g_fat[i] > 0 and "行人" in (D.a_txt[D.g_ix[i]][3] or ""):
            ped_fat += int(D.g_fat[i])

    kk = D.x_tree.query_ball_point([x, y], core.R_WALK)
    rows = []
    for k in kk:
        d = float(np.hypot(D.x_xy[D.x_keep[k], 0] - x, D.x_xy[D.x_keep[k], 1] - y))
        rows.append((k, d))
    rows.sort(key=lambda r: -D.x_cnt[D.x_keep[r[0]]])
    # 距離門檻要跟 pack_x 顯示的值用同一個四捨五入，否則 300.44 m 的路口
    # popup 寫「距離 300 m」，摘要卻不把它算進「300 公尺內的熱點」。
    hot = [r for r in rows
           if round(r[1]) <= NEAR_HOT and D.x_cnt[D.x_keep[r[0]]] >= HI_COUNT]
    xs = pack_x(rows[:SHOW_X])

    plain = "這裡走路，比桃園同類地區 <b>%d%%</b> 的地方安全。" % score
    plain += ("<br>但 300 公尺內有 <b>%d 個</b>事故偏多的路口，過馬路要留意。" % len(hot)
              if hot else "<br>300 公尺內沒有事故特別集中的路口。")

    hotN = len(hot)
    life = [
        {"label": "🚶 步行族", "stars": star(score + (10 if ped_fat == 0 else -18)),
         "reason": ("500m 內近兩年無行人死亡事故" if ped_fat == 0
                    else "500m 內近兩年有 %d 位行人死亡" % ped_fat)},
        {"label": "🛵 機車通勤", "stars": star(score + (0.42 - peak) * 95),
         "reason": "尖峰時段（07–10、17–21）事故佔 %d%%" % round(peak * 100)},
        {"label": "👨‍👩‍👧 親子家庭",
         "stars": star(score + (6 if fat == 0 else -16) + (-7 * hotN if hotN else 10)),
         "reason": ("鄰近 %d 處事故集中路口，孩童步行需留意" % hotN if hotN
                    else "圈內無明顯事故集中路口")},
        {"label": "🌙 夜間活動", "stars": star(score + (0.32 - night) * 165),
         "reason": "夜間（18–06）事故佔 %d%%" % round(night * 100)},
        {"label": "🏃 日常戶外", "stars": star(score + (0.32 - night) * 75 + 6),
         "reason": "日間事故佔 %d%%，以此與行人風險推估" % round((1 - night) * 100)},
    ]

    return {
        "mode": "walk", "demo": False,
        "score": {"value": score, "plain": plain, "confidence": confidence(n),
                  "confidence_reason": "圈內 %d 件事故" % n},
        "stats": {"accidents": n, "fatalities": fat, "injuries": inj},
        "intersections": xs,
        "lifestyle": life,
        "trend": trend_of(gidx),
        "data_meta": dict(meta_block(), exposure_km=round(km, 1),
                          risk=round(risk, 2),
                          band=D.band_of(km, "walk") + 1),
    }


# ------------------------------------------------------------------ 路線
def route_path(a, b):
    """機車路網上的最短路徑。與 建立基準.py 共用 core.shortest_path。"""
    return D.shortest_path(a, b)


def route_error(frm, to):
    """路線走不通時，判斷是哪一種走不通。回傳 (code, message) 或 None。

    三種情況對使用者的意義完全不同，全都回「找不到可行路線，請換一個目的地」
    只會讓人一直換目的地卻不知道問題在哪：
      ‧ 附近根本沒有路網（山區）——換目的地沒用，要換起點
      ‧ 起訖吸到同一個節點——不是找不到，是根本沒有距離
      ‧ 真的不連通——這時才該說找不到路線
    """
    (sn, sd), (tn, td) = D.snap(D.to_xy(*frm)), D.snap(D.to_xy(*to))
    for node, dist, which in ((sn, sd, "起點"), (tn, td, "終點")):
        if node < 0:
            return ("OUT_OF_COVERAGE",
                    "%s附近 %d 公尺內沒有可通行的道路，請改點在路上的位置。"
                    % (which, round(dist)))
    if sn == tn:
        return ("NO_ROUTE", "起點與終點在同一個路口上，請把兩點拉開一些。")
    return None


def analyze_route(frm, to):
    a = D.to_xy(frm[0], frm[1])
    b = D.to_xy(to[0], to[1])
    r = route_path(a, b)
    if r is None:
        return None
    seq, length_m = r
    xy = D.n_xy[seq]
    km = max(length_m / 1000.0, 0.1)

    pts = core.sample_line(xy, STEP)
    hits = D.acc_tree.query_ball_point(pts, core.R_ROUTE, workers=-1)
    flat = [np.asarray(h, dtype=np.int64) for h in hits if len(h)]
    gidx = np.unique(np.concatenate(flat)) if flat else np.empty(0, dtype=np.int64)

    wsum = float(D.g_sev[gidx].sum()) if gidx.size else 0.0
    risk = wsum / km
    # 路線的「同類」＝沿線路網密度相近（市區幹道 vs 鄉間產業道路）
    probe = pts[::10] if len(pts) > 10 else pts
    dens = float(np.mean([D.ride_km(px, py) for px, py in probe]))
    # 收縮的證據量用路線長度；分層用沿線路網密度
    score = D.percentile(D.shrink(wsum, km, "ride", band_km=dens), dens, "ride")

    n = int(gidx.size)
    fat = int(D.g_fat[gidx].sum()) if n else 0
    inj = int(D.g_inj[gidx].sum()) if n else 0
    night, peak = ratios(gidx)

    # 沿線路口：離路線 45m 內
    kk = D.x_tree.query_ball_point(pts, 45.0, workers=-1)
    seen = {}
    for h in kk:
        for k in h:
            seen.setdefault(int(k), 0.0)
    rows = sorted(seen.keys(), key=lambda k: -D.x_cnt[D.x_keep[k]])
    hi = [k for k in rows if D.x_cnt[D.x_keep[k]] >= HI_COUNT]
    shown = min(SHOW_X, max(5, int(round(km))))
    xs = pack_x([(k, 0.0) for k in rows[:shown]])
    for it in xs:                      # 路線模式的 detail 不講距離
        it["detail"] = "%d 件事故 ‧ %s" % (it["count"], it["cls"])

    hiN = len(hi)
    worst = xs[0] if xs else None
    if hiN > 0 and worst:
        plain = ("這條 <b>%.1f km</b> 路線上有 <b>%d 個</b>高風險路口，"
                 "最需注意 <b>%s</b>（%d 件）。" % (km, hiN, worst["name"], worst["count"]))
    elif worst:
        plain = ("這條 <b>%.1f km</b> 路線<b>沒有明顯的高風險路口</b>，"
                 "沿線事故最多的是 %s（%d 件）。" % (km, worst["name"], worst["count"]))
    else:
        plain = "這條 <b>%.1f km</b> 路線沿線沒有事故集中的路口。" % km

    hi_per_km = hiN / max(km, 0.5)
    hi_dens = round(hi_per_km * 10) / 10      # 不可叫 dens：會蓋掉上面的路網密度
    life = [
        {"label": "🛵 機車通勤", "stars": star(score + (0.42 - peak) * 80),
         "reason": "尖峰時段事故佔 %d%%" % round(peak * 100)},
        {"label": "🚗 汽車通勤",
         "stars": star(score + 12 - min(16, hi_per_km * 14)),
         "reason": "高風險路口每公里 %s 個；汽車受路口事故影響較機車小" % hi_dens},
        {"label": "🌙 夜間行駛", "stars": star(score + (0.32 - night) * 165),
         "reason": "夜間（18–06）事故佔 %d%%" % round(night * 100)},
        {"label": "🆕 新手駕駛",
         "stars": star(score + 10 - min(30, hi_per_km * 28)),
         "reason": ("沿線 %d 個高風險路口（每公里 %s 個）需頻繁判斷" % (hiN, hi_dens)
                    if hiN else "沿線無高風險路口")},
        {"label": "👶 載小孩／載人", "stars": star(score + (8 if fat == 0 else -20)),
         "reason": ("沿線近兩年無死亡事故" if fat == 0
                    else "沿線近兩年有 %d 人死亡" % fat)},
    ]

    path_ll = [[float(la), float(lo)] for la, lo in
               zip(*D.inv.transform(xy[:, 0], xy[:, 1])[::-1])]

    return {
        "mode": "route", "demo": False,
        "score": {"value": score, "plain": plain, "confidence": confidence(n),
                  "confidence_reason": "沿線 %d 件事故" % n},
        "stats": {"accidents": n, "fatalities": fat, "injuries": inj},
        "route": {"path": path_ll, "km": round(km, 1),
                  "min": max(2, int(round(km / RIDE_KMH * 60))),
                  "score": score, "hiCount": hiN,
                  "shown": len(xs), "total": len(rows),
                  "intersections": xs},
        "intersections": xs,
        "lifestyle": life,
        "trend": trend_of(gidx),
        "data_meta": dict(meta_block(), risk=round(risk, 2),
                          corridor_km=round(dens, 1),
                          band=D.band_of(dens, "ride") + 1),
    }


# ------------------------------------------------------------------ 路由
@app.post("/api/v3/analyze")
def analyze(req: Analyze):
    if req.mode == "walk":
        if req.lat is None or req.lon is None:
            return fail("DATA_UNAVAILABLE", "walk 模式需要 lat / lon")
        if not D.inside(req.lat, req.lon):
            return fail("OUT_OF_COVERAGE", "選定位置不在桃園市範圍內。")
        return analyze_walk(req.lat, req.lon)

    if not req.from_ or not req.to:
        return fail("DATA_UNAVAILABLE", "route 模式需要 from / to")
    for p in (req.from_, req.to):
        if not D.inside(p[0], p[1]):
            return fail("OUT_OF_COVERAGE", "起點或終點不在桃園市範圍內。")
    why = route_error(req.from_, req.to)
    if why:
        return fail(why[0], why[1], 422 if why[0] == "NO_ROUTE" else 400)
    out = analyze_route(req.from_, req.to)
    if out is None:
        return fail("NO_ROUTE", "起訖點之間沒有連通的機車路線，請換一個目的地。", 422)
    return out


@app.get("/api/v3/intersection")
def intersection(lat: float = Query(...), lon: float = Query(...),
                 r: float = Query(60.0, ge=5, le=300)):
    x, y = D.to_xy(lat, lon)
    kk = D.x_tree.query_ball_point([x, y], r)
    if not kk:
        return []
    k = max(kk, key=lambda i: D.x_cnt[D.x_keep[i]])
    return points_of(int(D.x_keep[k]))


@app.get("/api/v3/meta")
def meta():
    return dict(meta_block(),
                accidents=int(D.meta["accidents"]),
                intersections=int(D.meta["intersections"]),
                walk_network_km=round(float(D.w_len.sum()) / 1000, 1),
                # 是各層樣本數的總和，不是 len(dict)——後者永遠回 3
                # （edges / bands / med 三個 key）。
                baseline_walk_cells=sum(len(b) for b in D.ref["walk"]["bands"]),
                baseline_ride_samples=sum(len(b) for b in D.ref["ride"]["bands"]))


@app.get("/")
def root():
    return FileResponse(HTML, media_type="text/html; charset=utf-8")
