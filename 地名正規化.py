# -*- coding: utf-8 -*-
"""台灣地址字串的正規化與拆解。由 建立地名.py 與 api.py 共用。

共用是必要的：建索引時把「龍岡路一段」正規化成「龍岡路1段」，查詢時卻沒做
同一套轉換的話，使用者打「龍岡路1段」會搜不到自己資料庫裡的東西。這跟
核心.py 堅持基準與查詢共用同一套數學是同一個道理。

實際資料裡的變異（取自 事故索引.db 的 57,181 條相異發生地點）：

    桃園市中壢區龍岡路一段288號前0.0公尺附近      ← 中文數字段 + 兩層雜訊
    桃園市中壢區龍岡路1段183號                    ← 阿拉伯數字段
    桃園市蘆竹區山林路一段附近 / 桃園市蘆竹區順利一街附近   ← 一列兩個地點
    桃園市平鎮區金陵里金陵路2段口                 ← 中間夾了里
    桃園市蘆竹區富國路一段81巷口附近V114120279    ← 尾巴黏了案件編號
    桃園市龍潭區福龍路與龍平路口                  ← 「與」連接的路口
"""
import re

# 台／臺：資料裡 661 條用「台」、1 條用「臺」，但 OSM 反過來偏好「臺」。
# 一律收斂到「台」，兩邊才對得起來。
_TRAD = str.maketrans({"臺": "台", "巿": "市", "衖": "弄"})

# 全形英數 → 半形
_FULL = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}
_FULL["　"] = " "
_FULLTAB = str.maketrans(_FULL)

_CN = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
       "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 尾巴的相對位置描述。都不影響「這是哪條路的幾號」，一律拿掉。
# 順序有意義：「前0.0公尺附近」要先吃掉「前0.0公尺」才輪得到「附近」。
#
# 「路口」不能列進來。看起來很自然，但它會把路名自己的那個「路」吃掉：
# 「華美三路口」→「華美三」，反而拆不出路名。實際資料裡有 9,080 條帶
# 「路口」，全都會壞。正確做法是只拿掉結尾那個「口」，交給下面的 r"口$"。
_NOISE = [
    r"前\d+(?:\.\d+)?公尺(?:處)?", r"後\d+(?:\.\d+)?公尺(?:處)?",
    r"約\d+(?:\.\d+)?公尺(?:處)?", r"\d+(?:\.\d+)?公尺處",
    r"附近", r"對面", r"旁邊", r"路旁", r"號前", r"號旁", r"門前", r"近(?=\d)",
    r"交[岔叉](?=路)", r"一帶",
    # 「慢車道」要排在通用的車道規則前面：通用那條的前綴全是可選的，會先
    # 吃掉「車道」兩字，留下一個孤零零的「慢」，之後就沒人認得它了。
    r"[慢快機]車道", r"[東西南北]?向?(?:內|外|中)?側?車道", r"往.{1,8}方向",
    r"[東西南北]{1,2}側$", r"[東西南北]{1,2}向$", r"[東西南北]$",
    # 設施編號／案件代碼：V114120279、燈桿0113535、電杆、中大分線39
    r"[A-Za-z]\d{6,}\s*$", r"(?:電[杆桿]|燈[杆桿]|分線|支線)\s*\d*\s*$", r"\d{6,}\s*$",
    r"口$",
]
_NOISE_RE = [re.compile(p) for p in _NOISE]

_SEG_RE = re.compile(r"([零一二三四五六七八九十]{1,3})(?=[段巷弄號])")

# 桃園 13 個行政區。這裡一定要用明列，不能用 [^\s]{1,3}?[區鄉鎮市] 那種
# 通用寫法：非貪婪的量詞碰到「平鎮區」會先湊出「平鎮」（「鎮」本身就在字元
# 類裡就停了），剩下的「區」黏到路名前面變成「區南平路」。實測 3,993 筆
# 平鎮區的地點全部中獎，路口名也跟著長出「區康樂路 / 區延平路一段」。
DISTRICTS = ("桃園區", "中壢區", "平鎮區", "八德區", "楊梅區", "蘆竹區",
             "龜山區", "大溪區", "大園區", "觀音區", "新屋區", "龍潭區",
             "復興區")
_DIST_RE = re.compile(
    r"^(?:台灣省?|桃園[市縣])?\s*(%s)" % "|".join(DISTRICTS))
