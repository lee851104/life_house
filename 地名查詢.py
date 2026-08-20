# -*- coding: utf-8 -*-
"""地名搜尋：把使用者打的字變成座標。由 api.py 的 /api/v3/geocode 使用。

比對的部分交給 SQLite FTS5 的 trigram tokenizer（等於中文子字串搜尋），
這支模組負責兩件 FTS 做不了的事：

1. 先拆解查詢，再決定要搜什麼
   「中壢環北路300號」不能整串丟去比對——資料庫裡沒有任何一筆的文字是
   這樣。要先拆成 區=中壢區、路=環北路、號=300，用「環北路」去比對路段、
   用「中壢區」過濾、最後用門牌表把 300 號換成精確座標。

2. 排序
   trigram 會吐回一堆命中，難的是「中山路」該先給哪一條——桃園 12 個區
   有 9 個都有中山路。排序才是體感像不像 Google Maps 的關鍵，所以權重是
   顯式的、可調的，不用 bm25（trigram 的 bm25 對中文沒什麼意義）。
"""
import math
import os
import re
import sqlite3

import 地名正規化 as N

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "地名.db")

# 桃園 13 個行政區。使用者常常只打「中壢」不打「中壢區」。
DISTRICTS = ["桃園區", "中壢區", "平鎮區", "八德區", "楊梅區", "蘆竹區",
             "龜山區", "大溪區", "大園區", "觀音區", "新屋區", "龍潭區",
             "復興區"]
_BARE = {d[:-1]: d for d in DISTRICTS}
_BARE_RE = re.compile("^(%s)(?=.)" % "|".join(sorted(_BARE, key=len, reverse=True)))

KIND_W = {"poi": 8.0, "road": 4.0, "x": 0.0}   # 同分時 POI 比路名更像「目的地」
SNAP_MAX = 400.0        # 與 核心.py 一致：離機車路網這麼遠就不是可用的起訖點


