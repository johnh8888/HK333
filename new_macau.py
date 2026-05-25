# -*- coding: utf-8 -*-

import json
import sqlite3
from collections import Counter
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DB_FILE = "new_macau.db"

# =========================
# 波色 & 五行
# =========================
RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}

ELEMENTS = {
    "金": {5,6,13,14,21,22,35,36,43,44},
    "木": {3,4,17,18,25,26,33,34,47,48},
    "水": {1,2,15,16,23,24,37,38,45,46},
    "火": {7,8,19,20,27,28,41,42,49},
    "土": {9,10,11,12,29,30,31,32,39,40},
}

# =========================
# 数据库
# =========================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS lottery (
        issue TEXT PRIMARY KEY,
        n1 INTEGER, n2 INTEGER, n3 INTEGER,
        n4 INTEGER, n5 INTEGER, n6 INTEGER,
        special INTEGER
    )
    """)
    conn.commit()
    conn.close()

# =========================
# 网络请求（返回原始文本）
# =========================
def fetch_raw(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    req = Request(url, headers=headers)
    try:
        resp = urlopen(req, timeout=20)
    except HTTPError as e:
        print(f"❌ HTTP错误: {e.code}")
        return None
    except URLError as e:
        print(f"❌ 网络错误: {e.reason}")
        return None
    except Exception as e:
        print(f"❌ 未知错误: {e}")
        return None
    if resp.status != 200:
        print(f"⚠️ 状态码: {resp.status}")
        return None
    return resp.read().decode("utf-8", errors="ignore")

# =========================
# 解析 marksix6.net 接口（字符串数组格式）
# =========================
def parse_string_array(raw_text):
    """
    接口返回格式：["期号,号码1,号码2,...,号码7", ...]
    返回列表 [ (issue, [n1..n7]), ... ] 按期号升序排列
    """
    try:
        data = json.loads(raw_text)
    except Exception as e:
        print(f"❌ JSON解析失败: {e}")
        return []

    if not isinstance(data, list):
        print(f"⚠️ 数据不是列表，类型为: {type(data)}")
        return []

    records = []
    for item in data:
        if not isinstance(item, str):
            continue
        parts = item.split(",")
        if len(parts) != 8:   # 期号 + 7个号码
            continue
        try:
            issue = parts[0].strip()
            nums = [int(x) for x in parts[1:8]]
            records.append((issue, nums))
        except ValueError:
            continue

    # 按期号升序排列
    records.sort(key=lambda x: int(x[0]))
    return records

# =========================
# 获取多期数据（主入口）
# =========================
def fetch_multi_data():
    """
    尝试从 marksix6.net 获取最近多期数据（通常返回约120期）
    返回列表 [(issue, nums), ...]
    """
    url = "https://marksix6.net/index.php?api=1"
    raw = fetch_raw(url)
    if not raw:
        return []
    records = parse_string_array(raw)
    if records:
        print(f"✅ 在线获取到 {len(records)} 期数据（{records[0][0]} ~ {records[-1][0]}）")
    else:
        print("⚠️ 在线数据解析为空")
    return records

# =========================
# 保存数据
# =========================
def save_records(records):
    conn = sqlite3.connect(DB_FILE)
    new = 0
    for issue, nums in records:
        exists = conn.execute("SELECT issue FROM lottery WHERE issue=?", (issue,)).fetchone()
        if exists:
            continue
        conn.execute("INSERT INTO lottery VALUES(?,?,?,?,?,?,?,?)",
                     (issue, *nums))
        new += 1
    conn.commit()
    conn.close()
    return new

def get_history(limit=120):
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(f"SELECT * FROM lottery ORDER BY issue ASC LIMIT {limit}").fetchall()
    conn.close()
    return rows

# =========================
# 波色/属性
# =========================
def get_color(n):
    if n in RED: return "红"
    if n in BLUE: return "蓝"
    return "绿"

def get_element(n):
    for k,v in ELEMENTS.items():
        if n in v: return k
    return "?"

def get_attrs(n):
    ds = "单" if n%2 else "双"
    dx = "大" if n>=25 else "小"
    hs = sum(map(int, str(n)))
    hds = "合单" if hs%2 else "合双"
    hdx = "大" if hs>=7 else "小"
    tail = n%10
    tw = "尾大" if tail>=5 else "尾小"
    return f"{ds}/{dx} {hds}/{hdx} {tw} {get_color(n)} {get_element(n)}"

# =========================
# 策略（数据不足返回None）
# =========================
def hot_strategy(hist):
    if len(hist) < 7: return None, None
    c = Counter()
    for r in hist[-20:]:
        c.update(r[1:7])
    if not c: return None, None
    main = [x for x,_ in c.most_common(6)]
    sp = c.most_common(1)[0][0]
    return main, sp

def cold_strategy(hist):
    if len(hist) < 7: return None, None
    c = Counter()
    for r in hist[-30:]:
        c.update(r[1:7])
    miss = list(set(range(1,50)) - set(c.keys()))
    miss.sort()
    main = miss[:6]
    while len(main) < 6: main.append(len(main)+1)
    return main, main[0]

def momentum_strategy(hist):
    if len(hist) < 7: return None, None
    c = Counter()
    for r in hist[-10:]:
        c.update(r[1:7])
    if not c: return None, None
    main = [x for x,_ in c.most_common(6)]
    return main, main[0]

def vote_strategy(hist):
    a,_ = hot_strategy(hist) or (None, None)
    b,_ = momentum_strategy(hist) or (None, None)
    if not a or not b: return None, None
    c = Counter(a+b)
    main = [x for x,_ in c.most_common(6)]
    return main, main[0]

def pattern_strategy(hist):
    if len(hist) < 1: return None, None
    latest = hist[-1][1:7]
    return list(latest[:6]), latest[0]

# =========================
# 预测
# =========================
def color_predict(hist):
    score = {"红":0,"蓝":0,"绿":0}
    recent = hist[-10:]
    if len(recent) < 10: return []
    w = 10
    for r in recent:
        score[get_color(r[7])] += w
        w -= 1
    return sorted(score.items(), key=lambda x:x[1], reverse=True)

def dsdx_predict(hist):
    recent = hist[-10:]
    if len(recent) < 10: return "数据不足","数据不足"
    big=small=odd=even=0
    for r in recent:
        sp = r[7]
        if sp>=25: big+=1
        else: small+=1
        if sp%2: odd+=1
        else: even+=1
    return ("大" if big>=small else "小"), ("单" if odd>=even else "双")

def backtest(hist):
    recent = hist[-11:]
    if len(recent) < 11: return 0,0,0,0
    hit=total=max_miss=miss=0
    for i in range(1, len(recent)):
        train = recent[:i]
        target = recent[i]
        top = color_predict(train)
        if len(top) < 2: continue
        a,b = top[0][0], top[1][0]
        if get_color(target[7]) in (a,b):
            hit+=1; miss=0
        else:
            miss+=1; max_miss=max(max_miss, miss)
        total+=1
    rate = round(hit/total*100,1) if total else 0
    return hit,total,rate,max_miss

def show_strategy(name, main, sp):
    if main is None:
        print(f"{name:<16}: 数据不足，无法生成推荐")
        return
    print(f"{name:<16}: " + " ".join(f"{x:02d}" for x in main) + f" + {sp:02d}")
    print(f"{'':16} 特码属性: {get_attrs(sp)}")

# =========================
# 主流程
# =========================
def sync():
    init_db()
    # 1. 在线拉取多期数据
    records = fetch_multi_data()
    if records:
        new = save_records(records)
        print(f"📦 本次新增 {new} 条记录")
    else:
        print("⚠️ 在线数据获取失败，将使用本地数据库进行分析")

    hist = get_history()
    total = len(hist)
    print(f"📊 当前数据库共 {total} 期数据")
    if total == 0:
        print("❌ 无数据，无法预测")
        return

    latest = hist[-1]
    next_issue = int(latest[0]) + 1
    print(f"\n最新开奖: {latest[0]} | " +
          " ".join(f"{x:02d}" for x in latest[1:7]) + f" + {latest[7]:02d}")
    print(f"预测期号: {next_issue}")

    # 策略输出
    h_main, h_sp = hot_strategy(hist)
    c_main, c_sp = cold_strategy(hist)
    m_main, m_sp = momentum_strategy(hist)
    v_main, v_sp = vote_strategy(hist)
    p_main, p_sp = pattern_strategy(hist)

    show_strategy("组合策略 (投票)", v_main, v_sp)
    show_strategy("冷号回补", c_main, c_sp)
    show_strategy("热号策略", h_main, h_sp)
    show_strategy("近期动量", m_main, m_sp)
    show_strategy("规律挖掘", p_main, p_sp)

    print("\n🎨 特码波色预测（需≥10期）:")
    top = color_predict(hist)
    if top:
        print(f"   主强: {top[0][0]} (得分{top[0][1]})   次强: {top[1][0]} (得分{top[1][1]})")
    else:
        print("   数据不足")

    dx, ds = dsdx_predict(hist)
    print(f"\n📊 大小单双预测: 大小 {dx} / 单双 {ds}")

    hit, total_bt, rate, max_miss = backtest(hist)
    if total_bt:
        print(f"\n📈 近10期回测（二中一）: 命中率 {rate}% ({hit}/{total_bt})，最大连空 {max_miss}期")
    else:
        print("\n📈 回测：数据不足")

if __name__ == "__main__":
    sync()