# 桃園以外的縣市不是服務範圍，但字串還是要拆得掉（例如比對「不是桃園」）
_DIST_ANY_RE = re.compile(r"^(?:台灣省?|[^\s]{2,3}[市縣])?\s*([^\s]{1,2}[區鄉鎮])")
# 段在台灣最多到八段。超過就是原始資料把「巷」打成「段」——實測 69 筆，
# 出現「龍南路429段」「龍東路445段」這種不存在的段號。
MAX_SEG = 12
# 里與鄰是行政編組，不參與定位。OSM 的 addr:full 幾乎都帶著它們
# （「桃園市八德區瑞豐里21鄰豐吉路50巷1號」），不去掉就拆不出路名。
_LI_RE = re.compile(r"([^\s]{1,4}里)(?=[^\s]*[路街道大])")
_LIN_RE = re.compile(r"\d{1,3}鄰")
# 樓層與之N：「125號七樓之1」「文化七路111號二樓之71」。門牌定位到號就夠，
# 樓層不影響座標。
_FLOOR_RE = re.compile(
    r"(?:[0-9零一二三四五六七八九十]{1,3}樓)(?:之[0-9零一二三四五六七八九十]{1,4})?.*$")
_ADDR_RE = re.compile(
    r"(?P<road>.+?[路街道園])"
    r"(?:(?P<seg>\d{1,3})段)?"
    r"(?:(?P<lane>\d{1,4})巷)?"
    r"(?:(?P<alley>\d{1,4})弄)?"
    r"(?:(?P<no>\d{1,5})(?:[-之]\d{1,3})?號)?"
    r"\s*$")
# 「介壽路建國路」這種沒有分隔符的兩條路。只在後半也是完整路名、且兩邊都
# 夠長時才拆，免得把「中山東路」這種本來就含「路」的單一路名切壞。
_TWOROAD_RE = re.compile(r"^(?P<a>.{2,8}?[路街道])(?P<b>.{2,8}[路街道])$")
# 「A路一段與B路二段」。段的部分要可選，中文與阿拉伯數字都要吃
# （norm 之前就會呼叫到，所以不能假設已經轉成阿拉伯數字）。
_SEGSUF = r"(?:[0-9零一二三四五六七八九十]{1,3}段)?"
# 連接兩條路的字：與、和、、。OSM 的 way 名稱實測有「中山北路1段和中山北路
# 2段」「中山北路2段、中山北路2段」這種寫法，不拆就會長出假路名。
# 「和平路」不會被誤拆：「和」前面必須先有一個以路／街／道／巷結尾的完整
# 路名（至少 3 個字），單獨開頭的「和」湊不出來。
_AND_RE = re.compile(r"(.{2,10}?[路街道巷]%s)\s*[與和、]\s*(.{2,10}?[路街道巷]%s)"
                     % (_SEGSUF, _SEGSUF))


def cn2num(s):
    """把「一」「二十」「十五」這種中文數字轉成阿拉伯數字字串。

    只處理 1–99：段最多到七、巷弄號都是阿拉伯數字，用不到更大的。
    """
    if s.isdigit():
        return s
    if s == "十":
        return "10"
    if len(s) == 1:
        return str(_CN.get(s, 0))
    if s[0] == "十":                       # 十五 → 15
        return str(10 + _CN.get(s[1], 0))
    if len(s) == 3 and s[1] == "十":       # 二十五 → 25
        return str(_CN.get(s[0], 0) * 10 + _CN.get(s[2], 0))
    if len(s) == 2 and s[1] == "十":       # 二十 → 20
        return str(_CN.get(s[0], 0) * 10)
    return s


def norm(s):
    """正規化成可比對的形式：繁簡收斂、全形轉半形、中文數字轉阿拉伯、去空白。

    不去雜訊——雜訊只在拆解地址時去掉。使用者搜尋框裡打的東西不會有
    「前0.0公尺」，但可能會打「中壢區中山路」，那個「區」要留著。
    """
    if not s:
        return ""
    s = s.translate(_TRAD).translate(_FULLTAB)
    s = _SEG_RE.sub(lambda m: cn2num(m.group(1)), s)
    return re.sub(r"\s+", "", s).lower()