class Geo:
    """地名.db 的唯讀查詢介面。單一連線、常駐。"""

    def __init__(self, path=DB_PATH):
        self.ok = os.path.exists(path)
        if not self.ok:
            return
        self.db = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                                  uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.n = self.db.execute("SELECT COUNT(*) FROM place").fetchone()[0]
        self.n_addr = self.db.execute("SELECT COUNT(*) FROM addr").fetchone()[0]

    # -------------------------------------------------------------- 查詢拆解
    @staticmethod
    def _split_no(s):
        """把尾巴的門牌號切出來，回傳 (剩下的字, 號, 使用者有沒有真的打「號」)。

        「中山路2段」不會誤判，因為它結尾是「段」不是數字。但「中山路2」是
        真的有歧義——可能在打 2 段也可能在打 2 號——所以記下有沒有「號」字，
        讓上層兩種解讀都列出來。
        """
        m = re.search(r"(\d{1,5})(?:[-之]\d{1,3})?(號)?$", s)
        if m and re.search(r"[路街道巷弄段]", s[:m.start()]):
            return (re.sub(r"[巷弄號之\-]+$", "", s[:m.start()]),
                    int(m.group(1)), m.group(2) is not None)
        return re.sub(r"[巷弄號之\-]+$", "", s), None, False

    def parse_query(self, q):
        """把使用者打的字拆成幾種可能的解讀，每種是 (區, 比對字串, 號, 明確號)。

        為什麼要「幾種」而不是一種：「桃園車站」的開頭剛好是一個區名。把
        「桃園」當區名剝掉之後只剩「車站」，撈回來的是一家叫「車站腿庫飯」
        的小吃店。台鐵車站在 OSM 裡的名字又剛好就是「桃園」「中壢」，所以
        剝與不剝兩種都得試，再讓評分決定誰贏。

        刻意不用 地名正規化.parse：那支是設計來吃完整地址的，會要求字串以
        路／街／道結尾。使用者打到一半的「中壢環北」必須也能搜。
        """
        s = N.norm(q)
        out = []
        d, rest = N.split_district(s)
        if d and rest:
            out.append((d,) + self._split_no(rest))
        elif not d:
            m = _BARE_RE.match(s)          # 常常只打「中壢」不打「中壢區」
            if m and s[m.end():]:
                out.append((_BARE[m.group(1)],) + self._split_no(s[m.end():]))
        # 整串不剝區名的解讀永遠要保留
        out.append((None,) + self._split_no(s))
        if d and not rest:                 # 只打了「中壢區」：列出該區的地標
            out.insert(0, (d, "", None, False))
        return out

    # -------------------------------------------------------------- 候選
    def _candidates(self, text, district, limit=1500):
        """trigram FTS 需要至少 3 個字元；更短的用前綴 LIKE。

        區的過濾刻意留到 Python 做，不寫進 SQL 的 WHERE。把
        `AND p.district = ?` 加進去之後，查詢規劃器會改成從 place(district)
        的索引驅動、再逐列去比對 MATCH，實測從 1.8ms 變成 400ms（慢 200 倍）。
        候選最多 1500 筆，在 Python 過濾是幾十微秒的事。

        SQL 這層的 ORDER BY 不是最終排序（真正的評分在 _score），但不能省：
        LIMIT 沒有排序就是任意截斷，熱門路名可能命中好幾千筆，正確答案有機會
        被切掉。先按「完全相同 → 名字短 → 權重高」粗排，被截掉的一定是最不
        可能的那些。
        """
        order = " ORDER BY (p.txt = ?) DESC, length(p.txt) ASC, p.weight DESC LIMIT ?"
        if len(text) >= 3:
            # trigram 的 MATCH 把查詢也切成三連組做片語比對，等同子字串搜尋。
            # 一定要用雙引號包起來：不然 - 或 * 會被當成 FTS 運算子而語法錯誤。
            sql = ("SELECT p.* FROM place_fts f JOIN place p ON p.id = f.rowid "
                   "WHERE f.ftxt MATCH ?" + order)
            args = ['"%s"' % text.replace('"', '""'), text, limit]
        else:
            sql = "SELECT p.* FROM place p WHERE p.ftxt LIKE ? || '%'" + order
            args = [text, text, limit]
        try:
            rows = self.db.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []
        if district:
            hit = [r for r in rows if r["district"] == district]
            if hit:
                return hit
        return rows

    def _loose(self, text, limit=60):
        """字元順序相符但不連續的比對，例如「桃園機場」→「台灣桃園國際機場」。

        trigram 的 MATCH 是子字串比對，中間插了字就找不到。這裡改用
        LIKE '%桃%園%機%場%' 掃全表——34,730 筆而已，實測 10ms 上下，
        只在連續比對交不出東西時才動用，當成最後一道網。
        """
        if not 2 <= len(text) <= 12:
            return []
        pat = "%" + "%".join(text) + "%"
        return self.db.execute(
            "SELECT * FROM place WHERE ftxt LIKE ? "
            "ORDER BY length(txt) ASC, weight DESC LIMIT ?",
            (pat, limit)).fetchall()

    def _district_top(self, district, limit=8):
        """只打了「中壢區」時，給該區最有代表性的幾個地標。"""
        return self.db.execute(
            "SELECT * FROM place WHERE district = ? AND kind = 'poi' "
            "ORDER BY weight DESC, length(txt) ASC LIMIT ?",
            (district, limit)).fetchall()

    # -------------------------------------------------------------- 排序
    @staticmethod
    def _subseq_density(text, txt):
        """text 的字元依序（可不連續）出現在 txt 裡的緊密度，沒出現回 None。

        「桃園機場」在「台灣桃園國際機場」裡是 4 個字散在 6 個字的跨距內，
        密度 0.67；散得越開越不像使用者要找的東西。
        """
        i, first, last = 0, None, None
        for j, c in enumerate(txt):
            if i < len(text) and c == text[i]:
                if first is None:
                    first = j
                last = i, j
                i += 1
        if i < len(text):
            return None
        return len(text) / max(1, last[1] - first + 1)

    @staticmethod
    def _score(row, text, near, qlen=None, want_district=None):
        """比對分數 × 涵蓋率 ＋ 知名度 ＋ 位置修正。

        涵蓋率是關鍵：查詢「桃園機場」會產生兩種解讀，剝掉區名的那個只剩
        「機場」，它對「機場旅館」是漂亮的前綴命中，但那只用掉使用者打的一半
        字。不按比例打折的話，機場旅館會贏過機場本體。

        知名度（weight）只給 0.3 倍。它原本跟比對分數同一個量級（都到 100），
        於是排序幾乎由 OSM 的標籤種類決定，使用者打什麼變成次要的。
        """
        txt = row["txt"] or ""
        # 正式名稱與別名都要比，取最好的那個。別名扣 5 分，同分時正式名優先。
        s = 0.0
        for p in (row["ftxt"] or txt).split():
            if p == text:
                v = 100.0
            elif p.startswith(text):
                v = 70.0 - min(20.0, (len(p) - len(text)) * 1.5)
            elif text in p:
                v = 45.0 - min(20.0, (len(p) - len(text)) * 1.0)
            else:
                d = Geo._subseq_density(text, p)
                v = 42.0 * d if d else 12.0
            s = max(s, v - (0.0 if p == txt else 5.0))
        # 只用到查詢的一部分就按比例打折
        s *= (len(text) / qlen) if qlen else 1.0
        s += row["weight"] * 0.3 + KIND_W.get(row["kind"], 0.0)
        # 有門牌資料的路段代表它是真的住得了人的路，不是產業道路
        if row["kind"] == "road" and row["n_addr"]:
            s += min(6.0, math.log10(row["n_addr"] + 1) * 3)
        if want_district:
            # 剝掉的區名要兌現。使用者明確打了區，結果卻在別區，多半是這次
            # 剝除猜錯了（「大溪老街」的「大溪」不是區名而是地名的一部分）。
            # 罰得夠重才壓得住高知名度的無關結果；真的猜錯時，另一個「整串
            # 不剝」的解讀不受罰，會接手。
            s += 6.0 if row["district"] == want_district else -25.0
        if row["snap"] > SNAP_MAX:         # 選了也不能當起訖點，往後排
            s -= 25.0
        if near:                           # 離目前地圖中心近的優先
            d = math.hypot(row["x"] - near[0], row["y"] - near[1]) / 1000.0
            s += max(0.0, 8.0 - d * 0.4)
        return s

    # -------------------------------------------------------------- 門牌
    def _house(self, road_id, no, anchor=None):
        """在指定路段上找門牌號，回傳 (lat, lon, 實際號碼)。

        門牌不連續是常態（單雙號分邊、拆併、空號），硬要完全相符會讓
        「中山路101號」這種明明存在的地址搜不到，所以找不到就取最接近的。

        回傳的是「實際找到的號碼」而不是使用者要的號碼：兩者不同時，標題要
        寫實際的那個。寫成使用者要的號碼但座標是隔壁那間，等於在騙人——
        實測 300 個真實地址裡，座標差距 p90 是 366m，多半就是這種近似命中。
        """
        rows = self.db.execute(
            "SELECT lat, lon, no FROM addr WHERE road_id=? AND no=?",
            (road_id, no)).fetchall()
        if not rows:
            rows = self.db.execute(
                "SELECT lat, lon, no FROM addr WHERE road_id=? "
                "ORDER BY ABS(no - ?) LIMIT 8", (road_id, no)).fetchall()
            rows = [r for r in rows if abs(r["no"] - no) <= 200]
        if not rows:
            return None
        # 同一個號碼可能有好幾個座標：OSM 的 addr:district 偶爾標錯，把別區的
        # 門牌掛到同名的路上（實測楊梅區中山北路1段有 9/2159 筆離群，最遠 20km）。
        # 取離這條路本體最近的那個，離群點自然被排除。
        if len(rows) > 1 and anchor:
            rows.sort(key=lambda r: (r["lat"] - anchor[0]) ** 2
                      + (r["lon"] - anchor[1]) ** 2)
        return rows[0]["lat"], rows[0]["lon"], rows[0]["no"]

    # -------------------------------------------------------------- 對外
    def _entry(self, r, name=None, detail=None, lat=None, lon=None, exact=True):
        name = name or r["name"]
        return {
            "name": name,
            "district": r["district"],
            "detail": detail or r["detail"],
            "kind": r["kind"],
            "lat": round(lat if lat is not None else r["lat"], 6),
            "lon": round(lon if lon is not None else r["lon"], 6),
            "address": "桃園市%s%s" % (r["district"] or "", name),
            # 前端要在下拉選單就標示，不要等使用者選了才被後端擋下來
            "routable": bool(r["snap"] <= SNAP_MAX),
            "exact": bool(exact),
        }

    def search(self, q, limit=8, near=None):
        if not self.ok or not q or not q.strip():
            return []

        # 每種解讀各自取候選，同一筆地點取分數最高的那個解讀。這樣
        # 「桃園車站」剝區名的版本（車站）和不剝的版本（桃園車站）會同時
        # 參賽，由評分決定誰排前面，而不是由剝除規則單方面決定。
        qlen = max(1, len(N.norm(q)))
        best = {}
        for district, text, no, explicit in self.parse_query(q):
            if not text:
                rows = self._district_top(district) if district else []
            else:
                rows = self._candidates(text, district)
                # 連續比對有結果也不代表夠好：「桃園機場」會命中「桃園機場
                # 消防隊北站」，但機場本體叫「台灣桃園國際機場」，中間插了
                # 「國際」兩個字所以連續比對撈不到，得補一次非連續比對。
                #
                # 但已經有名稱完全相同的命中時就不必補了。非連續比對是掃全表
                # （34,738 筆，約 30ms），無條件跑會讓「中壢區環北路300號」
                # 這種最常見的查詢從 1ms 變 30ms。有完全相同的名字在手上，
                # 補進來的東西不可能贏過它。
                if not any((r["txt"] or "") == text for r in rows):
                    rows = rows + self._loose(text)
                if not rows and len(text) > 3:
                    rows = self._candidates(text[:3], district)
            for r in rows:
                sc = self._score(r, text, near, qlen, district)
                prev = best.get(r["id"])
                if prev is None or sc > prev[0]:
                    best[r["id"]] = (sc, r, no, explicit)
        if not best:
            return []

        ranked = sorted(best.values(), key=lambda t: -t[0])
        out, seen = [], set()
        for _, r, no, explicit in ranked:
            # 同名同區的重複只留分數最高的（OSM 常把一座公園切成好幾個面，
            # 建索引時的 100m 去重擋不住散得比較開的那些）
            dk = (r["txt"], r["district"])
            if dk in seen:
                continue
            seen.add(dk)
            if no is not None and r["kind"] == "road":
                h = self._house(r["id"], no, (r["lat"], r["lon"]))
                if h:
                    lat, lon, got = h
                    hit = (got == no)
                    out.append(self._entry(
                        r, name="%s%d號" % (r["name"], got),
                        detail="門牌" if hit else "門牌（查無 %d 號，這是最近的）" % no,
                        lat=lat, lon=lon, exact=hit))
                    # 使用者只打了數字沒打「號」時，「中山路2」可能是在打
                    # 2 段也可能是 2 號。兩種解讀都列出來讓他自己挑。
                    if not explicit:
                        out.append(self._entry(r))
                    if len(out) >= limit:
                        break
                    continue
            out.append(self._entry(r))
            if len(out) >= limit:
                break
        return out[:limit]
