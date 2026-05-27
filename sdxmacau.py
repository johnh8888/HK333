#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三彩种属性预测 V9.3 (最终优化版)
- 自动温度搜索 + Platt 校准
- 模型多样性 (异阶马尔可夫、动量)
- Regime 择时 (滑动 ΔLogLoss 开关)
- 聚焦香港彩波色
"""

from __future__ import annotations

import argparse, json, sqlite3, math, ssl, sys, time, random
from collections import defaultdict, Counter
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
from urllib.request import Request, urlopen

# ========== 配置 ==========
SCRIPT_DIR = Path(__file__).resolve().parent
DB_FILES = {
    "老澳门彩": "old_macau.db",
    "香港彩": "hk_macau.db",
    "新澳门彩": "xin_macau.db"
}
THIRD_PARTY_URLS = [
    "https://marksix6.net/index.php?api=1",
    "https://marksix6.net/api/lottery_api.php"
]
ATTRIBUTE_STATES = {
    "color": ["红", "蓝", "绿"],
    "size": ["大", "小"],
    "odd_even": ["单", "双"]
}

# ========== 属性映射 ==========
def get_color(num: int) -> str:
    RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
    BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
    GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}
    if num in RED: return "红"
    if num in BLUE: return "蓝"
    return "绿"

def get_big_small(num: int) -> str: return "大" if num >= 25 else "小"
def get_odd_even(num: int) -> str: return "单" if num % 2 == 1 else "双"
def get_tail(num: int) -> int: return num % 10
def get_mod7(num: int) -> int: return num % 7
def get_zone(num: int) -> int:
    if num <= 10: return 0
    if num <= 20: return 1
    if num <= 30: return 2
    if num <= 40: return 3
    return 4

def get_cross_distance(prev: int, cur: int) -> int: return abs(prev - cur)
def bin_cross_distance(dist: int) -> int:
    if dist <= 4: return 0
    if dist <= 9: return 1
    if dist <= 14: return 2
    if dist <= 19: return 3
    return 4

# ========== 数据层 ==========
@dataclass
class DrawRecord:
    issue_no: str; draw_date: str; numbers: List[int]; special_number: int

def utc_now() -> str: return datetime.now(timezone.utc).isoformat()

def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS draws (
        issue_no TEXT PRIMARY KEY, draw_date TEXT NOT NULL,
        numbers_json TEXT NOT NULL, special_number INTEGER NOT NULL,
        source TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()

def fetch_json_url(url: str, timeout: int = 20, retries: int = 2):
    for attempt in range(retries):
        try:
            ctx = ssl.create_default_context()
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=timeout, context=ctx) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                raw = resp.read().decode(charset, errors="ignore")
                return json.loads(raw)
        except Exception:
            if attempt == retries - 1: raise
            time.sleep(1)
    raise RuntimeError("无法获取数据")

def parse_response(payload, lottery_name: str):
    records = []
    lottery_data = payload.get("lottery_data", [])
    target = next((l for l in lottery_data if l.get("name") == lottery_name), None)
    if not target: return records
    try: latest_open_time = datetime.strptime(target.get("openTime", ""), "%Y-%m-%d %H:%M:%S")
    except: latest_open_time = datetime.now()
    for idx, item in enumerate(target.get("history", [])):
        try:
            parts = item.split("期：")
            if len(parts) != 2: continue
            issue_no = parts[0].strip()
            nums = [int(n.strip()) for n in parts[1].split(",")]
            if len(nums) != 7: continue
            draw_date = (latest_open_time - timedelta(days=idx)).strftime("%Y-%m-%d")
            records.append(DrawRecord(issue_no, draw_date, nums[:6], nums[6]))
        except: continue
    return records

def fetch_online_records(lottery_name: str):
    for url in THIRD_PARTY_URLS:
        try:
            payload = fetch_json_url(url)
            records = parse_response(payload, lottery_name)
            if records: return records, "marksix6", url
        except Exception as e: print(f"从 {url} 获取失败: {e}")
    raise RuntimeError(f"无法获取 {lottery_name} 数据")

def upsert_draw(conn, record, source):
    now = utc_now()
    if conn.execute("SELECT 1 FROM draws WHERE issue_no=?", (record.issue_no,)).fetchone():
        conn.execute("""UPDATE draws SET draw_date=?, numbers_json=?, special_number=?, source=?, updated_at=? WHERE issue_no=?""",
                     (record.draw_date, json.dumps(record.numbers), record.special_number, source, now, record.issue_no))
        return "updated"
    else:
        conn.execute("""INSERT INTO draws VALUES (?,?,?,?,?,?,?)""",
                     (record.issue_no, record.draw_date, json.dumps(record.numbers), record.special_number, source, now, now))
        return "inserted"

def sync_from_records(conn, records, source):
    ins = upd = 0
    for r in records:
        res = upsert_draw(conn, r, source)
        if res == "inserted": ins += 1
        else: upd += 1
    conn.commit()
    return len(records), ins, upd

def load_sequence(conn, attr_func, limit=500) -> List[str]:
    rows = conn.execute("SELECT special_number FROM draws ORDER BY issue_no ASC LIMIT ?", (limit,)).fetchall()
    return [attr_func(r["special_number"]) for r in rows]

def load_full_draws(conn, limit=500) -> List[Dict]:
    rows = conn.execute("SELECT special_number, draw_date FROM draws ORDER BY issue_no ASC LIMIT ?", (limit,)).fetchall()
    return [{"num": r["special_number"], "date": r["draw_date"]} for r in rows]

# ========== 统计工具 ==========
def wilcoxon_signed_rank_test(diffs: List[float]) -> Tuple[float, float]:
    diffs = [d for d in diffs if d != 0]
    n = len(diffs)
    if n < 5: return 0, 1.0
    abs_diffs = [abs(d) for d in diffs]
    ranks = np.argsort(np.argsort(abs_diffs)) + 1
    W_plus = sum(r for d, r in zip(diffs, ranks) if d > 0)
    W_minus = sum(r for d, r in zip(diffs, ranks) if d < 0)
    T = min(W_plus, W_minus)
    mu = n*(n+1)/4
    sigma = math.sqrt(n*(n+1)*(2*n+1)/24)
    if sigma == 0: return T, 1.0
    z = (T - mu) / sigma
    from math import erf
    p = 2 * (1 - 0.5 * (1 + erf(abs(z)/math.sqrt(2))))
    return T, p

def mann_whitney_u(x: List[float], y: List[float]) -> Tuple[float, float]:
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0: return 0, 1.0
    combined = np.concatenate([x, y])
    ranks = np.argsort(np.argsort(combined)) + 1
    rank_x = np.sum(ranks[:n1])
    U1 = n1*n2 + n1*(n1+1)/2 - rank_x
    U2 = n1*n2 - U1
    U = min(U1, U2)
    mu = n1*n2/2
    sigma = math.sqrt(n1*n2*(n1+n2+1)/12)
    if sigma == 0: return U, 1.0
    z = (U - mu) / sigma
    from math import erf
    p = 2 * (1 - 0.5 * (1 + erf(abs(z)/math.sqrt(2))))
    return U, p

# ========== Platt Scaling ==========
class PlattCalibrator:
    def __init__(self, lr=0.01, epochs=100):
        self.lr = lr; self.epochs = epochs
        self.A = 0.0; self.B = 0.0

    def fit(self, scores: np.ndarray, labels: np.ndarray):
        scores = np.clip(scores, 1e-12, 1-1e-12)
        logits = np.log(scores / (1 - scores))
        self.A = 0.0; self.B = 0.0
        for _ in range(self.epochs):
            p = 1.0 / (1 + np.exp(-(self.A * logits + self.B)))
            err = labels - p
            grad_A = -np.mean(err * logits)
            grad_B = -np.mean(err)
            self.A -= self.lr * grad_A
            self.B -= self.lr * grad_B

    def predict_proba(self, scores: np.ndarray) -> np.ndarray:
        scores = np.clip(scores, 1e-12, 1-1e-12)
        logits = np.log(scores / (1 - scores))
        return 1.0 / (1 + np.exp(-(self.A * logits + self.B)))

# ========== StableHMM ==========
class StableHMM:
    def __init__(self, n_hidden=6, n_obs=3, states_list=None, reg_factor=0.25):
        self.n_hidden = n_hidden; self.n_obs = n_obs
        self.states_list = states_list
        self.obs_to_idx = {s: i for i, s in enumerate(states_list)} if states_list else {}
        self.reg_factor = reg_factor; self.eps = 1e-10
        self.pi = np.ones(n_hidden)/n_hidden
        self.A = np.ones((n_hidden, n_hidden))/n_hidden
        self.B = np.ones((n_hidden, n_obs))/n_obs

    def train(self, obs_seq, max_iter=50):
        if len(obs_seq) < 40: return
        obs_idx = np.array([self.obs_to_idx[o] for o in obs_seq])
        T = len(obs_idx)
        for _ in range(max_iter):
            log_alpha = np.full((T, self.n_hidden), -np.inf)
            log_alpha[0] = np.log(self.pi+self.eps) + np.log(self.B[:, obs_idx[0]]+self.eps)
            for t in range(1,T):
                tmp = log_alpha[t-1][:,None] + np.log(self.A+self.eps)
                log_alpha[t] = np.logaddexp.reduce(tmp, axis=0) + np.log(self.B[:, obs_idx[t]]+self.eps)
            log_beta = np.full((T, self.n_hidden), -np.inf)
            log_beta[-1] = 0.0
            for t in range(T-2,-1,-1):
                tmp = np.log(self.A+self.eps) + np.log(self.B[:, obs_idx[t+1]]+self.eps) + log_beta[t+1]
                log_beta[t] = np.logaddexp.reduce(tmp, axis=1)
            log_gamma = log_alpha + log_beta
            log_gamma -= np.logaddexp.reduce(log_gamma, axis=1, keepdims=True)
            gamma = np.exp(log_gamma)
            self.pi = gamma[0]/(np.sum(gamma[0])+self.eps)
            xi_sum = np.zeros((self.n_hidden, self.n_hidden))
            for t in range(T-1):
                tmp = log_alpha[t][:,None] + np.log(self.A+self.eps) + np.log(self.B[:, obs_idx[t+1]]+self.eps) + log_beta[t+1]
                log_xi = tmp - np.logaddexp.reduce(tmp, axis=1, keepdims=True)
                xi_sum += np.exp(log_xi)
            self.A = xi_sum/(np.sum(gamma[:-1], axis=0)[:,None]+self.eps)
            uniform = np.full((self.n_hidden, self.n_hidden), 1.0/self.n_hidden)
            self.A = (1-self.reg_factor)*self.A + self.reg_factor*uniform
            self.A /= np.sum(self.A, axis=1, keepdims=True)+self.eps
            self.B = np.zeros((self.n_hidden, self.n_obs))
            for t in range(T):
                self.B[:, obs_idx[t]] += gamma[t]
            self.B += self.eps
            self.B /= np.sum(self.B, axis=1, keepdims=True)+self.eps

    def predict_next_probs(self, obs_seq):
        if len(obs_seq) < 3: return {s: 1.0/len(self.states_list) for s in self.states_list}
        obs_idx = [self.obs_to_idx[o] for o in obs_seq]
        T = len(obs_idx)
        log_alpha = np.full((T, self.n_hidden), -np.inf)
        log_alpha[0] = np.log(self.pi+self.eps) + np.log(self.B[:, obs_idx[0]]+self.eps)
        for t in range(1,T):
            tmp = log_alpha[t-1][:,None] + np.log(self.A+self.eps)
            log_alpha[t] = np.logaddexp.reduce(tmp, axis=0) + np.log(self.B[:, obs_idx[t]]+self.eps)
        log_gamma = log_alpha[-1]
        gamma = np.exp(log_gamma - np.logaddexp.reduce(log_gamma))
        gamma /= np.sum(gamma)+self.eps
        next_hidden = gamma @ self.A
        next_probs = next_hidden @ self.B
        next_probs = np.clip(next_probs, self.eps, 1.0)
        next_probs /= np.sum(next_probs)
        return {self.states_list[i]: float(next_probs[i]) for i in range(self.n_obs)}

# ========== 子模型组件 ==========
class FeatureConditionalModel:
    def __init__(self, states, alpha=2.0, min_count=5):
        self.states = states; self.alpha = alpha; self.min_count = min_count
        self.counts = defaultdict(lambda: defaultdict(float)); self.total = defaultdict(float)

    def train(self, seq, fvs, decay=1.0):
        w = 1.0
        for s, fv in zip(reversed(seq), reversed(fvs)):
            self.counts[fv][s] += w; self.total[fv] += w; w *= decay

    def partial_fit(self, s, fv, w=1.0):
        self.counts[fv][s] += w; self.total[fv] += w

    def predict(self, fv):
        K = len(self.states)
        tot = self.total.get(fv, 0.0)
        if tot < self.min_count: return {s: 1.0/K for s in self.states}
        probs = {s: (self.counts[fv].get(s,0)+self.alpha)/(tot+self.alpha*K) for s in self.states}
        s = sum(probs.values())
        return {k: v/s for k,v in probs.items()} if s>0 else {k:1.0/K for k in self.states}

class MarkovN:
    def __init__(self, order, states, alpha=1.2):
        self.order = order; self.states = states; self.alpha = alpha
        self.counts = defaultdict(Counter); self.total = defaultdict(int)

    def train(self, seq, decay=1.0):
        w = 1.0
        for i in range(len(seq)-self.order-1, -1, -1):
            ctx = tuple(seq[i:i+self.order])
            nxt = seq[i+self.order]
            self.counts[ctx][nxt] += w; self.total[ctx] += w; w *= decay

    def partial_fit(self, ctx, nxt, w=1.0):
        self.counts[ctx][nxt] += w; self.total[ctx] += w

    def predict(self, ctx):
        K = len(self.states)
        tot = self.total.get(ctx, 0)
        probs = {s: (self.counts[ctx].get(s,0)+self.alpha)/(tot+self.alpha*K) for s in self.states}
        s = sum(probs.values())
        return {k: v/s for k,v in probs.items()} if s>0 else {k:1.0/K for k in self.states}

class StreakBias:
    def __init__(self, states, alpha=1.2):
        self.states = states; self.alpha = alpha
        self.streak_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        self.cur = None; self.cur_len = 0

    def train(self, seq):
        i = 0
        while i < len(seq):
            j = i
            while j < len(seq) and seq[j] == seq[i]: j += 1
            if j < len(seq): self.streak_counts[seq[i]][j-i][seq[j]] += 1.0
            i = j

    def partial_update(self, new_state):
        if self.cur is None:
            self.cur = new_state; self.cur_len = 1; return
        if new_state == self.cur: self.cur_len += 1
        else:
            self.streak_counts[self.cur][self.cur_len][new_state] += 1.0
            self.cur = new_state; self.cur_len = 1

    def predict(self, last, streak_len):
        K = len(self.states)
        tot = sum(self.streak_counts[last][streak_len].values())
        probs = {s: (self.streak_counts[last][streak_len].get(s,0)+self.alpha)/(tot+self.alpha*K) for s in self.states}
        s = sum(probs.values())
        return {k: v/s for k,v in probs.items()} if s>0 else {k:1.0/K for k in self.states}

class FrequencyPrior:
    def __init__(self, states):
        self.states = states; self.probs = {s: 1.0/len(states) for s in states}
        self._cnt = defaultdict(float); self._total = 0.0

    def train(self, seq, decay=1.0):
        if not seq: return
        weights = np.power(decay, np.arange(len(seq)-1, -1, -1))
        for s, w in zip(reversed(seq), weights):
            self._cnt[s] += w; self._total += w
        self._update()

    def partial_fit(self, s, w=1.0):
        self._cnt[s] += w; self._total += w; self._update()

    def _update(self):
        if self._total > 0:
            self.probs = {s: self._cnt[s]/self._total for s in self.states}
            s = sum(self.probs.values())
            self.probs = {k: v/s for k,v in self.probs.items()}

    def predict(self): return self.probs.copy()

class MomentumModel:
    def __init__(self, states, alpha=2.0):
        self.states = states; self.alpha = alpha
        self.same_count = 0.0; self.total = 0.0

    def train(self, seq):
        for i in range(len(seq)-1):
            if seq[i] == seq[i+1]: self.same_count += 1
            self.total += 1

    def partial_fit(self, prev_state, curr_state, weight=1.0):
        if prev_state is not None and prev_state == curr_state:
            self.same_count += weight
        self.total += weight

    def predict(self, last_state):
        K = len(self.states)
        if self.total == 0: return {s: 1.0/K for s in self.states}
        p_same = (self.same_count + self.alpha) / (self.total + self.alpha * K)
        p_diff = 1.0 - p_same
        probs = {s: p_diff/(K-1) for s in self.states if s != last_state}
        probs[last_state] = p_same
        return probs

class TemperatureScaling:
    def __init__(self, temp=1.0): self.temp = temp
    def calibrate(self, probs):
        if abs(self.temp-1.0)<1e-6: return probs
        scaled = {s: p**(1/self.temp) for s,p in probs.items()}
        tot = sum(scaled.values())
        return {s: p/tot for s,p in scaled.items()}

class OnlineBayesianWeight:
    def __init__(self, models, lr=0.1):
        self.models = models; self.eta = lr
        self.cum_loss = {m: 0.0 for m in models}

    def update(self, model_probs, actual):
        for m, probs in model_probs.items():
            p = probs.get(actual, 1e-12)
            self.cum_loss[m] += -math.log(p)

    def get_weights(self):
        losses = {m: self.cum_loss[m] for m in self.models}
        min_loss = min(losses.values())
        exp_weights = {m: math.exp(-self.eta*(losses[m]-min_loss)) for m in self.models}
        tot = sum(exp_weights.values())
        if tot < 1e-12: return {m: 1.0/len(self.models) for m in self.models}
        return {m: w/tot for m,w in exp_weights.items()}

# ========== 增强指标 ==========
class AdvancedMetrics:
    @staticmethod
    def ece(probs_list, actual_list, n_bins=10):
        confs, accs = [], []
        for pd, a in zip(probs_list, actual_list):
            confs.append(pd.get(a, 0.0))
            accs.append(1.0)
        if not confs: return 0, [], []
        bins = np.linspace(0, 1, n_bins+1)
        idx = np.digitize(confs, bins[1:])
        ece = 0.0
        for b in range(n_bins):
            mask = idx == b
            n = np.sum(mask)
            if n == 0: continue
            avg_c = np.mean(np.array(confs)[mask])
            avg_a = np.mean(np.array(accs)[mask])
            ece += (n/len(confs)) * abs(avg_c - avg_a)
        return ece, [], []

    @staticmethod
    def entropy_decomposition(sub_probs_list, fused_list):
        total_ent, avg_exp = 0.0, 0.0
        n = len(fused_list)
        if n == 0: return 0,0,0
        for fused, subs in zip(fused_list, sub_probs_list):
            ht = -sum(p*math.log(p+1e-12) for p in fused.values() if p>0)
            total_ent += ht
            exp = 0.0
            for probs in subs.values():
                exp += -sum(p*math.log(p+1e-12) for p in probs.values() if p>0)
            exp /= len(subs)
            avg_exp += exp
        total_ent /= n; avg_exp /= n
        mi = total_ent - avg_exp
        return total_ent, avg_exp, mi

# ========== 集成引擎 ==========
class AttributeEngineV9_3:
    def __init__(self, name, markov_order=2, temperature=1.0, use_hmm=True, hmm_hidden=6, hmm_reg=0.25, decay=1.0):
        self.name = name; self.states = ATTRIBUTE_STATES[name]
        self.markov_order = markov_order; self.use_hmm = use_hmm
        self.temp_scaler = TemperatureScaling(temperature); self.decay = decay

        self.markov = MarkovN(markov_order, self.states, alpha=1.2)
        self.markov2 = MarkovN(max(1, markov_order-1), self.states, alpha=1.2)  # 异阶
        self.streak = StreakBias(self.states, alpha=1.2)
        self.freq = FrequencyPrior(self.states)
        self.momentum = MomentumModel(self.states)
        self.hmm = StableHMM(hmm_hidden, len(self.states), self.states, hmm_reg) if use_hmm else None

        self.tail = FeatureConditionalModel(self.states)
        self.mod7 = FeatureConditionalModel(self.states)
        self.cross = FeatureConditionalModel(self.states)
        self.zone = FeatureConditionalModel(self.states)

        self.sub_models = ["markov","markov2","streak","freq","momentum"]
        if use_hmm: self.sub_models.append("hmm")
        self.sub_models += ["tail","mod7","cross","zone"]
        self.weight_learner = OnlineBayesianWeight(self.sub_models, lr=0.1)

    def train(self, seq, draws):
        d = self.decay
        self.markov.train(seq, d); self.markov2.train(seq, d)
        self.streak.train(seq); self.freq.train(seq, d); self.momentum.train(seq)
        if self.hmm and len(seq)>40: self.hmm.train(seq)
        tails = [get_tail(d["num"]) for d in draws]
        mod7s = [get_mod7(d["num"]) for d in draws]
        zones = [get_zone(d["num"]) for d in draws]
        cross_bins = []
        for i in range(len(draws)):
            if i>0: cross_bins.append(bin_cross_distance(get_cross_distance(draws[i-1]["num"], draws[i]["num"])))
            else: cross_bins.append(0)
        self.tail.train(seq, tails, d); self.mod7.train(seq, mod7s, d)
        self.zone.train(seq, zones, d); self.cross.train(seq, cross_bins, d)

    def partial_train(self, prev_state, curr_state, prev_draw, curr_draw, weight=1.0):
        self.freq.partial_fit(curr_state, weight)
        self.momentum.partial_fit(prev_state, curr_state, weight)
        tail = get_tail(curr_draw["num"]); mod7 = get_mod7(curr_draw["num"])
        zone = get_zone(curr_draw["num"]); cross = bin_cross_distance(get_cross_distance(prev_draw["num"], curr_draw["num"]))
        self.tail.partial_fit(curr_state, tail, weight); self.mod7.partial_fit(curr_state, mod7, weight)
        self.zone.partial_fit(curr_state, zone, weight); self.cross.partial_fit(curr_state, cross, weight)
        self.streak.partial_update(curr_state)

    def _sub_preds(self, recent_seq, recent_draws):
        preds = {}
        ctx = tuple(recent_seq[-self.markov_order:]) if len(recent_seq)>=self.markov_order else tuple()
        ctx2 = tuple(recent_seq[-self.markov2.order:]) if len(recent_seq)>=self.markov2.order else tuple()
        preds["markov"] = self.markov.predict(ctx) if ctx else {s:1.0/len(self.states) for s in self.states}
        preds["markov2"] = self.markov2.predict(ctx2) if ctx2 else {s:1.0/len(self.states) for s in self.states}
        if recent_seq:
            last = recent_seq[-1]; streak_len = 1
            for i in range(len(recent_seq)-2,-1,-1):
                if recent_seq[i]==last: streak_len+=1
                else: break
            preds["streak"] = self.streak.predict(last, streak_len)
            preds["momentum"] = self.momentum.predict(last)
        else:
            preds["streak"] = {s:1.0/len(self.states) for s in self.states}
            preds["momentum"] = {s:1.0/len(self.states) for s in self.states}
        preds["freq"] = self.freq.predict()
        if self.hmm and len(recent_seq)>=3: preds["hmm"] = self.hmm.predict_next_probs(recent_seq)
        elif self.use_hmm: preds["hmm"] = {s:1.0/len(self.states) for s in self.states}
        if recent_draws:
            prev = recent_draws[-2] if len(recent_draws)>=2 else None
            curr = recent_draws[-1]
            preds["tail"] = self.tail.predict(get_tail(curr["num"]))
            preds["mod7"] = self.mod7.predict(get_mod7(curr["num"]))
            preds["zone"] = self.zone.predict(get_zone(curr["num"]))
            preds["cross"] = self.cross.predict(bin_cross_distance(get_cross_distance(prev["num"], curr["num"]) if prev else 0))
        else:
            for f in ["tail","mod7","cross","zone"]: preds[f] = {s:1.0/len(self.states) for s in self.states}
        return preds

    def predict_proba(self, recent_seq, recent_draws):
        sub = self._sub_preds(recent_seq, recent_draws)
        w = self.weight_learner.get_weights()
        fused = {s:0.0 for s in self.states}
        for m in self.sub_models:
            for s in self.states: fused[s] += w.get(m,0) * sub[m].get(s, 1.0/len(self.states))
        s = sum(fused.values()) or 1.0
        fused = {k: v/s for k,v in fused.items()}
        return self.temp_scaler.calibrate(fused), sub

    def update_weights(self, recent_seq, recent_draws, actual):
        sub = self._sub_preds(recent_seq, recent_draws)
        self.weight_learner.update(sub, actual)

# ========== 预测系统 V9.3 ==========
class PredictionSystemV9_3:
    def __init__(self, order=4, min_ig=0.45, temperature=1.0, use_hmm=True, hmm_hidden=6, hmm_reg=0.25,
                 decay=1.0, entropy_pct=30, mi_threshold=0.15, regime_window=30, regime_min_delta=0.02):
        self.order = order; self.min_ig = min_ig; self.temp = temperature
        self.use_hmm = use_hmm; self.decay = decay; self.entropy_pct = entropy_pct
        self.mi_threshold = mi_threshold
        self.regime_window = regime_window
        self.regime_min_delta = regime_min_delta
        self.entropy_history: List[float] = []
        self.engines = {
            "color": AttributeEngineV9_3("color", markov_order=2 if order>2 else order, temperature=temperature,
                                         use_hmm=use_hmm, hmm_hidden=hmm_hidden, hmm_reg=hmm_reg, decay=decay),
            "size": AttributeEngineV9_3("size", markov_order=2, temperature=temperature,
                                        use_hmm=use_hmm, hmm_hidden=hmm_hidden, hmm_reg=hmm_reg, decay=decay),
            "odd_even": AttributeEngineV9_3("odd_even", markov_order=1, temperature=temperature,
                                            use_hmm=use_hmm, hmm_hidden=hmm_hidden, hmm_reg=hmm_reg, decay=decay)
        }
        self.platt = {n: PlattCalibrator() for n in self.engines}
        self.platt_fitted = False

    def train_all(self, seqs, draws):
        for n, s in seqs.items(): self.engines[n].train(s, draws[n])

    def learn_weights_offline(self, seqs, draws, warmup=50):
        for n, eng in self.engines.items():
            s, d = seqs[n], draws[n]
            for i in range(warmup, len(s)-1):
                rec = s[max(0,i-self.order):i]; rd = d[max(0,i-self.order):i]
                eng.update_weights(rec, rd, s[i])

    def predict_all(self, recents, recent_draws):
        res, subs = {}, {}
        for n, eng in self.engines.items():
            probs, sub = eng.predict_proba(recents[n], recent_draws[n])
            subs[n] = sub
            sp = sorted(probs.items(), key=lambda x:-x[1])
            res[n] = {"probs":probs, "max_prob":sp[0][1], "best":sp[0][0], "second":sp[1][0] if len(sp)>1 else None,
                      "entropy": -sum(p*math.log(p+1e-12) for p in probs.values())}
        avg_max = np.mean([res[n]["max_prob"] for n in self.engines])
        color_ent = res["color"]["entropy"]
        if len(self.entropy_history)<20: thresh = math.log(len(ATTRIBUTE_STATES["color"]))*0.95
        else: thresh = np.percentile(self.entropy_history, self.entropy_pct)
        # MI 过滤
        _, _, mi_c = self._calc_mi(subs["color"], res["color"]["probs"])
        _, _, mi_s = self._calc_mi(subs["size"], res["size"]["probs"])
        _, _, mi_o = self._calc_mi(subs["odd_even"], res["odd_even"]["probs"])
        mi_override = mi_c>self.mi_threshold or mi_s>self.mi_threshold or mi_o>self.mi_threshold
        final_ig = self.min_ig + (0.1 if mi_override else 0.0)
        should_act = (avg_max >= final_ig) and (color_ent <= thresh)
        self.entropy_history.append(color_ent)
        res["meta"] = {"should_act":should_act, "avg_max":avg_max, "entropy":color_ent, "thresh":thresh, "mi_override":mi_override}
        res["_sub"] = subs
        return res

    def _calc_mi(self, subs, fused):
        total_ent = -sum(p*math.log(p+1e-12) for p in fused.values() if p>0)
        exp_ent = 0.0
        for probs in subs.values():
            exp_ent += -sum(p*math.log(p+1e-12) for p in probs.values() if p>0)
        exp_ent /= len(subs)
        return total_ent, exp_ent, total_ent - exp_ent

    def walk_forward_backtest(self, seqs, draws, test_len=150):
        res = {"act": {"total":0, "acc":{n:0 for n in self.engines}},
               "all": {"total":0, "acc":{n:0 for n in self.engines}},
               "delta_logloss": {n: [] for n in self.engines},
               "regime_active": []}
        uniform_loss = {n: -math.log(1.0/len(ATTRIBUTE_STATES[n])) for n in self.engines}
        ece_data = {n: {"probs":[], "actuals":[]} for n in self.engines}
        sub_probs = {n: [] for n in self.engines}; fused_probs = {n: [] for n in self.engines}
        delta_window = {n: [] for n in self.engines}

        start = max(len(seqs["color"])-test_len, self.order+40)
        train_s = {n: seqs[n][:start] for n in seqs}
        train_d = {n: draws[n][:start] for n in draws}
        self.train_all(train_s, train_d)
        self.learn_weights_offline(seqs, draws, warmup=self.order+20)

        for idx in range(start, len(seqs["color"])-1):
            rec = {n: seqs[n][max(0,idx-self.order):idx] for n in self.engines}
            rd = {n: draws[n][max(0,idx-self.order):idx] for n in self.engines}
            pred = self.predict_all(rec, rd)
            act = pred["meta"]["should_act"]
            actuals = {n: seqs[n][idx] for n in self.engines}

            # Regime 择时
            regime_ok = True
            if idx - start >= self.regime_window:
                recent_deltas = []
                for n in self.engines:
                    if len(delta_window[n]) >= self.regime_window:
                        recent_deltas.append(np.mean(delta_window[n][-self.regime_window:]))
                if recent_deltas and np.mean(recent_deltas) < self.regime_min_delta:
                    regime_ok = False
            execute = act and regime_ok

            for n in self.engines:
                prob = pred[n]["probs"].get(actuals[n], 1e-12)
                delta = uniform_loss[n] - (-math.log(prob))
                res["delta_logloss"][n].append(delta)
                delta_window[n].append(delta)
                if execute:
                    res["act"]["acc"][n] += (pred[n]["best"] == actuals[n])
                res["all"]["acc"][n] += (pred[n]["best"] == actuals[n])
                ece_data[n]["probs"].append(pred[n]["probs"])
                ece_data[n]["actuals"].append(actuals[n])
                sub_probs[n].append(pred["_sub"][n])
                fused_probs[n].append(pred[n]["probs"])

            res["act"]["total"] += 1 if execute else 0
            res["all"]["total"] += 1
            res["regime_active"].append(regime_ok)

            # 增量更新
            for n, eng in self.engines.items():
                pd_ = draws[n][idx-1] if idx>0 else draws[n][idx]
                eng.partial_train(seqs[n][idx-1] if idx>0 else None, actuals[n], pd_, draws[n][idx])
                recent_seq = seqs[n][max(0,idx-self.order):idx+1]
                if len(recent_seq)>=self.order+1:
                    ctx = tuple(recent_seq[-(self.order+1):-1])
                    eng.markov.partial_fit(ctx, recent_seq[-1])
                    ctx2 = tuple(recent_seq[-eng.markov2.order:]) if len(recent_seq)>=eng.markov2.order else tuple()
                    if ctx2: eng.markov2.partial_fit(ctx2, recent_seq[-1])
                eng.update_weights(rec[n], rd[n], actuals[n])

        def safe(a,b): return a/b if b>0 else 0.0
        r = {}
        r["act"] = {"total": res["act"]["total"], "acc": {n: safe(res["act"]["acc"][n], res["act"]["total"]) for n in self.engines}}
        r["all"] = {"total": res["all"]["total"], "acc": {n: safe(res["all"]["acc"][n], res["all"]["total"]) for n in self.engines}}
        r["avg_delta"] = {n: np.mean(res["delta_logloss"][n]) if res["delta_logloss"][n] else 0 for n in self.engines}
        wilc = {n: wilcoxon_signed_rank_test(res["delta_logloss"][n])[1] if res["delta_logloss"][n] else 1.0 for n in self.engines}
        r["wilcoxon_p"] = wilc
        ece_r = {n: AdvancedMetrics.ece(ece_data[n]["probs"], ece_data[n]["actuals"])[0] for n in self.engines}
        r["ece"] = ece_r
        ent = {n: AdvancedMetrics.entropy_decomposition(sub_probs[n], fused_probs[n]) for n in self.engines}
        r["entropy_decomp"] = {n: {"total":ent[n][0], "exp":ent[n][1], "mi":ent[n][2]} for n in self.engines}
        active_periods = sum(res["regime_active"])
        r["regime_coverage"] = active_periods / len(res["regime_active"]) if res["regime_active"] else 0
        return r

# ========== 仪表盘 ==========
def print_dashboard(conn, lottery_name, args):
    seqs = {"color": load_sequence(conn, get_color, 500), "size": load_sequence(conn, get_big_small, 500), "odd_even": load_sequence(conn, get_odd_even, 500)}
    draws = load_full_draws(conn, 500)
    draws_dict = {k: draws for k in seqs}
    if len(seqs["color"])<100:
        print("数据不足"); return

    if args.auto_temp:
        print("🔧 自动搜索最佳温度 (基于波色 ECE)...")
        best_temp, best_ece = 1.0, float('inf')
        for temp in [0.6,0.8,1.0,1.2,1.5]:
            sys_tmp = PredictionSystemV9_3(order=args.order, temperature=temp, use_hmm=args.use_hmm, hmm_hidden=args.hmm_hidden, hmm_reg=args.hmm_reg, decay=args.decay, mi_threshold=0.15, regime_window=args.regime_window, regime_min_delta=args.regime_min_delta)
            result = sys_tmp.walk_forward_backtest(seqs, draws_dict, test_len=args.backtest)
            ece = result["ece"]["color"]
            if ece < best_ece: best_ece, best_temp = ece, temp
        use_temp = best_temp
        print(f"   最佳温度: {use_temp}, ECE={best_ece:.4f}")
    else:
        use_temp = args.temp

    system = PredictionSystemV9_3(order=args.order, min_ig=args.min_ig, temperature=use_temp,
                                  use_hmm=args.use_hmm, hmm_hidden=args.hmm_hidden, hmm_reg=args.hmm_reg,
                                  decay=args.decay, entropy_pct=args.entropy_pct, mi_threshold=0.15,
                                  regime_window=args.regime_window, regime_min_delta=args.regime_min_delta)
    system.train_all(seqs, draws_dict)
    system.learn_weights_offline(seqs, draws_dict, warmup=50)

    recents = {n: seqs[n][-max(args.order,4):] for n in seqs}
    rd = {n: draws[-max(args.order,4):] for n in seqs}
    pred = system.predict_all(recents, rd)

    print(f"\n🔮 下一期预测 {lottery_name} (V9.3 最终版)")
    for name in ["color","size","odd_even"]:
        d = pred[name]
        print(f"\n{name}:")
        for s,p in sorted(d["probs"].items(), key=lambda x:-x[1]):
            mark = " ✓" if s==d["best"] else ""
            print(f"   {s}: {p*100:.1f}%{mark}")
    sorted_c = sorted(pred["color"]["probs"].items(), key=lambda x:-x[1])
    print(f"\n🎯 推荐波色: {sorted_c[0][0]} + {sorted_c[1][0]}")
    meta = pred["meta"]
    print(f"\n🧠 决策: {'出手' if meta['should_act'] else '观望'} (avg_max={meta['avg_max']:.3f}, entropy={meta['entropy']:.3f})")

    print(f"\n📊 回测 (最近 {args.backtest} 期, Regime 窗口={args.regime_window}, 阈值={args.regime_min_delta}):")
    result = system.walk_forward_backtest(seqs, draws_dict, test_len=args.backtest)
    print("\n--- 核心指标 ---")
    for n in ["color","size","odd_even"]:
        a_act = result["act"]["acc"][n]*100; a_all = result["all"]["acc"][n]*100
        delta = result["avg_delta"][n]; p = result["wilcoxon_p"][n]
        print(f"{n}: 出手准确率 {a_act:.1f}% | 全量 {a_all:.1f}% | ΔLogLoss={delta:+.4f} (p={p:.4f})")
    print(f"\n--- Regime 覆盖度: {result['regime_coverage']*100:.1f}% 的时间允许交易 ---")
    print("\n--- 校准误差 ---")
    for n in ["color","size","odd_even"]:
        print(f"{n}: ECE = {result['ece'][n]:.4f}")
    print("\n--- 熵分解 ---")
    for n in ["color","size","odd_even"]:
        e = result["entropy_decomp"][n]
        print(f"{n}: Total={e['total']:.4f} Exp={e['exp']:.4f} MI={e['mi']:.4f}")

    if lottery_name == "香港彩":
        print("\n🔍 香港彩波色聚焦分析:")
        hk_c_delta = result["avg_delta"]["color"]
        hk_c_p = result["wilcoxon_p"]["color"]
        if hk_c_delta > 0.02 and hk_c_p < 0.05:
            print("   ✅ 信号显著且正向，建议在 regime 允许时轻仓下注。")
        else:
            print("   ⚠️ 信号未达稳健标准，继续观察。")

def main():
    p = argparse.ArgumentParser(description="V9.3 最终优化版")
    p.add_argument("--lottery", choices=["老澳门彩","香港彩","新澳门彩"])
    p.add_argument("--order", type=int, default=4)
    p.add_argument("--min-ig", type=float, default=0.45)
    p.add_argument("--temp", type=float, default=0.85)
    p.add_argument("--auto-temp", action="store_true")
    p.add_argument("--use-hmm", action="store_true", default=True)
    p.add_argument("--no-hmm", dest="use_hmm", action="store_false")
    p.add_argument("--hmm-hidden", type=int, default=6)
    p.add_argument("--hmm-reg", type=float, default=0.25)
    p.add_argument("--decay", type=float, default=0.99)
    p.add_argument("--backtest", type=int, default=150)
    p.add_argument("--entropy-pct", type=int, default=30)
    p.add_argument("--regime-window", type=int, default=30, help="Regime 滑动窗口期数")
    p.add_argument("--regime-min-delta", type=float, default=0.02, help="Regime 最低平均 ΔLogLoss 阈值")
    args = p.parse_args()

    for lot in ([args.lottery] if args.lottery else ["老澳门彩","香港彩","新澳门彩"]):
        db = str(SCRIPT_DIR / DB_FILES[lot])
        conn = connect_db(db)
        try:
            init_db(conn)
            recs, src, url = fetch_online_records(lot)
            t, i, u = sync_from_records(conn, recs, src)
            print(f"{lot} 同步: {t} 条 (新增{i}, 更新{u})")
            print_dashboard(conn, lot, args)
        except Exception as e:
            print(f"错误 {lot}: {e}")
        finally:
            conn.close()

if __name__ == "__main__":
    main()
