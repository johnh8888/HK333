#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三彩种属性预测 V9.0 Research Suite
融合三条路线：
1. 统计学版：条件置换检验 + 多重校正
2. 金融化：信号强度、IC分析、累积收益
3. 强化学习：在线策略梯度优化 should_act 决策
"""

from __future__ import annotations

import argparse, json, sqlite3, math, ssl, sys, time, random, copy
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
# 假设赔率（下注成功返奖率，用于金融评估）
PAYOFF_MULTIPLIER = {"color": 2.0, "size": 1.95, "odd_even": 1.95}  # 例如赔率1:2

# ========== 属性映射 ==========
def get_color(num: int) -> str:
    RED = {1,2,7,8,12,13,18,19,23,24,29,30,34,35,40,45,46}
    BLUE = {3,4,9,10,14,15,20,25,26,31,36,37,41,42,47,48}
    GREEN = {5,6,11,16,17,21,22,27,28,32,33,38,39,43,44,49}
    if num in RED: return "红"
    if num in BLUE: return "蓝"
    return "绿"

def get_big_small(num: int) -> str:
    return "大" if num >= 25 else "小"

def get_odd_even(num: int) -> str:
    return "单" if num % 2 == 1 else "双"

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
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int

def utc_now() -> str: return datetime.now(timezone.utc).isoformat()

def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS draws (
            issue_no TEXT PRIMARY KEY,
            draw_date TEXT NOT NULL,
            numbers_json TEXT NOT NULL,
            special_number INTEGER NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
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
    try:
        latest_open_time = datetime.strptime(target.get("openTime", ""), "%Y-%m-%d %H:%M:%S")
    except:
        latest_open_time = datetime.now()
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
        except Exception as e:
            print(f"从 {url} 获取 {lottery_name} 数据失败: {e}")
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

def load_sequence(conn, attr_func, limit: int = 500) -> List[str]:
    rows = conn.execute("SELECT special_number FROM draws ORDER BY issue_no ASC LIMIT ?", (limit,)).fetchall()
    return [attr_func(r["special_number"]) for r in rows]

def load_full_draws(conn, limit: int = 500) -> List[Dict]:
    rows = conn.execute("SELECT special_number, draw_date FROM draws ORDER BY issue_no ASC LIMIT ?", (limit,)).fetchall()
    return [{"num": r["special_number"], "date": r["draw_date"]} for r in rows]

# ========== 纯 Python 二项检验 ==========
def binomial_p_value(k: int, n: int, p0: float) -> float:
    from math import comb
    if k <= 0: return 1.0
    total = 0.0
    for i in range(k, n + 1):
        total += comb(n, i) * (p0 ** i) * ((1 - p0) ** (n - i))
    return total

# ========== 多重检验校正 ==========
def apply_bonferroni(p_values: List[float]) -> List[float]:
    m = len(p_values)
    return [min(p * m, 1.0) for p in p_values]

def apply_bh(p_values: List[float], alpha: float = 0.05) -> List[bool]:
    """Benjamini-Hochberg 过程，返回是否拒绝"""
    m = len(p_values)
    sorted_indices = np.argsort(p_values)
    sorted_p = np.array(p_values)[sorted_indices]
    reject = np.zeros(m, dtype=bool)
    for i in range(m-1, -1, -1):
        if sorted_p[i] <= (i+1) / m * alpha:
            reject[sorted_indices[:i+1]] = True
            break
    return reject.tolist()

# ========== StableHMM ==========
class StableHMM:
    def __init__(self, n_hidden: int = 6, n_obs: int = 3, states_list: List[str] = None, reg_factor: float = 0.25):
        self.n_hidden = n_hidden; self.n_obs = n_obs
        self.states_list = states_list
        self.obs_to_idx = {s: i for i, s in enumerate(states_list)} if states_list else {}
        self.reg_factor = reg_factor
        self.eps = 1e-10
        self.pi = np.ones(n_hidden) / n_hidden
        self.A = np.ones((n_hidden, n_hidden)) / n_hidden
        self.B = np.ones((n_hidden, n_obs)) / n_obs

    def train(self, obs_seq: List[str], max_iter: int = 70):
        if len(obs_seq) < 40: return
        obs_idx = np.array([self.obs_to_idx[o] for o in obs_seq])
        T = len(obs_idx)
        for _ in range(max_iter):
            log_alpha = np.full((T, self.n_hidden), -np.inf)
            log_alpha[0] = np.log(self.pi + self.eps) + np.log(self.B[:, obs_idx[0]] + self.eps)
            for t in range(1, T):
                tmp = log_alpha[t-1][:, None] + np.log(self.A + self.eps)
                log_alpha[t] = np.logaddexp.reduce(tmp, axis=0) + np.log(self.B[:, obs_idx[t]] + self.eps)
            log_beta = np.full((T, self.n_hidden), -np.inf)
            log_beta[-1] = 0.0
            for t in range(T-2, -1, -1):
                tmp = np.log(self.A + self.eps) + np.log(self.B[:, obs_idx[t+1]] + self.eps) + log_beta[t+1]
                log_beta[t] = np.logaddexp.reduce(tmp, axis=1)
            log_gamma = log_alpha + log_beta
            log_gamma -= np.logaddexp.reduce(log_gamma, axis=1, keepdims=True)
            gamma = np.exp(log_gamma)
            self.pi = gamma[0] / (np.sum(gamma[0]) + self.eps)
            xi_sum = np.zeros((self.n_hidden, self.n_hidden))
            for t in range(T-1):
                tmp = log_alpha[t][:, None] + np.log(self.A + self.eps) + np.log(self.B[:, obs_idx[t+1]] + self.eps) + log_beta[t+1]
                log_xi = tmp - np.logaddexp.reduce(tmp, axis=1, keepdims=True)
                xi_sum += np.exp(log_xi)
            self.A = xi_sum / (np.sum(gamma[:-1], axis=0)[:, None] + self.eps)
            uniform = np.full((self.n_hidden, self.n_hidden), 1.0 / self.n_hidden)
            self.A = (1 - self.reg_factor) * self.A + self.reg_factor * uniform
            self.A /= np.sum(self.A, axis=1, keepdims=True) + self.eps
            self.B = np.zeros((self.n_hidden, self.n_obs))
            for t in range(T):
                self.B[:, obs_idx[t]] += gamma[t]
            self.B += self.eps
            self.B /= np.sum(self.B, axis=1, keepdims=True) + self.eps

    def predict_next_probs(self, obs_seq: List[str]) -> Dict[str, float]:
        if len(obs_seq) < 3: return {s: 1.0/len(self.states_list) for s in self.states_list}
        obs_idx = [self.obs_to_idx[o] for o in obs_seq]
        T = len(obs_idx)
        log_alpha = np.full((T, self.n_hidden), -np.inf)
        log_alpha[0] = np.log(self.pi + self.eps) + np.log(self.B[:, obs_idx[0]] + self.eps)
        for t in range(1, T):
            tmp = log_alpha[t-1][:, None] + np.log(self.A + self.eps)
            log_alpha[t] = np.logaddexp.reduce(tmp, axis=0) + np.log(self.B[:, obs_idx[t]] + self.eps)
        log_gamma = log_alpha[-1]
        gamma = np.exp(log_gamma - np.logaddexp.reduce(log_gamma))
        gamma /= np.sum(gamma) + self.eps
        next_hidden = gamma @ self.A
        next_probs = next_hidden @ self.B
        next_probs = np.clip(next_probs, self.eps, 1.0)
        next_probs /= np.sum(next_probs)
        return {self.states_list[i]: float(next_probs[i]) for i in range(self.n_obs)}

# ========== 子模型组件 ==========
class FeatureConditionalModel:
    def __init__(self, states: List[str], alpha: float = 2.0, min_count: int = 5):
        self.states = states; self.alpha = alpha; self.min_count = min_count
        self.counts = defaultdict(lambda: defaultdict(float)); self.total = defaultdict(float)

    def train(self, seq: List[str], feature_values: List[Any], decay_factor: float = 1.0):
        weight = 1.0
        for s, fv in zip(reversed(seq), reversed(feature_values)):
            self.counts[fv][s] += weight
            self.total[fv] += weight
            weight *= decay_factor

    def partial_fit(self, state: str, feature_value: Any, weight: float = 1.0):
        self.counts[feature_value][state] += weight
        self.total[feature_value] += weight

    def predict(self, fv: Any) -> Dict[str, float]:
        K = len(self.states)
        total = self.total.get(fv, 0.0)
        if total < self.min_count: return {s: 1.0/K for s in self.states}
        probs = {s: (self.counts[fv].get(s,0.0)+self.alpha)/(total+self.alpha*K) for s in self.states}
        sum_p = sum(probs.values())
        return {s: p/sum_p for s, p in probs.items()} if sum_p > 0 else {s: 1.0/K for s in self.states}

class MarkovN:
    def __init__(self, order: int, states: List[str], alpha: float = 1.2):
        self.order = order; self.states = states; self.alpha = alpha
        self.counts = defaultdict(Counter); self.total = defaultdict(int)

    def train(self, seq: List[str], decay_factor: float = 1.0):
        weight = 1.0
        for i in range(len(seq)-self.order-1, -1, -1):
            state = tuple(seq[i:i+self.order])
            nxt = seq[i+self.order]
            self.counts[state][nxt] += weight
            self.total[state] += weight
            weight *= decay_factor

    def partial_fit(self, context: Tuple[str], nxt: str, weight: float = 1.0):
        self.counts[context][nxt] += weight
        self.total[context] += weight

    def predict(self, context: Tuple[str]) -> Dict[str, float]:
        K = len(self.states)
        total = self.total.get(context, 0)
        probs = {s: (self.counts[context].get(s,0)+self.alpha)/(total+self.alpha*K) for s in self.states}
        sum_p = sum(probs.values())
        return {s: p/sum_p for s, p in probs.items()} if sum_p > 0 else {s: 1.0/K for s in self.states}

class StreakBias:
    def __init__(self, states: List[str], alpha: float = 1.2):
        self.states = states; self.alpha = alpha
        self.streak_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        self._current_state = None; self._streak_len = 0

    def train(self, seq: List[str]):
        i = 0
        while i < len(seq):
            j = i
            while j < len(seq) and seq[j] == seq[i]: j += 1
            length = j - i; state = seq[i]
            if j < len(seq): self.streak_counts[state][length][seq[j]] += 1.0
            i = j

    def partial_update(self, new_state: str):
        if self._current_state is None:
            self._current_state = new_state; self._streak_len = 1; return
        if new_state == self._current_state:
            self._streak_len += 1
        else:
            self.streak_counts[self._current_state][self._streak_len][new_state] += 1.0
            self._current_state = new_state; self._streak_len = 1

    def predict(self, last: str, streak_len: int) -> Dict[str, float]:
        K = len(self.states)
        total = sum(self.streak_counts[last][streak_len].values())
        probs = {s: (self.streak_counts[last][streak_len].get(s,0)+self.alpha)/(total+self.alpha*K) for s in self.states}
        sum_p = sum(probs.values())
        return {s: p/sum_p for s, p in probs.items()} if sum_p > 0 else {s: 1.0/K for s in self.states}

class FrequencyPrior:
    def __init__(self, states: List[str]):
        self.states = states; self.probs = {s: 1.0/len(states) for s in states}
        self._cnt = defaultdict(float); self._total = 0.0

    def train(self, seq: List[str], decay_factor: float = 1.0):
        if not seq: return
        weights = np.power(decay_factor, np.arange(len(seq)-1, -1, -1))
        for s, w in zip(reversed(seq), weights):
            self._cnt[s] += w; self._total += w
        self._update_probs()

    def partial_fit(self, state: str, weight: float = 1.0):
        self._cnt[state] += weight; self._total += weight
        self._update_probs()

    def _update_probs(self):
        if self._total > 0:
            self.probs = {s: self._cnt[s]/self._total for s in self.states}
            sum_p = sum(self.probs.values())
            self.probs = {s: p/sum_p for s, p in self.probs.items()}

    def predict(self) -> Dict[str, float]: return self.probs.copy()

class TemperatureScaling:
    def __init__(self, temperature: float = 1.0): self.temperature = temperature
    def calibrate(self, probs: Dict[str, float]) -> Dict[str, float]:
        if abs(self.temperature - 1.0) < 1e-6: return probs
        scaled = {s: p ** (1/self.temperature) for s, p in probs.items()}
        total = sum(scaled.values())
        return {s: p/total for s, p in scaled.items()}

class OnlineBayesianWeight:
    def __init__(self, models: List[str], learning_rate: float = 0.1):
        self.models = models; self.eta = learning_rate
        self.cum_loss = {m: 0.0 for m in models}

    def update(self, model_probs: Dict[str, Dict[str, float]], actual: str):
        for m, probs in model_probs.items():
            p = probs.get(actual, 1e-12)
            self.cum_loss[m] += -math.log(p)

    def get_weights(self) -> Dict[str, float]:
        losses = {m: self.cum_loss[m] for m in self.models}
        min_loss = min(losses.values())
        exp_weights = {m: math.exp(-self.eta * (losses[m] - min_loss)) for m in self.models}
        total = sum(exp_weights.values())
        if total < 1e-12: return {m: 1.0/len(self.models) for m in self.models}
        return {m: exp_weights[m]/total for m in self.models}

# ========== 集成引擎 ==========
class AttributeEngineV9:
    def __init__(self, name: str, markov_order: int = 2, temperature: float = 1.0,
                 use_hmm: bool = True, hmm_hidden: int = 6, hmm_reg: float = 0.25,
                 decay_factor: float = 1.0):
        self.name = name; self.states = ATTRIBUTE_STATES[name]
        self.markov_order = markov_order; self.use_hmm = use_hmm
        self.temp_scaler = TemperatureScaling(temperature); self.decay_factor = decay_factor

        self.markov = MarkovN(markov_order, self.states, alpha=1.2)
        self.streak = StreakBias(self.states, alpha=1.2)
        self.freq = FrequencyPrior(self.states)
        self.hmm = StableHMM(n_hidden=hmm_hidden, n_obs=len(self.states),
                             states_list=self.states, reg_factor=hmm_reg) if use_hmm else None
        self.tail_model = FeatureConditionalModel(self.states)
        self.mod7_model = FeatureConditionalModel(self.states)
        self.cross_model = FeatureConditionalModel(self.states)
        self.zone_model = FeatureConditionalModel(self.states)

        self.submodel_names = ["markov", "streak", "freq"]
        if use_hmm: self.submodel_names.append("hmm")
        self.submodel_names += ["tail", "mod7", "cross", "zone"]
        self.weight_learner = OnlineBayesianWeight(self.submodel_names, learning_rate=0.1)

    def train(self, seq: List[str], draws: List[Dict]):
        d = self.decay_factor
        self.markov.train(seq, decay_factor=d)
        self.streak.train(seq)
        self.freq.train(seq, decay_factor=d)
        if self.hmm and len(seq) > 40: self.hmm.train(seq)

        tails = [get_tail(d["num"]) for d in draws]
        mod7s = [get_mod7(d["num"]) for d in draws]
        zones = [get_zone(d["num"]) for d in draws]
        cross_bins = []
        for i in range(len(draws)):
            if i > 0:
                dist = get_cross_distance(draws[i-1]["num"], draws[i]["num"])
                cross_bins.append(bin_cross_distance(dist))
            else: cross_bins.append(0)

        self.tail_model.train(seq, tails, decay_factor=d)
        self.mod7_model.train(seq, mod7s, decay_factor=d)
        self.zone_model.train(seq, zones, decay_factor=d)
        self.cross_model.train(seq, cross_bins, decay_factor=d)

    def partial_train(self, prev_state: str, curr_state: str, prev_draw: Dict, curr_draw: Dict, weight: float = 1.0):
        self.freq.partial_fit(curr_state, weight)
        tail = get_tail(curr_draw["num"]); mod7 = get_mod7(curr_draw["num"])
        zone = get_zone(curr_draw["num"]); cross = bin_cross_distance(get_cross_distance(prev_draw["num"], curr_draw["num"]))
        self.tail_model.partial_fit(curr_state, tail, weight)
        self.mod7_model.partial_fit(curr_state, mod7, weight)
        self.zone_model.partial_fit(curr_state, zone, weight)
        self.cross_model.partial_fit(curr_state, cross, weight)
        self.streak.partial_update(curr_state)

    def _submodel_preds(self, recent_seq: List[str], recent_draws: List[Dict]) -> Dict[str, Dict[str, float]]:
        preds = {}
        context = tuple(recent_seq[-self.markov_order:]) if len(recent_seq) >= self.markov_order else tuple()
        preds["markov"] = self.markov.predict(context) if context else {s: 1.0/len(self.states) for s in self.states}
        streak_probs = {s: 1.0/len(self.states) for s in self.states}
        if recent_seq:
            last = recent_seq[-1]
            streak_len = 1
            for i in range(len(recent_seq)-2, -1, -1):
                if recent_seq[i] == last: streak_len += 1
                else: break
            streak_probs = self.streak.predict(last, streak_len)
        preds["streak"] = streak_probs
        preds["freq"] = self.freq.predict()
        if self.hmm and len(recent_seq) >= 3:
            preds["hmm"] = self.hmm.predict_next_probs(recent_seq)
        elif self.use_hmm:
            preds["hmm"] = {s: 1.0/len(self.states) for s in self.states}

        if recent_draws:
            prev = recent_draws[-2] if len(recent_draws) >= 2 else None
            curr = recent_draws[-1]
            preds["tail"] = self.tail_model.predict(get_tail(curr["num"]))
            preds["mod7"] = self.mod7_model.predict(get_mod7(curr["num"]))
            preds["zone"] = self.zone_model.predict(get_zone(curr["num"]))
            cross_val = bin_cross_distance(get_cross_distance(prev["num"], curr["num"]) if prev else 0)
            preds["cross"] = self.cross_model.predict(cross_val)
        else:
            for feat in ["tail", "mod7", "cross", "zone"]:
                preds[feat] = {s: 1.0/len(self.states) for s in self.states}
        return preds

    def predict_proba(self, recent_seq: List[str], recent_draws: List[Dict]) -> Dict[str, float]:
        sub_preds = self._submodel_preds(recent_seq, recent_draws)
        weights = self.weight_learner.get_weights()
        fused = {s: 0.0 for s in self.states}
        for m in self.submodel_names:
            w = weights.get(m, 0.0)
            for s in self.states:
                fused[s] += w * sub_preds[m].get(s, 1.0/len(self.states))
        total = sum(fused.values()) or 1.0
        fused = {s: p/total for s, p in fused.items()}
        return self.temp_scaler.calibrate(fused)

    def update_weights(self, recent_seq: List[str], recent_draws: List[Dict], actual_state: str):
        sub_preds = self._submodel_preds(recent_seq, recent_draws)
        self.weight_learner.update(sub_preds, actual_state)

# ========== 强化学习策略模块 ==========
class OnlineREINFORCE:
    """
    简单 REINFORCE 优化二元决策概率。
    输入：状态特征向量；输出：下注概率。
    """
    def __init__(self, input_dim: int = 2, lr: float = 0.01):
        self.weights = np.zeros(input_dim)
        self.lr = lr

    def predict_proba(self, state: np.ndarray) -> float:
        z = np.dot(state, self.weights)
        return 1.0 / (1.0 + math.exp(-z))  # sigmoid

    def update(self, state: np.ndarray, action: int, reward: float):
        """action 0 or 1, reward 为收益（如+1赢，-1输）"""
        prob = self.predict_proba(state)
        # 梯度 = state * (action - prob)
        self.weights += self.lr * (action - prob) * state * reward

# ========== 预测系统 V9 ==========
class PredictionSystemV9:
    def __init__(self, order: int = 4, min_ig: float = 0.45, temperature: float = 1.0,
                 use_hmm: bool = True, hmm_hidden: int = 6, hmm_reg: float = 0.25,
                 decay_factor: float = 1.0, entropy_percentile: int = 30):
        self.order = order; self.min_ig = min_ig; self.temperature = temperature
        self.use_hmm = use_hmm; self.decay_factor = decay_factor
        self.entropy_percentile = entropy_percentile
        self.entropy_history: List[float] = []

        self.engines = {
            "color": AttributeEngineV9("color", markov_order=2 if order > 2 else order,
                                       temperature=temperature, use_hmm=use_hmm,
                                       hmm_hidden=hmm_hidden, hmm_reg=hmm_reg, decay_factor=decay_factor),
            "size": AttributeEngineV9("size", markov_order=2, temperature=temperature,
                                      use_hmm=use_hmm, hmm_hidden=hmm_hidden, hmm_reg=hmm_reg, decay_factor=decay_factor),
            "odd_even": AttributeEngineV9("odd_even", markov_order=1, temperature=temperature,
                                          use_hmm=use_hmm, hmm_hidden=hmm_hidden, hmm_reg=hmm_reg, decay_factor=decay_factor)
        }
        # 强化学习模块
        self.rl_agent = OnlineREINFORCE(input_dim=2, lr=0.02)

    def train_all(self, seqs, draws):
        for name, seq in seqs.items():
            self.engines[name].train(seq, draws[name])

    def learn_weights_offline(self, seqs, draws, warmup=50):
        for name, engine in self.engines.items():
            seq = seqs[name]; draw_list = draws[name]
            for i in range(warmup, len(seq)-1):
                rec_seq = seq[i-self.order:i] if i >= self.order else seq[:i]
                rec_draws = draw_list[i-self.order:i] if i >= self.order else draw_list[:i]
                engine.update_weights(rec_seq, rec_draws, seq[i])

    def predict_all(self, recents, recent_draws) -> Dict[str, Any]:
        results = {}
        for name, engine in self.engines.items():
            probs = engine.predict_proba(recents[name], recent_draws[name])
            sorted_probs = sorted(probs.items(), key=lambda x: -x[1])
            results[name] = {
                "probs": probs,
                "max_prob": sorted_probs[0][1],
                "best_state": sorted_probs[0][0],
                "second_state": sorted_probs[1][0] if len(sorted_probs)>=2 else None,
                "entropy": -sum(p*math.log(p+1e-12) for p in probs.values())
            }
        avg_max = np.mean([results[n]["max_prob"] for n in self.engines])
        color_entropy = results["color"]["entropy"]
        if len(self.entropy_history) < 20:
            entropy_thr = math.log(len(ATTRIBUTE_STATES["color"])) * 0.95
        else:
            entropy_thr = np.percentile(self.entropy_history, self.entropy_percentile)
        # 固定规则 should_act
        should_act_fixed = (avg_max >= self.min_ig) and (color_entropy <= entropy_thr)
        # 强化学习决策
        state = np.array([avg_max, color_entropy])
        rl_action_prob = self.rl_agent.predict_proba(state)
        should_act_rl = random.random() < rl_action_prob  # 采样动作
        self.entropy_history.append(color_entropy)

        results["meta"] = {
            "should_act_fixed": should_act_fixed,
            "should_act_rl": should_act_rl,
            "rl_action_prob": rl_action_prob,
            "avg_max": avg_max,
            "entropy": color_entropy,
            "threshold": entropy_thr
        }
        return results

    def walk_forward_backtest(self, seqs, draws, test_len=150):
        total_act_fixed = total_act_rl = 0
        total_all = 0
        correct_act_fixed = {n:0 for n in self.engines}
        correct_act_rl = {n:0 for n in self.engines}
        correct_all = {n:0 for n in self.engines}
        # 金融指标
        cum_return_fixed = {n:0.0 for n in self.engines}
        cum_return_rl = {n:0.0 for n in self.engines}
        ic_records = {n:[] for n in self.engines}
        # 记录预测详情
        pred_records_act_fixed = {n:[] for n in self.engines}
        pred_records_act_rl = {n:[] for n in self.engines}
        pred_records_all = {n:[] for n in self.engines}

        min_len = self.order + 40
        start_idx = max(len(seqs["color"]) - test_len, min_len)
        train_seqs = {n: seqs[n][:start_idx] for n in seqs}
        train_draws = {n: draws[n][:start_idx] for n in draws}
        self.train_all(train_seqs, train_draws)
        self.learn_weights_offline(seqs, draws, warmup=min_len)

        for idx in range(start_idx, len(seqs["color"]) - 1):
            recents = {n: seqs[n][idx-self.order:idx] if idx>=self.order else seqs[n][:idx] for n in self.engines}
            recent_draws = {n: draws[n][idx-self.order:idx] if idx>=self.order else draws[n][:idx] for n in self.engines}
            pred = self.predict_all(recents, recent_draws)
            actuals = {n: seqs[n][idx] for n in self.engines}

            # 固定策略动作
            act_fixed = pred["meta"]["should_act_fixed"]
            act_rl = pred["meta"]["should_act_rl"]
            for name in self.engines:
                prob = pred[name]["probs"].get(actuals[name], 1e-12)
                # 金融信号（简单用 max_prob - 1/k）
                signal = pred[name]["max_prob"] - 1.0/len(ATTRIBUTE_STATES[name])
                # 收益（假设下注正确得赔率-1，否则-1）
                win = (pred[name]["best_state"] == actuals[name])
                reward_fixed = (PAYOFF_MULTIPLIER[name]-1) if win else -1
                if act_fixed:
                    cum_return_fixed[name] += reward_fixed
                    correct_act_fixed[name] += win
                    total_act_fixed += 1
                    pred_records_act_fixed[name].append((pred[name]["best_state"], actuals[name]))
                if act_rl:
                    cum_return_rl[name] += reward_fixed  # 同样奖励
                    correct_act_rl[name] += win
                    total_act_rl += 1
                    pred_records_act_rl[name].append((pred[name]["best_state"], actuals[name]))
                # 全量统计
                cum_return_rl[name] += 0  # 不影响
                correct_all[name] += win
                total_all += 1
                pred_records_all[name].append((pred[name]["best_state"], actuals[name]))
                # IC: signal vs 实际收益的相关系数（简化：存储信号和收益标签）
                ic_records[name].append((signal, 1 if win else -1))

            # 强化学习更新：根据固定策略是否盈利调整（这里用固定策略盈利作为 RL 的奖励？）
            # 我们使用 act_rl 产生的奖励更新 RL
            state = np.array([pred["meta"]["avg_max"], pred["meta"]["entropy"]])
            # 简单奖励：取所有属性的平均收益
            avg_reward = np.mean([(PAYOFF_MULTIPLIER[name]-1) if (pred[name]["best_state"]==actuals[name]) else -1 for name in self.engines])
            self.rl_agent.update(state, 1 if act_rl else 0, avg_reward)

            # 增量更新模型
            for name, engine in self.engines.items():
                prev_draw = draws[name][idx-1] if idx>0 else draws[name][idx]
                engine.partial_train(seqs[name][idx-1] if idx>0 else None, actuals[name], prev_draw, draws[name][idx])
                recent_seq = seqs[name][max(0, idx-self.order):idx+1]
                if len(recent_seq) >= self.order+1:
                    ctx = tuple(recent_seq[-(self.order+1):-1])
                    engine.markov.partial_fit(ctx, recent_seq[-1])
            for name, engine in self.engines.items():
                engine.update_weights(recents[name], recent_draws[name], actuals[name])

        # 汇总指标
        def safe_div(a,b): return a/b if b>0 else 0
        acc_fixed = {n: safe_div(correct_act_fixed[n], total_act_fixed) for n in self.engines}
        acc_rl = {n: safe_div(correct_act_rl[n], total_act_rl) for n in self.engines}
        acc_all = {n: safe_div(correct_all[n], total_all) for n in self.engines}
        # 金融指标
        sharpe_fixed = {n: np.mean([cum_return_fixed[n]])/(np.std([cum_return_fixed[n]])+1e-8) for n in self.engines}
        sharpe_rl = {n: np.mean([cum_return_rl[n]])/(np.std([cum_return_rl[n]])+1e-8) for n in self.engines}
        # 平均IC
        ic_mean = {}
        for n in self.engines:
            signals, returns = zip(*ic_records[n])
            ic_mean[n] = np.corrcoef(signals, returns)[0,1] if len(signals)>1 else 0

        return {
            "fixed": {"total":total_act_fixed, "acc":acc_fixed, "cum_return":cum_return_fixed, "sharpe":sharpe_fixed},
            "rl": {"total":total_act_rl, "acc":acc_rl, "cum_return":cum_return_rl, "sharpe":sharpe_rl},
            "all": {"total":total_all, "acc":acc_all},
            "ic": ic_mean,
            "pred_records_fixed": pred_records_act_fixed,
            "pred_records_rl": pred_records_act_rl,
            "pred_records_all": pred_records_all
        }

# ========== 条件置换检验（固定特征打乱颜色） ==========
def conditional_permutation_test(seqs, draws, base_system, test_len=150, n_perm=100):
    color_seq = seqs["color"]
    # 抽取与颜色序列对应的特征索引
    features = []
    for i in range(len(color_seq)):
        d = draws["color"][i]
        fv = (get_tail(d["num"]), get_mod7(d["num"]), get_zone(d["num"]))
        features.append(fv)
    # 真实系统准确率
    sys_real = copy.deepcopy(base_system)
    res_real = sys_real.walk_forward_backtest(seqs, draws, test_len=test_len)
    real_acc = res_real["fixed"]["acc"]["color"]

    perm_accs = []
    for _ in range(n_perm):
        # 在每个特征组合内部打乱颜色标签
        perm_color = color_seq.copy()
        groups = defaultdict(list)
        for idx, fv in enumerate(features):
            groups[fv].append(idx)
        for fv, indices in groups.items():
            vals = [color_seq[i] for i in indices]
            random.shuffle(vals)
            for i, idx in enumerate(indices):
                perm_color[idx] = vals[i]
        perm_seqs = {**seqs, "color": perm_color}
        sys_perm = PredictionSystemV9(
            order=base_system.order, min_ig=base_system.min_ig, temperature=base_system.temperature,
            use_hmm=base_system.use_hmm,
            hmm_hidden=base_system.engines["color"].hmm.n_hidden if base_system.engines["color"].hmm else 6,
            hmm_reg=base_system.engines["color"].hmm.reg_factor if base_system.engines["color"].hmm else 0.25,
            decay_factor=base_system.decay_factor, entropy_percentile=base_system.entropy_percentile
        )
        res_perm = sys_perm.walk_forward_backtest(perm_seqs, draws, test_len=test_len)
        perm_accs.append(res_perm["fixed"]["acc"]["color"])
    p_val = sum(1 for a in perm_accs if a >= real_acc) / n_perm
    return real_acc, perm_accs, p_val

# ========== 仪表盘 ==========
def print_dashboard(conn, lottery_name: str, args):
    seqs = {
        "color": load_sequence(conn, get_color, limit=500),
        "size": load_sequence(conn, get_big_small, limit=500),
        "odd_even": load_sequence(conn, get_odd_even, limit=500)
    }
    draws = load_full_draws(conn, limit=500)
    draws_dict = {"color": draws, "size": draws, "odd_even": draws}

    if len(seqs["color"]) < 100:
        print("历史数据不足，请先同步数据。"); return

    latest = conn.execute("SELECT * FROM draws ORDER BY issue_no DESC LIMIT 1").fetchone()
    if latest:
        nums = " ".join(f"{n:02d}" for n in json.loads(latest["numbers_json"]))
        print(f"最新开奖: {latest['issue_no']} | {nums} + {latest['special_number']:02d}")
        attrs = { "色波": get_color(latest["special_number"]), "大小": get_big_small(latest["special_number"]), "单双": get_odd_even(latest["special_number"]) }
        print(f"特码属性: {attrs['单双']} {attrs['大小']} {attrs['色波']}")

    system = PredictionSystemV9(
        order=args.order, min_ig=args.min_ig, temperature=args.temp,
        use_hmm=args.use_hmm, hmm_hidden=args.hmm_hidden, hmm_reg=args.hmm_reg,
        decay_factor=args.decay, entropy_percentile=args.entropy_percentile
    )
    system.train_all(seqs, draws_dict)
    system.learn_weights_offline(seqs, draws_dict, warmup=50)

    recents = {name: seqs[name][-max(args.order,4):] for name in seqs}
    recent_draws = {name: draws[-max(args.order,4):] for name in seqs}
    pred = system.predict_all(recents, recent_draws)

    print(f"\n🔮 下一期属性预测 {lottery_name} (V9.0 Research)")
    for name, data in pred.items():
        if name == "meta": continue
        print(f"\n{name}:")
        for s, p in sorted(data["probs"].items(), key=lambda x: -x[1]):
            marker = " ✓" if s == data["best_state"] else ""
            print(f"   {s}: {p*100:.1f}%{marker}")

    sorted_color = sorted(pred["color"]["probs"].items(), key=lambda x: -x[1])
    print(f"\n🎯 【推荐两个波色】: {sorted_color[0][0]} + {sorted_color[1][0]}")
    meta = pred["meta"]
    print(f"\n🧠 固定规则: {'出手' if meta['should_act_fixed'] else '观望'}")
    print(f"🤖 RL规则: {'出手' if meta['should_act_rl'] else '观望'} (概率 {meta['rl_action_prob']:.3f})")

    # 回测
    print(f"\n📊 在线增量 Walk-Forward 回测 (最近 {args.backtest} 期):")
    result = system.walk_forward_backtest(seqs, draws_dict, test_len=args.backtest)

    print("\n--- Coverage-aware 指标 ---")
    print(f"总预测期: {result['all']['total']}, 固定出手: {result['fixed']['total']}, RL出手: {result['rl']['total']}")
    for name in ["color","size","odd_even"]:
        a_f = result["fixed"]["acc"][name]*100; a_r = result["rl"]["acc"][name]*100; a_all = result["all"]["acc"][name]*100
        print(f"{name}: 固定准确率 {a_f:.1f}% | RL准确率 {a_r:.1f}% | 全量 {a_all:.1f}%")
    print("\n--- 金融指标 (假设赔率) ---")
    for name in ["color","size","odd_even"]:
        print(f"{name}: 固定累积收益 {result['fixed']['cum_return'][name]:+.2f}  Sharpe {result['fixed']['sharpe'][name]:.3f}")
        print(f"      RL累积收益 {result['rl']['cum_return'][name]:+.2f}  Sharpe {result['rl']['sharpe'][name]:.3f}")
    print("\n--- 信息系数 (IC) ---")
    for name in ["color","size","odd_even"]:
        print(f"{name}: 平均 IC = {result['ic'][name]:.4f}")

    # 条件置换检验
    if args.perm_test and len(seqs["color"]) > 120:
        print("\n🔄 条件置换检验 (固定特征，打乱颜色, 50次)...")
        real_acc, perm_accs, p_val = conditional_permutation_test(seqs, draws_dict, system, test_len=args.backtest, n_perm=50)
        print(f"   真实固定准确率: {real_acc:.3f}, 零分布均值: {np.mean(perm_accs):.3f}, p={p_val:.4f}")

    # HMM 贡献
    if args.compare_hmm and system.use_hmm:
        print("\n🔬 HMM 贡献检验:")
        sys_no = PredictionSystemV9(order=args.order, min_ig=args.min_ig, temperature=args.temp,
                                    use_hmm=False, decay_factor=args.decay, entropy_percentile=args.entropy_percentile)
        res_no = sys_no.walk_forward_backtest(seqs, draws_dict, test_len=args.backtest)
        for name in ["color","size","odd_even"]:
            diff = result["fixed"]["acc"][name] - res_no["fixed"]["acc"][name]
            print(f"   {name}: ΔAcc(固定)={diff*100:+.2f}%")

def main():
    p = argparse.ArgumentParser(description="三彩种属性预测 V9.0 Research Suite")
    p.add_argument("--lottery", choices=["老澳门彩","香港彩","新澳门彩"])
    p.add_argument("--order", type=int, default=4)
    p.add_argument("--min-ig", type=float, default=0.45)
    p.add_argument("--temp", type=float, default=0.85)
    p.add_argument("--use-hmm", action="store_true", default=True)
    p.add_argument("--no-hmm", dest="use_hmm", action="store_false")
    p.add_argument("--hmm-hidden", type=int, default=6)
    p.add_argument("--hmm-reg", type=float, default=0.25)
    p.add_argument("--decay", type=float, default=0.99)
    p.add_argument("--backtest", type=int, default=150)
    p.add_argument("--entropy-percentile", type=int, default=30)
    p.add_argument("--perm-test", action="store_true")
    p.add_argument("--compare-hmm", action="store_true")
    args = p.parse_args()

    for lottery in ([args.lottery] if args.lottery else ["老澳门彩","香港彩","新澳门彩"]):
        db_path = str(SCRIPT_DIR / DB_FILES[lottery])
        conn = connect_db(db_path)
        try:
            init_db(conn)
            records, source, url = fetch_online_records(lottery)
            total, ins, upd = sync_from_records(conn, records, source)
            print(f"{lottery} 同步: {total} 条 (新增{ins}, 更新{upd})")
            print_dashboard(conn, lottery, args)
        except Exception as e:
            print(f"错误 {lottery}: {e}")
        finally:
            conn.close()

if __name__ == "__main__":
    main()
