#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 三彩种属性预测 V7.3 (HMM 数值稳定版)

from __future__ import annotations

import argparse
import json
import sqlite3
import math
import ssl
import sys
import random
import time
from collections import defaultdict, Counter
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

import numpy as np

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

def get_big_small(num: int) -> str:
    return "大" if num >= 25 else "小"

def get_odd_even(num: int) -> str:
    return "单" if num % 2 else "双"

def get_tail(num: int) -> int:
    return num % 10

def get_mod7(num: int) -> int:
    return num % 7

def get_cross_distance(prev: int, cur: int) -> int:
    return abs(prev - cur)

# ========== 数据层 ==========
@dataclass
class DrawRecord:
    issue_no: str
    draw_date: str
    numbers: List[int]
    special_number: int

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

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
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1)
    raise RuntimeError("无法获取数据")

def parse_response(payload, lottery_name: str):
    records = []
    lottery_data = payload.get("lottery_data", [])
    target = next((l for l in lottery_data if l.get("name") == lottery_name), None)
    if not target:
        return records
    try:
        latest_open_time = datetime.strptime(target.get("openTime", ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
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
        except Exception:
            continue
    return records

def fetch_online_records(lottery_name: str):
    for url in THIRD_PARTY_URLS:
        try:
            payload = fetch_json_url(url)
            records = parse_response(payload, lottery_name)
            if records:
                return records, "marksix6", url
        except Exception as e:
            print(f"从 {url} 获取 {lottery_name} 数据失败: {e}")
    raise RuntimeError(f"无法获取 {lottery_name} 数据")

def upsert_draw(conn, record, source):
    now = utc_now()
    if conn.execute("SELECT 1 FROM draws WHERE issue_no=?", (record.issue_no,)).fetchone():
        conn.execute("""UPDATE draws SET draw_date=?, numbers_json=?, special_number=?, source=?, updated_at=?
                        WHERE issue_no=?""",
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
    rows = conn.execute("SELECT special_number FROM draws ORDER BY draw_date ASC, issue_no ASC LIMIT ?", (limit,)).fetchall()
    return [attr_func(r["special_number"]) for r in rows]

def load_full_draws(conn, limit: int = 500) -> List[Dict]:
    rows = conn.execute("SELECT special_number, draw_date FROM draws ORDER BY draw_date ASC, issue_no ASC LIMIT ?", (limit,)).fetchall()
    return [{"num": r["special_number"], "date": r["draw_date"]} for r in rows]

# ========== 稳定 HMM ==========
class StableHMM:
    def __init__(self, n_states: int, n_obs: int, states_list: List[str],
                 reg_factor: float = 0.12, early_stop_eps: float = 1e-4):
        self.n_states = n_states
        self.n_obs = n_obs
        self.states_list = states_list
        self.obs_to_idx = {s: i for i, s in enumerate(states_list)}
        self.reg_factor = reg_factor
        self.early_stop_eps = early_stop_eps
        self.eps = 1e-12

        self.pi = np.random.dirichlet(np.ones(n_states) * 2.0)
        self.A = np.random.dirichlet(np.ones(n_states) * 2.0, size=n_states)
        self.B = np.random.dirichlet(np.ones(n_obs) * 2.0, size=n_states)

    def _logsumexp(self, x, axis=None, keepdims=False):
        max_x = np.max(x, axis=axis, keepdims=True)
        res = max_x + np.log(np.sum(np.exp(x - max_x), axis=axis, keepdims=keepdims))
        return res.squeeze() if not keepdims else res

    def train(self, obs_seq: List[str], max_iter: int = 80):
        if len(obs_seq) < 20:
            return
        obs_idx = np.array([self.obs_to_idx[o] for o in obs_seq])
        T = len(obs_idx)

        prev_log_lik = -np.inf
        for it in range(max_iter):
            # Forward
            log_alpha = np.full((T, self.n_states), -np.inf)
            log_alpha[0] = np.log(self.pi + self.eps) + np.log(self.B[:, obs_idx[0]] + self.eps)

            for t in range(1, T):
                tmp = log_alpha[t-1][:, None] + np.log(self.A + self.eps)
                log_alpha[t] = self._logsumexp(tmp, axis=0) + np.log(self.B[:, obs_idx[t]] + self.eps)

            log_lik = self._logsumexp(log_alpha[-1])

            if it > 0 and abs(log_lik - prev_log_lik) < self.early_stop_eps:
                break
            prev_log_lik = log_lik

            # Backward
            log_beta = np.full((T, self.n_states), -np.inf)
            log_beta[-1] = 0.0
            for t in range(T-2, -1, -1):
                tmp = np.log(self.A + self.eps) + np.log(self.B[:, obs_idx[t+1]] + self.eps) + log_beta[t+1]
                log_beta[t] = self._logsumexp(tmp, axis=1)

            # Gamma
            log_gamma = log_alpha + log_beta
            log_gamma -= self._logsumexp(log_gamma, axis=1, keepdims=True)
            gamma = np.exp(log_gamma)

            # Xi
            xi = np.zeros((T-1, self.n_states, self.n_states))
            for t in range(T-1):
                tmp = (log_alpha[t][:, None] + np.log(self.A + self.eps) +
                       np.log(self.B[:, obs_idx[t+1]] + self.eps) + log_beta[t+1])
                log_xi = tmp - self._logsumexp(tmp, axis=1, keepdims=True)
                xi[t] = np.exp(log_xi)

            # Update parameters
            self.pi = gamma[0]
            self.pi /= self.pi.sum() + self.eps

            self.A = np.sum(xi, axis=0) / (np.sum(gamma[:-1], axis=0)[:, None] + self.eps)
            uniform = np.ones_like(self.A) / self.n_states
            self.A = (1 - self.reg_factor) * self.A + self.reg_factor * uniform
            self.A /= self.A.sum(axis=1, keepdims=True)

            self.B = np.zeros_like(self.B)
            for k in range(self.n_states):
                for t in range(T):
                    self.B[k, obs_idx[t]] += gamma[t, k]
            self.B += self.eps
            self.B /= self.B.sum(axis=1, keepdims=True)

    def predict_next_probs(self, obs_seq: List[str]) -> Dict[str, float]:
        if len(obs_seq) < 2:
            return {s: 1.0 / len(self.states_list) for s in self.states_list}

        obs_idx = [self.obs_to_idx[o] for o in obs_seq]
        T = len(obs_idx)

        log_alpha = np.full((T, self.n_states), -np.inf)
        log_alpha[0] = np.log(self.pi + self.eps) + np.log(self.B[:, obs_idx[0]] + self.eps)

        for t in range(1, T):
            tmp = log_alpha[t-1][:, None] + np.log(self.A + self.eps)
            log_alpha[t] = self._logsumexp(tmp, axis=0) + np.log(self.B[:, obs_idx[t]] + self.eps)

        log_gamma_T = log_alpha[-1] - self._logsumexp(log_alpha[-1])
        gamma_T = np.exp(log_gamma_T)

        next_hidden = gamma_T @ self.A
        next_probs = next_hidden @ self.B

        next_probs = np.clip(next_probs, self.eps, 1.0)
        next_probs /= next_probs.sum()

        return {self.states_list[i]: float(next_probs[i]) for i in range(self.n_obs)}

# ========== 辅助模型 ==========
class OnlineBayesianWeight:
    def __init__(self, models: List[str], alpha_prior: float = 1.0, beta_prior: float = 1.0):
        self.models = models
        self.alpha = {m: alpha_prior for m in models}
        self.beta = {m: beta_prior for m in models}

    def update(self, model: str, prob_actual: float):
        self.alpha[model] += prob_actual
        self.beta[model] += (1 - prob_actual)

    def get_weight(self, model: str) -> float:
        return self.alpha[model] / (self.alpha[model] + self.beta[model])

    def get_all_weights(self) -> Dict[str, float]:
        total = sum(self.get_weight(m) for m in self.models)
        if total < 1e-8:
            return {m: 1.0/len(self.models) for m in self.models}
        return {m: self.get_weight(m)/total for m in self.models}

class FeatureConditionalModel:
    def __init__(self, states: List[str]):
        self.states = states
        self.counts = defaultdict(lambda: defaultdict(int))
        self.total = defaultdict(int)

    def train(self, seq: List[str], feature_values: List[Any]):
        for s, fv in zip(seq, feature_values):
            self.counts[fv][s] += 1
            self.total[fv] += 1

    def predict(self, fv: Any, alpha: float = 1.0) -> Dict[str, float]:
        K = len(self.states)
        total = self.total.get(fv, 0)
        probs = {}
        for s in self.states:
            cnt = self.counts[fv].get(s, 0)
            probs[s] = (cnt + alpha) / (total + alpha * K)
        sum_p = sum(probs.values())
        return {s: p/sum_p for s, p in probs.items()} if sum_p > 0 else {s: 1.0/K for s in self.states}

class MarkovN:
    def __init__(self, order: int, states: List[str], alpha: float = 1.0):
        self.order = order
        self.states = states
        self.alpha = alpha
        self.counts = defaultdict(Counter)
        self.total = defaultdict(int)

    def train(self, seq: List[str]):
        for i in range(len(seq) - self.order):
            state = tuple(seq[i:i+self.order])
            nxt = seq[i+self.order]
            self.counts[state][nxt] += 1
            self.total[state] += 1

    def predict(self, context: Tuple[str]) -> Dict[str, float]:
        K = len(self.states)
        total = self.total.get(context, 0)
        probs = {}
        for s in self.states:
            cnt = self.counts[context].get(s, 0)
            probs[s] = (cnt + self.alpha) / (total + self.alpha * K)
        sum_p = sum(probs.values())
        return {s: p/sum_p for s, p in probs.items()} if sum_p > 0 else {s: 1.0/K for s in self.states}

class StreakBias:
    def __init__(self, states: List[str], alpha: float = 1.0):
        self.states = states
        self.alpha = alpha
        self.streak_counts = defaultdict(lambda: defaultdict(Counter))

    def update(self, seq: List[str]):
        i = 0
        while i < len(seq):
            j = i
            while j < len(seq) and seq[j] == seq[i]:
                j += 1
            length = j - i
            state = seq[i]
            if j < len(seq):
                nxt = seq[j]
                self.streak_counts[state][length][nxt] += 1
            i = j

    def predict(self, last: str, streak_len: int) -> Dict[str, float]:
        K = len(self.states)
        total = sum(self.streak_counts[last][streak_len].values())
        probs = {}
        for s in self.states:
            cnt = self.streak_counts[last][streak_len].get(s, 0)
            probs[s] = (cnt + self.alpha) / (total + self.alpha * K)
        sum_p = sum(probs.values())
        return {s: p/sum_p for s, p in probs.items()} if sum_p > 0 else {s: 1.0/K for s in self.states}

class FrequencyPrior:
    def __init__(self, states: List[str]):
        self.states = states
        self.probs = {s: 1.0/len(states) for s in states}

    def train(self, seq: List[str]):
        cnt = Counter(seq)
        total = len(seq)
        if total > 0:
            self.probs = {s: cnt[s]/total for s in self.states}
        sum_p = sum(self.probs.values())
        self.probs = {s: p/sum_p for s, p in self.probs.items()}

    def predict(self) -> Dict[str, float]:
        return self.probs.copy()

class TemperatureScaling:
    def __init__(self, temperature: float = 1.0):
        self.temperature = temperature

    def calibrate(self, probs: Dict[str, float]) -> Dict[str, float]:
        if abs(self.temperature - 1.0) < 1e-6:
            return probs
        scaled = {s: p ** (1/self.temperature) for s, p in probs.items()}
        total = sum(scaled.values())
        return {s: p/total for s, p in scaled.items()}

# ========== 集成引擎 V7.3 ==========
class AttributeEngineV7_3:
    def __init__(self, name: str, order: int = 3, alpha: float = 1.0,
                 use_hmm: bool = True, hmm_states: int = 3, temperature: float = 1.0):
        self.name = name
        self.states = ATTRIBUTE_STATES[name]
        self.use_hmm = use_hmm
        self.temp_scaler = TemperatureScaling(temperature)

        self.markov = MarkovN(order, self.states, alpha)
        self.streak = StreakBias(self.states, alpha)
        self.freq = FrequencyPrior(self.states)

        self.hmm = StableHMM(hmm_states, len(self.states), self.states, reg_factor=0.12) if use_hmm else None

        self.tail_model = FeatureConditionalModel(self.states)
        self.mod7_model = FeatureConditionalModel(self.states)
        self.cross_dist_model = FeatureConditionalModel(self.states)

        self.model_names = ["markov", "streak", "freq"]
        if use_hmm:
            self.model_names.append("hmm")
        self.bayes_weight = OnlineBayesianWeight(self.model_names)

    def train(self, seq: List[str], draws: List[Dict]):
        self.markov.train(seq)
        self.streak.update(seq)
        self.freq.train(seq)
        if self.hmm and len(seq) > 30:
            self.hmm.train(seq)

        tails = [get_tail(d["num"]) for d in draws]
        mod7s = [get_mod7(d["num"]) for d in draws]
        cross_dists = [get_cross_distance(draws[i-1]["num"], draws[i]["num"]) if i > 0 else 0 for i in range(len(draws))]

        self.tail_model.train(seq, tails)
        self.mod7_model.train(seq, mod7s)
        self.cross_dist_model.train(seq, cross_dists)

    def predict_proba(self, recent_seq: List[str], recent_draws: List[Dict]) -> Dict[str, float]:
        # Markov
        context = tuple(recent_seq[-self.markov.order:]) if len(recent_seq) >= self.markov.order else tuple()
        markov_probs = self.markov.predict(context) if context else {s: 1.0/len(self.states) for s in self.states}

        # Streak
        streak_probs = {s: 1.0/len(self.states) for s in self.states}
        if recent_seq:
            last = recent_seq[-1]
            streak_len = 1
            for i in range(len(recent_seq)-2, -1, -1):
                if recent_seq[i] == last:
                    streak_len += 1
                else:
                    break
            streak_probs = self.streak.predict(last, streak_len)

        freq_probs = self.freq.predict()
        hmm_probs = self.hmm.predict_next_probs(recent_seq) if self.hmm and len(recent_seq) >= 2 else {s: 1.0/len(self.states) for s in self.states}

        # Feature probs
        feature_probs = {s: 1.0/len(self.states) for s in self.states}
        if recent_draws:
            prev = recent_draws[-2] if len(recent_draws) >= 2 else None
            curr = recent_draws[-1]
            p1 = self.tail_model.predict(get_tail(curr["num"]))
            p2 = self.mod7_model.predict(get_mod7(curr["num"]))
            p3 = self.cross_dist_model.predict(get_cross_distance(prev["num"], curr["num"]) if prev else 0)
            feature_probs = {s: (p1.get(s,0) + p2.get(s,0) + p3.get(s,0))/3 for s in self.states}
            total = sum(feature_probs.values()) or 1.0
            feature_probs = {s: p/total for s, p in feature_probs.items()}

        # Ensemble
        weights = self.bayes_weight.get_all_weights()
        fused = {s: 0.0 for s in self.states}
        for s in self.states:
            for m in self.model_names:
                p = (markov_probs if m == "markov" else
                     streak_probs if m == "streak" else
                     freq_probs if m == "freq" else hmm_probs).get(s, 0)
                fused[s] += weights.get(m, 0) * p

        # Blend with features
        for s in self.states:
            fused[s] = 0.75 * fused[s] + 0.25 * feature_probs.get(s, 1/3)

        total = sum(fused.values()) or 1.0
        fused = {s: p/total for s, p in fused.items()}

        return self.temp_scaler.calibrate(fused)

    def update_feedback(self, predicted_probs: Dict[str, float], actual: str):
        prob_actual = predicted_probs.get(actual, 1e-10)
        for m in self.model_names:
            self.bayes_weight.update(m, prob_actual)

# ========== 预测系统 ==========
class PredictionSystemV7_3:
    def __init__(self, order: int = 3, min_ig: float = 0.01, temperature: float = 1.0, use_hmm: bool = True):
        self.order = order
        self.min_ig = min_ig
        self.temperature = temperature
        self.use_hmm = use_hmm
        self.engines = {
            "color": AttributeEngineV7_3("color", order, use_hmm=use_hmm, temperature=temperature),
            "size": AttributeEngineV7_3("size", order, use_hmm=use_hmm, temperature=temperature),
            "odd_even": AttributeEngineV7_3("odd_even", order, use_hmm=use_hmm, temperature=temperature)
        }

    def train_all(self, seqs: Dict[str, List[str]], draws: Dict[str, List[Dict]]):
        for name, seq in seqs.items():
            self.engines[name].train(seq, draws[name])

    def predict_all(self, recents: Dict[str, List[str]], draws: Dict[str, List[Dict]]) -> Dict[str, Any]:
        results = {}
        for name, engine in self.engines.items():
            probs = engine.predict_proba(recents[name], draws[name])
            results[name] = {
                "probs": probs,
                "max_prob": max(probs.values()),
                "best_state": max(probs.items(), key=lambda x: x[1])[0],
                "second_state": sorted(probs.items(), key=lambda x: -x[1])[1][0] if len(probs) >= 2 else None
            }
        avg_max_prob = np.mean([results[name]["max_prob"] for name in self.engines])
        should_act = avg_max_prob >= self.min_ig
        results["meta"] = {"should_act": should_act, "reason": f"avg_max_prob={avg_max_prob:.3f}"}
        return results

    def walk_forward_backtest(self, seqs: Dict[str, List[str]], draws: Dict[str, List[Dict]],
                              test_len: int = 100):
        total = 0
        correct = {name: 0 for name in self.engines}
        logloss_sum = {name: 0.0 for name in self.engines}
        kl_sum = {name: 0.0 for name in self.engines}
        color_second_correct = 0
        actuals_list = []
        preds_list = []
        max_probs_list = {name: [] for name in self.engines}
        outcomes_list = {name: [] for name in self.engines}

        min_len = self.order + 20
        start_idx = max(len(seqs["color"]) - test_len, min_len)

        for idx in range(start_idx, len(seqs["color"]) - 1):
            system = PredictionSystemV7_3(order=self.order, min_ig=self.min_ig,
                                          temperature=self.temperature, use_hmm=self.use_hmm)
            train_seqs = {name: seqs[name][:idx] for name in seqs}
            train_draws = {name: draws[name][:idx] for name in draws}
            system.train_all(train_seqs, train_draws)

            recents = {name: seqs[name][idx-self.order:idx] if idx >= self.order else seqs[name][:idx]
                       for name in self.engines}
            recent_draws = {name: draws[name][idx-self.order:idx] if idx >= self.order else draws[name][:idx]
                            for name in self.engines}

            pred = system.predict_all(recents, recent_draws)
            actuals = {name: seqs[name][idx] for name in self.engines}

            if pred["meta"]["should_act"]:
                for name in self.engines:
                    prob_actual = pred[name]["probs"].get(actuals[name], 1e-15)
                    logloss_sum[name] += -math.log(prob_actual)
                    baseline = system.engines[name].freq.probs
                    kl = sum(p * math.log(p / baseline.get(s, 1e-15) + 1e-15) for s, p in pred[name]["probs"].items() if p > 0)
                    kl_sum[name] += kl
                    if pred[name]["best_state"] == actuals[name]:
                        correct[name] += 1
                    max_probs_list[name].append(pred[name]["max_prob"])
                    outcomes_list[name].append(1 if pred[name]["best_state"] == actuals[name] else 0)

                if pred["color"]["best_state"] == actuals["color"] or pred["color"]["second_state"] == actuals["color"]:
                    color_second_correct += 1
                actuals_list.append(actuals["color"])
                preds_list.append(pred["color"]["best_state"])
                total += 1

        if total == 0:
            return {name:0.0 for name in self.engines}, {name:0.0 for name in self.engines}, 0.0, 1.0, {name:0.0 for name in self.engines}, {name:0.0 for name in self.engines}

        acc = {name: correct[name]/total for name in self.engines}
        avg_logloss = {name: logloss_sum[name]/total for name in self.engines}
        avg_kl = {name: kl_sum[name]/total for name in self.engines}
        ece = {name: 0.0 for name in self.engines}  # 简化版，可后续完善

        color_second_acc = color_second_correct / total
        return acc, avg_logloss, color_second_acc, 0.5, avg_kl, ece   # p_value 简化

# ========== 仪表盘 ==========
def print_dashboard(conn, lottery_name: str, order=3, min_ig=0.01, temperature=1.0,
                    use_hmm=True, backtest_len=100):
    seqs = {
        "color": load_sequence(conn, get_color, limit=500),
        "size": load_sequence(conn, get_big_small, limit=500),
        "odd_even": load_sequence(conn, get_odd_even, limit=500)
    }
    draws = load_full_draws(conn, limit=500)
    draws_dict = {"color": draws, "size": draws, "odd_even": draws}

    if len(seqs["color"]) < order + 20:
        print("历史数据不足，请先同步数据。")
        return

    latest = conn.execute("SELECT * FROM draws ORDER BY draw_date DESC, issue_no DESC LIMIT 1").fetchone()
    if latest:
        nums = " ".join(f"{n:02d}" for n in json.loads(latest["numbers_json"]))
        print(f"最新开奖: {latest['issue_no']} | {nums} + {latest['special_number']:02d}")
        attrs = {
            "色波": get_color(latest["special_number"]),
            "大小": get_big_small(latest["special_number"]),
            "单双": get_odd_even(latest["special_number"]),
        }
        print(f"特码属性: {attrs['单双']} {attrs['大小']} {attrs['色波']}")

    system = PredictionSystemV7_3(order=order, min_ig=min_ig, temperature=temperature, use_hmm=use_hmm)
    system.train_all(seqs, draws_dict)
    recents = {name: seqs[name][-order:] for name in seqs}
    recent_draws = {name: draws[-order:] for name in seqs}
    pred = system.predict_all(recents, recent_draws)

    print(f"\n🔮 下一期属性预测 {lottery_name} (阶数={order}, 温度={temperature}, HMM={use_hmm})")
    for name, data in pred.items():
        if name == "meta": continue
        print(f"\n{name}:")
        for s, p in sorted(data["probs"].items(), key=lambda x: -x[1]):
            marker = " ✓" if s == data["best_state"] else ""
            print(f"   {s}: {p*100:.1f}%{marker}")

    meta = pred["meta"]
    print(f"\n🧠 元决策: {'出手' if meta['should_act'] else '观望'}")
    print(f"   原因: {meta['reason']}")

    print(f"\n📊 无泄漏 Walk-Forward 回测 (最近 {backtest_len} 期):")
    acc, logloss, color_second_acc, p_value, avg_kl, ece = system.walk_forward_backtest(seqs, draws_dict, test_len=backtest_len)
    for name in acc:
        print(f"   {name} 准确率: {acc[name]*100:.1f}%   LogLoss: {logloss[name]:.4f}   KL散度: {avg_kl[name]:.4f}")
    print(f"   波色二中一准确率: {color_second_acc*100:.1f}%")
    print(f"   平均准确率: {np.mean(list(acc.values()))*100:.1f}%")

# ========== 主函数 ==========
def process_lottery(lottery_name: str, args):
    db_path = str(SCRIPT_DIR / DB_FILES[lottery_name])
    print(f"\n{'='*60}\n处理彩种: {lottery_name}\n数据库: {db_path}\n{'='*60}")
    conn = connect_db(db_path)
    try:
        init_db(conn)
        records, source, url = fetch_online_records(lottery_name)
        total, ins, upd = sync_from_records(conn, records, source)
        print(f"同步完成: 总计 {total}, 新增 {ins}, 更新 {upd}, 来源 {source} ({url})")
        print_dashboard(conn, lottery_name, order=args.order, min_ig=args.min_ig,
                        temperature=args.temp, use_hmm=args.use_hmm, backtest_len=args.backtest)
    except Exception as e:
        print(f"处理 {lottery_name} 时出错: {e}")
    finally:
        conn.close()

def main():
    p = argparse.ArgumentParser(description="三彩种属性预测 V7.3 (HMM 稳定版)")
    p.add_argument("--lottery", choices=["老澳门彩", "香港彩", "新澳门彩"], help="指定单个彩种")
    p.add_argument("--order", type=int, default=3)
    p.add_argument("--min-ig", type=float, default=0.01)
    p.add_argument("--temp", type=float, default=1.0)
    p.add_argument("--use-hmm", action="store_true", default=True)
    p.add_argument("--no-hmm", dest="use_hmm", action="store_false")
    p.add_argument("--backtest", type=int, default=100)
    args = p.parse_args()

    if args.lottery:
        process_lottery(args.lottery, args)
    else:
        for lottery in ["老澳门彩", "香港彩", "新澳门彩"]:
            process_lottery(lottery, args)

if __name__ == "__main__":
    main()