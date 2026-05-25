# -*- coding: utf-8 -*-

import json
import sqlite3
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DB_FILE = "new_macau.db"

# =========================
# 波色
# =========================

RED = {
    1, 2, 7, 8, 12, 13, 18, 19,
    23, 24, 29, 30, 34, 35,
    40, 45, 46
}

BLUE = {
    3, 4, 9, 10, 14, 15, 20, 25,
    26, 31, 36, 37, 41, 42,
    47, 48
}

GREEN = {
    5, 6, 11, 16, 17, 21, 22,
    27, 28, 32, 33, 38, 39,
    43, 44, 49
}

# =========================
# 五行
# =========================

ELEMENTS = {
    "金": {5, 6, 13, 14, 21, 22, 35, 36, 43, 44},
    "木": {3, 4, 17, 18, 25, 26, 33, 34, 47, 48},
    "水": {1, 2, 15, 16, 23, 24, 37, 38, 45, 46},
    "火": {7, 8, 19, 20, 27, 28, 41, 42, 49},
    "土": {9, 10, 11, 12, 29, 30, 31, 32, 39, 40},
}

# =========================
# 数据库
# =========================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS lottery (
        issue TEXT PRIMARY KEY,
        n1 INTEGER,
        n2 INTEGER,
        n3 INTEGER,
        n4 INTEGER,
        n5 INTEGER,
        n6 INTEGER,
        special INTEGER
    )
    """)
    conn.commit()
    conn.close()

# =========================
# 通用请求
# =========================

def request_api(url):
    """发送请求并返回JSON对象，失败返回None"""
    headers = {"User-Agent": "Mozilla/5.0"}
    req = Request(url, headers=headers)
    try:
        resp = urlopen(req, timeout=20)
    except HTTPError as e:
        print(f"❌ HTTP错误 {url}: {e.code} {e.reason}")
        return None
    except URLError as e:
        print(f"❌ 网络错误 {url}: {e.reason}")
        return None
    except Exception as e:
        print(f"❌ 未知网络错误 {url}: {e}")
        return None

    if resp.status != 200:
        print(f"⚠️ 状态码异常 {url}: {resp.status}")
        return None

    raw = resp.read().decode("utf-8", errors="ignore")
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"❌ JSON解析失败 {url}: {e}")
        print(f"原始响应前200字符: {raw[:200]}")
        return None

# =========================
# 解析单条记录
# =========================

def parse_record(item):
    """从API返回的字典中提取一期开奖数据，返回(期号, 号码列表)或None"""
    issue = str(item.get("expect", "")).strip()
    opencode = item.get("openCode") or item.get("opencode", "")
    if not issue or not opencode:
        return None
    nums = [int(x) for x in opencode.split(",") if x.strip().isdigit()]
    if len(nums) != 7:
        return None
    draw_date = str(item.get("openTime", "") or item.get("opentime", ""))[:10]
    return (issue, draw_date, nums)

# =========================
# 获取最新一期
# =========================

def fetch_latest():
    """获取最新一期开奖数据"""
    url = "https://api3.marksix6.net/lottery_api.php?type=newMacau"
    payload = request_api(url)
    if not payload:
        return None

    # 兼容旧格式(data数组)和新格式(单个对象)
    data_list = payload.get("data")
    if isinstance(data_list, list) and data_list:
        for item in data_list:
            r = parse_record(item)
            if r:
                return r
    else:
        return parse_record(payload)
    return None

# =========================
# 按期号获取历史
# =========================

def fetch_by_issue(issue_no):
    """根据期号拉取单期数据"""
    url = f"https://api3.marksix6.net/lottery_api.php?type=newMacau&expect={issue_no}"
    payload = request_api(url)
    if not payload:
        return None
    return parse_record(payload)

# =========================
# 自动获取最近10期（至少）
# =========================

def fetch_recent_10(existing_issues=None):
    """
    从最新期号开始向前拉取，直到累积至少10条记录或连续失败3次。
    返回 (issue, draw_date, nums) 列表，按期号升序排列。
    """
    if existing_issues is None:
        existing_issues = set()

    # 获取最新期作为基准
    latest = fetch_latest()
    if not latest:
        print("⚠️ 无法获取最新期号，历史拉取中断")
        return []

    records = [latest]
    current_issue = int(latest[0]) - 1
    fails = 0

    # 如果最新期已在库中，且数据库已有>=10条，就不再拉取
    if latest[0] in existing_issues:
        print("📌 最新期已存在，不再重复拉取历史")
        return []

    while len(records) < 10 and fails < 3:
        # 跳过已存在的期号
        if str(current_issue) in existing_issues:
            current_issue -= 1
            continue

        rec = fetch_by_issue(current_issue)
        if rec:
            records.append(rec)
            fails = 0
            print(f"   ✅ 期号 {rec[0]} 拉取成功")
        else:
            fails += 1
            print(f"   ⚠️ 期号 {current_issue} 无数据或请求失败")

        current_issue -= 1
        time.sleep(0.5)  # 礼貌间隔

    # 按期号升序排列
    records.sort(key=lambda x: int(x[0]))
    return records

# =========================
# 保存数据
# =========================

def save_records(rows):
    conn = sqlite3.connect(DB_FILE)
    new_count = 0
    for r in rows:
        issue, draw_date, nums = r
        exists = conn.execute(
            "SELECT issue FROM lottery WHERE issue=?",
            (issue,)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO lottery VALUES(?,?,?,?,?,?,?,?)",
            (issue, nums[0], nums[1], nums[2], nums[3], nums[4], nums[5], nums[6])
        )
        new_count += 1
    conn.commit()
    conn.close()
    return new_count

# =========================
# 获取历史
# =========================

def get_history(limit=120):
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(f"""
    SELECT * FROM lottery
    ORDER BY issue ASC
    LIMIT {limit}
    """).fetchall()
    conn.close()
    return rows

# =========================
# 波色/五行/属性
# =========================

def get_color(n):
    if n in RED:
        return "红"
    if n in BLUE:
        return "蓝"
    return "绿"

def get_element(n):
    for k, v in ELEMENTS.items():
        if n in v:
            return k
    return "?"

def get_attrs(n):
    ds = "单" if n % 2 else "双"
    dx = "大" if n >= 25 else "小"
    hs = sum(map(int, str(n)))
    hds = "合单" if hs % 2 else "合双"
    hdx = "大" if hs >= 7 else "小"
    tail = n % 10
    tw = "尾大" if tail >= 5 else "尾小"
    color = get_color(n)
    element = get_element(n)
    return f"{ds}/{dx} {hds}/{hdx} {tw} {color} {element}"

# =========================
# 选号策略
# =========================

def hot_strategy(hist):
    if len(hist) < 7:
        return None, None
    c = Counter()
    for r in hist[-20:]:
        nums = r[1:7]
        c.update(nums)
    if not c:
        return None, None
    main = [x for x, _ in c.most_common(6)]
    sp = c.most_common(1)[0][0]
    return main, sp

def cold_strategy(hist):
    if len(hist) < 7:
        return None, None
    c = Counter()
    for r in hist[-30:]:
        nums = r[1:7]
        c.update(nums)
    all_nums = set(range(1, 50))
    miss = list(all_nums - set(c.keys()))
    miss.sort()
    main = miss[:6]
    while len(main) < 6:
        main.append(len(main) + 1)
    sp = main[0]
    return main, sp

def momentum_strategy(hist):
    if len(hist) < 7:
        return None, None
    c = Counter()
    for r in hist[-10:]:
        nums = r[1:7]
        c.update(nums)
    if not c:
        return None, None
    main = [x for x, _ in c.most_common(6)]
    sp = main[0]
    return main, sp

def vote_strategy(hist):
    a, _ = hot_strategy(hist) or (None, None)
    b, _ = momentum_strategy(hist) or (None, None)
    if not a or not b:
        return None, None
    c = Counter(a + b)
    main = [x for x, _ in c.most_common(6)]
    sp = main[0]
    return main, sp

def pattern_strategy(hist):
    if len(hist) < 1:
        return None, None
    latest = hist[-1][1:7]
    return list(latest[:6]), latest[0]

# =========================
# 波色预测
# =========================

def color_predict(hist):
    score = {"红": 0, "蓝": 0, "绿": 0}
    recent = hist[-10:]
    if len(recent) < 10:
        return []
    weight = 10
    for r in recent:
        sp = r[7]
        color = get_color(sp)
        score[color] += weight
        weight -= 1
    return sorted(score.items(), key=lambda x: x[1], reverse=True)

# =========================
# 大小单双
# =========================

def dsdx_predict(hist):
    recent = hist[-10:]
    if len(recent) < 10:
        return "数据不足", "数据不足"
    big = small = odd = even = 0
    for r in recent:
        sp = r[7]
        if sp >= 25:
            big += 1
        else:
            small += 1
        if sp % 2:
            odd += 1
        else:
            even += 1
    dx = "大" if big >= small else "小"
    ds = "单" if odd >= even else "双"
    return dx, ds

# =========================
# 回测
# =========================

def backtest(hist):
    recent = hist[-11:]
    if len(recent) < 11:
        return 0, 0, 0, 0
    hit = total = max_miss = miss = 0
    for i in range(1, len(recent)):
        train = recent[:i]
        target = recent[i]
        top = color_predict(train)
        if len(top) < 2:
            continue
        a, b = top[0][0], top[1][0]
        real = get_color(target[7])
        total += 1
        if real in [a, b]:
            hit += 1
            miss = 0
        else:
            miss += 1
            max_miss = max(max_miss, miss)
    rate = round(hit / total * 100, 1) if total else 0
    return hit, total, rate, max_miss

# =========================
# 打印策略
# =========================

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

    # 1. 获取数据库中已有期号集合
    conn = sqlite3.connect(DB_FILE)
    existing = {row[0] for row in conn.execute("SELECT issue FROM lottery").fetchall()}
    conn.close()

    print(f"📊 当前数据库已有 {len(existing)} 期数据")

    # 2. 自动补全历史（如果不足10期，拉取最近10期；否则只拉最新一期）
    if len(existing) < 10:
        print("🔍 数据不足10期，尝试在线拉取最近10期历史...")
        records = fetch_recent_10(existing)
    else:
        # 正常只拉最新一期
        latest = fetch_latest()
        records = [latest] if latest else []

    if records:
        new_count = save_records(records)
        print(f"✅ 本次保存 {new_count} 条新记录")
    else:
        print("⚠️ 未获取到新数据，将基于现有数据库进行分析")

    # 3. 获取历史并预测
    hist = get_history()
    total = len(hist)
    if total == 0:
        print("❌ 数据库无任何开奖记录，无法生成预测")
        return

    latest = hist[-1]
    issue_next = int(latest[0]) + 1

    print("\n最新开奖:")
    print(f"{latest[0]} | " +
          " ".join(f"{x:02d}" for x in latest[1:7]) +
          f" + {latest[7]:02d}")
    print(f"\n预测期号: {issue_next}")

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

    # 波色预测
    print("\n🎨 特码波色预测（加权频率，基于最近10期）：")
    top = color_predict(hist)
    if top:
        print(f"   主强: {top[0][0]} (得分 {top[0][1]})   "
              f"次强: {top[1][0]} (得分 {top[1][1]})")
    else:
        print("   数据不足，无法预测")

    # 大小单双
    dx, ds = dsdx_predict(hist)
    print("\n📊 大小单双预测（最近10期真实数据）：")
    print(f"   大小预测: {dx}   单双预测: {ds}")

    # 回测
    hit, total_bt, rate, max_miss = backtest(hist)
    if total_bt > 0:
        print("\n📈 历史回测（最近10期）：")
        print(f"   二中一命中率: {rate}%")
        print(f"   最近10期命中: {hit}/{total_bt}")
        print(f"   最大连空: {max_miss}期")
    else:
        print("\n📈 历史回测：数据不足，无法计算")

if __name__ == "__main__":
    sync()