def strip_noise(s):
    """去掉相對位置描述與尾巴代碼，留下「哪條路的幾號」。

    要跑到收斂為止：雜訊會疊好幾層，而且拿掉一層才會露出下一層——
    「紅土厝路0113748電杆」先去掉「電杆」，那串數字才變成結尾而被認出來。
    """
    for _ in range(4):
        before = s
        for r in _NOISE_RE:
            s = r.sub("", s)
        if s == before:
            break
    return s.strip()


def split_locs(s):
    """一列兩個地點的拆開。A2 資料用「 / 」，也有用「與」接兩條路的。

    路口事故的發生地點常常記成兩條路，兩條都值得進索引——使用者搜哪一條
    都應該找得到這個位置。
    """
    parts = re.split(r"\s*/\s*", s) if "/" in s else [s]
    out = []
    for p in parts:
        # 縣市與區要先切出來。第二條路名前面沒有區，直接吐出去會變成
        # 「桃園市龍平路」——區不見了，後面就對不到正確的那條路。
        m = _DIST_RE.match(p)
        head, body = (p[:m.end()], p[m.end():]) if m else ("", p)
        # 「福龍路與龍平路口」→ 兩條路。只在兩邊都像路名時才拆。
        # 左邊要容許帶段：「龍岡路一段與中北路二段」如果只認到路／街／道結尾，
        # 「龍岡路一段」的結尾是「段」而配不上，整串就會變成一個叫
        # 「龍岡路1段與中北路」的假路名。實測 435 種發生地點中獎。
        m2 = _AND_RE.search(body)
        if m2:
            out.append(head + m2.group(1))
            out.append(head + m2.group(2))
            continue
        # 「介壽路建國路」這種沒有分隔符的
        two = split_two_roads(strip_noise(body))
        if two:
            out.append(head + two[0])
            out.append(head + two[1])
            continue
        out.append(p)
    return [x for x in (t.strip() for t in out) if x]


def split_two_roads(rest):
    """「介壽路建國路」→ ('介壽路', '建國路')，不像兩條路就回 None。

    兩邊都要求 2–8 字且各自以路／街／道結尾，「中山東路」這種本身就含
    「路」的單一路名才不會被切壞（它切不出兩個合格的半邊）。
    """
    m = _TWOROAD_RE.match(rest)
    return (m.group("a"), m.group("b")) if m else None


def parse(s):
    """把一條地址字串拆成 (區, 路, 段, 巷, 弄, 號)，拆不出來的給 None。

    回傳的路名已正規化（台/段都轉好），可以直接拿來當索引鍵。
    """
    s = norm(strip_noise(s or ""))
    if not s:
        return None
    dist, rest = split_district(s)
    rest = _LI_RE.sub("", rest, count=1)   # 里不參與定位，去掉
    rest = _LIN_RE.sub("", rest, count=1)  # 鄰同理
    rest = _FLOOR_RE.sub("", rest)         # 樓層不影響座標
    m = _ADDR_RE.match(rest)
    if not m:
        return None
    road = m.group("road")
    if not road or len(road) < 2:
        return None
    seg, lane = m.group("seg"), m.group("lane")
    if seg and int(seg) > MAX_SEG:
        # 「龍南路429段」不存在，原始資料把巷打成段了。當成巷才對得上門牌。
        seg, lane = None, lane or seg
    return {"district": dist, "road": road, "seg": seg, "lane": lane,
            "alley": m.group("alley"), "no": m.group("no")}


def split_district(s):
    """把開頭的縣市與區切出來，回傳 (區, 剩下的)。切不出來時區給 None。

    先比對桃園 13 個區的完整名稱，再退回通用規則。順序不能顛倒，理由見
    DISTRICTS 上面的註解。
    """
    m = _DIST_RE.match(s)
    if m:
        return m.group(1), s[m.end():]
    m = _DIST_ANY_RE.match(s)
    return (m.group(1), s[m.end():]) if m else (None, s)


def road_key(district, road, seg=None):
    """路段的唯一鍵。同名的路在不同區是不同的路（桃園有 12 個區都有中山路）。"""
    return "%s|%s|%s" % (district or "", road or "", seg or "")


def display(district, road, seg=None, lane=None, alley=None, no=None):
    """組回人看的地址。"""
    out = "桃園市" + (district or "")
    out += road or ""
    if seg:
        out += "%s段" % seg
    if lane:
        out += "%s巷" % lane
    if alley:
        out += "%s弄" % alley
    if no:
        out += "%s號" % no
    return out
