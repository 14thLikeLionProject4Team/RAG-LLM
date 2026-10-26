"""
첫단추 시연용 원장 생성기

config.toml 의 규칙과 이야기표를 읽어서
가족별 원장(돈이 움직인 기록), 월별 요약, 백엔드용 시드 파일, 확인용 리포트를 만든다.

사용법:  python3 generate.py            (같은 폴더의 config.toml 사용)
        python3 generate.py 다른설정.toml

필요한 것: Python 3.11 이상 (추가 설치 없음)
"""
from __future__ import annotations

import calendar
import csv
import datetime as dt
import json
import random
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"

POCKET_NOW = "이번 달 용돈"
POCKET_SAVED = "모은 돈"

TYPE_LABEL = {
    "ALLOWANCE": "용돈 입금",
    "MISSION_REWARD": "미션 보상",
    "CARD_PAYMENT": "카드 결제",
    "CARD_CANCEL": "결제 취소",
    "SETTLEMENT": "결제일 정산",
    "SHORTFALL": "부족 발생",
    "SHORTFALL_REPAY": "부족분 송금",
    "AUTO_RECOVERY": "자동 정산",
    "POCKET_MOVE": "남은 돈 옮김",
}


class StoryError(Exception):
    """이야기표가 규칙과 맞지 않을 때"""


# ───────────────────────── 날짜 도우미 ─────────────────────────
def parse_ym(s: str) -> tuple[int, int]:
    y, m = s.split("-")
    return int(y), int(m)


def add_month(ym: tuple[int, int], k: int) -> tuple[int, int]:
    y, m = ym
    idx = y * 12 + (m - 1) + k
    return idx // 12, idx % 12 + 1


def ym_str(ym: tuple[int, int]) -> str:
    return f"{ym[0]:04d}-{ym[1]:02d}"


def months_between(a: str, b: str) -> list[tuple[int, int]]:
    out, cur, end = [], parse_ym(a), parse_ym(b)
    while cur <= end:
        out.append(cur)
        cur = add_month(cur, 1)
    return out


def cycle_period(ym, payday):
    """정산 달 ym: 전달 (payday+1)일 ~ 이번 달 payday일"""
    py, pm = add_month(ym, -1)
    start_day = min(payday + 1, calendar.monthrange(py, pm)[1])
    start = dt.date(py, pm, start_day)
    if payday + 1 > calendar.monthrange(py, pm)[1]:
        start = dt.date(*ym, 1)
    return start, dt.date(ym[0], ym[1], payday)


def cycle_of(d: dt.date, payday: int) -> str:
    return ym_str((d.year, d.month)) if d.day <= payday else ym_str(add_month((d.year, d.month), 1))


def days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def r100(x: float) -> int:
    return int(round(x / 100.0)) * 100


def won(n) -> str:
    return f"{n:,}원" if isinstance(n, int) else str(n)


def age_on(birth: dt.date, d: dt.date) -> int:
    return d.year - birth.year - ((d.month, d.day) < (birth.month, birth.day))


# ───────────────────────── 원장 한 줄 ─────────────────────────
class Ledger:
    def __init__(self, fam_id: str):
        self.fam = fam_id
        self.rows: list[dict] = []

    def add(self, when: dt.datetime, typ: str, amount: int, *, frm="", to="", category="",
            merchant="", memo="", cycle="", for_cycle="", order=0):
        self.rows.append({
            "_when": when, "_order": order,
            "type": typ, "type_label": TYPE_LABEL[typ], "amount": int(amount),
            "from": frm, "to": to, "category": category, "merchant": merchant,
            "memo": memo, "cycle": cycle, "for_cycle": for_cycle, "verified": "L2",
        })

    def finalize(self):
        self.rows.sort(key=lambda r: (r["_when"], r["_order"]))
        for i, r in enumerate(self.rows, 1):
            r["id"] = f"{self.fam}-{i:05d}"
            r["date"] = r["_when"].date().isoformat()
            r["time"] = r["_when"].strftime("%H:%M")
        return self.rows


def at(d: dt.date, hhmm: str, sec: int = 0) -> dt.datetime:
    h, m = map(int, hhmm.split(":"))
    return dt.datetime(d.year, d.month, d.day, h, m, sec)


def rand_time(rng: random.Random, d: dt.date, latest: str = "22:30") -> dt.datetime:
    lh, lm = map(int, latest.split(":"))
    lo, hi = 7 * 60, lh * 60 + lm
    t = rng.randint(lo, max(lo, hi))
    return dt.datetime(d.year, d.month, d.day, t // 60, t % 60)


# ───────────────────────── 카드 결제 만들기 ─────────────────────────
def make_card_payments(rng, spend_cfg, start, end, target, events, payday_date, ctx):
    """start~end 사이 매일 결제를 만들고, 합계가 target 이 되도록 맞춘다."""
    pays = []
    for d in days(start, end):
        vacation = d.month in (1, 2, 7, 8)
        for s in spend_cfg:
            if s.get("weekday_only") and d.weekday() >= 5:
                continue
            p = s["per_week"] / (5 if s.get("weekday_only") else 7)
            if vacation:
                p *= s.get("vacation_factor", 1.0)
            if rng.random() < p:
                amt = s["min"] if s.get("fixed") else r100(rng.uniform(s["min"], s["max"]))
                latest = "17:30" if d == payday_date else "22:30"
                pays.append({"when": rand_time(rng, d, latest), "amount": amt, "spec": s,
                             "category": s["category"], "merchant": rng.choice(s["merchants"]),
                             "memo": "", "event": False})
    for e in events:
        ed = e["date"]
        latest = "17:30" if ed == payday_date else "21:00"
        pays.append({"when": rand_time(rng, ed, latest), "amount": e["amount"], "spec": None,
                     "category": e["category"], "merchant": e["merchant"],
                     "memo": e.get("memo", ""), "event": True})

    fixed_sum = lambda: sum(p["amount"] for p in pays if p["spec"] and p["spec"].get("fixed"))
    event_sum = sum(p["amount"] for p in pays if p["event"])
    flex = lambda: [p for p in pays if p["spec"] and not p["spec"].get("fixed")]

    # 교통 같은 고정 금액이 목표를 넘으면 몇 건 뺀다
    while target - fixed_sum() - event_sum < 0:
        fixed = [p for p in pays if p["spec"] and p["spec"].get("fixed")]
        if not fixed:
            raise StoryError(f"{ctx}: 큰 지출(events) 합계 {won(event_sum)}가 목표 청구액 {won(target)}보다 커요")
        pays.remove(rng.choice(fixed))

    remaining = target - fixed_sum() - event_sum
    flex_specs = [s for s in spend_cfg if not s.get("fixed")]

    def new_flex(amount=None):
        s = rng.choice(flex_specs)
        d = start + dt.timedelta(days=rng.randint(0, (end - start).days))
        latest = "17:30" if d == payday_date else "22:30"
        a = amount if amount is not None else r100(rng.uniform(s["min"], s["max"]))
        p = {"when": rand_time(rng, d, latest), "amount": a, "spec": s, "category": s["category"],
             "merchant": rng.choice(s["merchants"]), "memo": "", "event": False}
        pays.append(p)
        return p

    if remaining > 0 and not flex():
        new_flex()
    # 금액을 억지로 줄이거나 늘리지 않게, 먼저 결제 건수로 목표에 가깝게 맞춘다
    while sum(p["amount"] for p in flex()) > remaining * 1.15 and len(flex()) > 1:
        pays.remove(rng.choice(flex()))
    while sum(p["amount"] for p in flex()) < remaining * 0.85:
        new_flex()
    fl = flex()
    total = sum(p["amount"] for p in fl)
    if total > 0:
        f = remaining / total
        for p in fl:
            p["amount"] = r100(p["amount"] * f)

    lo = lambda s: max(500, r100(s["min"] * 0.5))
    hi = lambda s: r100(s["max"] * 1.8)
    for p in flex():
        p["amount"] = min(max(p["amount"], lo(p["spec"])), hi(p["spec"]))

    guard = 0
    while True:
        guard += 1
        if guard > 5000:
            raise StoryError(f"{ctx}: 결제 금액을 목표에 맞추지 못했어요")
        diff = remaining - sum(p["amount"] for p in flex())
        if diff == 0:
            break
        if diff > 0:
            room = [p for p in flex() if p["amount"] < hi(p["spec"])]
            if not room:
                s = rng.choice(flex_specs)
                new_flex(min(diff, hi(s)) if diff >= lo(s) else None)
                continue
            p = rng.choice(room)
            p["amount"] += min(diff, hi(p["spec"]) - p["amount"], 1000)
        else:
            room = [p for p in flex() if p["amount"] > lo(p["spec"])]
            if not room:
                if not flex():
                    raise StoryError(f"{ctx}: 목표 청구액이 너무 작아요")
                pays.remove(min(flex(), key=lambda p: p["amount"]))
                continue
            p = rng.choice(room)
            p["amount"] -= min(-diff, p["amount"] - lo(p["spec"]), 1000)

    assert sum(p["amount"] for p in pays) == target, ctx
    return pays


# ───────────────────────── 한 가족 만들기 ─────────────────────────
def build_family(cfg, fam):
    rules, demo = cfg["rules"], cfg["demo"]
    payday, aday = rules["payday"], rules["allowance_day"]
    settle_time = rules.get("settle_time", "18:00")
    today: dt.date = demo["today"]
    rng = random.Random(f'{demo.get("seed", 42)}-{fam["id"]}')
    spend_cfg = cfg["spend"]
    fid = fam["id"]
    led = Ledger(fid)

    months = {m["cycle"]: m for m in fam.get("month", [])}
    allowances = sorted(fam["allowance"], key=lambda a: a["from"])
    missions = sorted(fam.get("missions_paid", []), key=lambda m: m["date"])

    def allowance_for(c):
        amt = None
        for a in allowances:
            if a["from"] <= c:
                amt = a["amount"]
        if amt is None:
            raise StoryError(f"가족 {fid}: {c} 달의 용돈이 정해지지 않았어요")
        return amt

    def ratio_for(c):
        rng_range = fam.get("spend_ratio", [0.86, 0.95])
        for p in fam.get("spend_ratio_periods", []):
            if p["from"] <= c <= p["to"]:
                rng_range = p["range"]
        return rng.uniform(*rng_range)

    savings = 0
    prev_short = None
    monthly = []

    for ym in months_between(fam["first_cycle"], fam["last_cycle"]):
        c = ym_str(ym)
        start, end = cycle_period(ym, payday)
        if start > today:
            break
        current = today <= end
        m = months.get(c, {})
        ctx = f"가족 {fid} {c}"

        allow_date = dt.date(ym[0], ym[1], aday)
        A = allowance_for(c) if allow_date <= today else 0
        R = 0
        repay = None

        # 지난달 부족분 처리
        if prev_short:
            if prev_short["resolve"] == "스스로":
                rd = prev_short["settle_date"] + dt.timedelta(days=prev_short["days"])
                if rd >= allow_date:
                    raise StoryError(f"{prev_short['cycle']}: 스스로 갚은 날({rd})이 다음 용돈일({allow_date})보다 늦어요")
                repay = {"date": rd, **prev_short}
            elif allow_date <= today:
                R = prev_short["amount"]
                if R > A:
                    raise StoryError(f"{ctx}: 자동 정산할 부족분 {won(R)}이 용돈 {won(A)}보다 커요")

        # 이 기간에 들어온 미션 보상
        settle_dt = at(end, settle_time)
        mis = [x for x in missions if start <= x["date"] <= min(end, today)]
        Mi = sum(x["amount"] for x in mis)
        cap = r100(allowance_for(c) * rules.get("mission_cap_ratio", 0.3))
        if Mi > cap:
            raise StoryError(f"{ctx}: 미션 보상 {won(Mi)}이 한 달 상한 {won(cap)}을 넘어요")

        repay_from_now = 0
        if repay:
            if repay["source"] == POCKET_NOW:
                before = sum(x["amount"] for x in mis if x["date"] < repay["date"])
                if before < repay["amount"]:
                    raise StoryError(f"{repay['cycle']}: 갚는 날까지 '이번 달 용돈'에 {won(before)}뿐이라 {won(repay['amount'])}을 갚을 수 없어요 (미션 보상이 먼저 들어와야 해요)")
                repay_from_now = repay["amount"]
            else:
                if savings < repay["amount"]:
                    raise StoryError(f"{repay['cycle']}: 모은 돈이 {won(savings)}뿐이라 {won(repay['amount'])}을 갚을 수 없어요")
                savings -= repay["amount"]

        B = A - R + Mi - repay_from_now   # 정산 직전 '이번 달 용돈'

        # 목표 청구액
        if current:
            T = m.get("bill_until_today")
            if T is None:
                T = r100(B * 0.6)
        elif "bill" in m:
            T = m["bill"]
        elif m.get("result") == "부족":
            T = B + m["shortfall"]
        else:
            T = r100(B * ratio_for(c))
            T = min(T, B)

        # 원장에 돈 이벤트 기록
        if A:
            led.add(at(allow_date, "09:00"), "ALLOWANCE", A, frm=f"부모 계좌", to=POCKET_NOW,
                    memo=f"{allow_date.month}월 용돈", cycle=c)
        if R:
            led.add(at(allow_date, "09:00", 30), "AUTO_RECOVERY", R, frm=POCKET_NOW, to="부모 계좌",
                    memo=f"{prev_short['cycle']} 부족분, 다음 용돈에서 자동 정산", cycle=c,
                    for_cycle=prev_short["cycle"], order=1)
        for x in mis:
            led.add(at(x["date"], "19:00"), "MISSION_REWARD", x["amount"], frm="부모 계좌", to=POCKET_NOW,
                    memo=x["name"], cycle=c)
        if repay:
            led.add(at(repay["date"], "20:00"), "SHORTFALL_REPAY", repay["amount"],
                    frm=repay["source"], to="부모 계좌",
                    memo=f"{repay['cycle']} 부족분, {repay['days']}일 만에 직접 보냄", cycle=c,
                    for_cycle=repay["cycle"])

        # 카드 결제
        events = m.get("events", [])
        for e in events:
            if not (start <= e["date"] <= min(end, today)):
                raise StoryError(f"{ctx}: 큰 지출 날짜 {e['date']}가 정산 기간({start}~{end}) 밖이에요")
        card_end = (today - dt.timedelta(days=1)) if current else end
        card_end = max(card_end, start)
        pays = make_card_payments(rng, spend_cfg, start, card_end, T, events, end, ctx)
        for p in pays:
            led.add(p["when"], "CARD_PAYMENT", p["amount"], frm="가족카드", to=p["merchant"],
                    category=p["category"], merchant=p["merchant"], memo=p["memo"], cycle=c)
        # 결제 취소 한 쌍 (합계에는 영향 없음)
        if rng.random() < 0.35 and (card_end - start).days >= 2:
            d = start + dt.timedelta(days=rng.randint(0, (card_end - start).days - 1))
            a = r100(rng.uniform(3000, 5000))
            w = rand_time(rng, d, "20:00")
            led.add(w, "CARD_PAYMENT", a, frm="가족카드", to="카페", category="카페", merchant="카페", cycle=c)
            led.add(w + dt.timedelta(days=1), "CARD_CANCEL", a, frm="카페", to="가족카드",
                    category="카페", merchant="카페", memo="결제 취소", cycle=c)

        row = {"cycle": c, "period": f"{start.isoformat()} ~ {end.isoformat()}", "allowance": A,
               "auto_recovery": R, "missions": Mi, "repay_from_now": repay_from_now,
               "balance_before_settle": B, "bill": T}

        if current:
            row.update({"status": "진행 중", "settled": None, "shortfall": None, "moved_to_saved": None,
                        "keep_ratio": None, "saved_after": savings, "until": today.isoformat()})
            monthly.append(row)
            break

        settled = min(B, T)
        short = T - settled
        if m.get("result") == "완납" and short:
            raise StoryError(f"{ctx}: '완납'인데 청구액 {won(T)}이 이번 달 용돈 {won(B)}보다 커요")
        if m.get("result") == "부족" and short != m["shortfall"] and "bill" not in m:
            raise StoryError(f"{ctx}: 부족 금액이 {won(short)}로 계산됐어요")

        led.add(settle_dt, "SETTLEMENT", settled, frm=POCKET_NOW, to="부모 계좌",
                memo=f"청구액 {won(T)}", cycle=c, order=0)
        moved = 0
        if short:
            led.add(settle_dt + dt.timedelta(seconds=1), "SHORTFALL", short,
                    memo="다음 용돈 전까지 직접 보내면 해소, 못 보내면 다음 용돈에서 자동 정산",
                    cycle=c, order=1)
            prev_short = {"cycle": c, "amount": short, "resolve": m.get("resolve", "자동"),
                          "days": m.get("resolve_days", 0), "source": m.get("resolve_from", POCKET_SAVED),
                          "settle_date": end}
            status = "부족"
        else:
            moved = B - settled
            if moved:
                led.add(settle_dt + dt.timedelta(minutes=1), "POCKET_MOVE", moved, frm=POCKET_NOW,
                        to=POCKET_SAVED, memo="결제일 정산 뒤 남은 돈", cycle=c, order=2)
                savings += moved
            prev_short = None
            status = "완납"

        income = A + Mi
        row.update({"status": status, "settled": settled, "shortfall": short, "moved_to_saved": moved,
                    "keep_ratio": round((B - T) / income, 4) if income else None, "saved_after": savings})
        monthly.append(row)

    # 마지막 달이 부족으로 끝나고 다음 달이 없으면, 처리 결과를 남겨 둠
    rows = led.finalize()
    resolve_info = annotate_resolution(rows, monthly)
    snapshot = simulate(rows, monthly, today, payday, fid)
    return rows, monthly, snapshot, resolve_info


def annotate_resolution(rows, monthly):
    by_cycle = {}
    for r in rows:
        if r["type"] in ("SHORTFALL_REPAY", "AUTO_RECOVERY"):
            by_cycle[r["for_cycle"]] = r
    out = []
    for mrow in monthly:
        if mrow["status"] == "부족":
            r = by_cycle.get(mrow["cycle"])
            if r is None:
                mrow["resolution"] = "아직 처리 전"
            elif r["type"] == "SHORTFALL_REPAY":
                d = (dt.date.fromisoformat(r["date"]) - dt.date.fromisoformat(mrow["period"][-10:])).days
                mrow["resolution"] = f"스스로 {d}일 ({r['from']})"
                mrow["resolve_days"] = d
                out.append(("스스로", d))
            else:
                mrow["resolution"] = "다음 용돈에서 자동 정산"
                out.append(("자동", None))
        elif mrow["status"] == "완납":
            mrow["resolution"] = "완납"
        else:
            mrow["resolution"] = "진행 중"
    return out


def simulate(rows, monthly, today, payday, fid):
    """원장을 처음부터 다시 읽어서 규칙대로 맞는지 확인하고, 오늘 기준 숫자를 계산한다."""
    now, saved = 0, 0
    bills: dict[str, int] = {}
    settled: dict[str, int] = {}
    for r in rows:
        a, t = r["amount"], r["type"]
        if t in ("ALLOWANCE", "MISSION_REWARD"):
            now += a
        elif t == "AUTO_RECOVERY":
            now -= a
        elif t == "CARD_PAYMENT":
            bills[r["cycle"]] = bills.get(r["cycle"], 0) + a
        elif t == "CARD_CANCEL":
            bills[r["cycle"]] -= a
        elif t == "SETTLEMENT":
            expect = min(now, bills.get(r["cycle"], 0))
            assert a == expect, f"{fid} {r['cycle']} 정산액 불일치 {a} != {expect}"
            settled[r["cycle"]] = a
            now -= a
        elif t == "SHORTFALL":
            assert a == bills[r["cycle"]] - settled[r["cycle"]], f"{fid} {r['cycle']} 부족 금액 불일치"
        elif t == "POCKET_MOVE":
            assert a == now, f"{fid} {r['cycle']} 옮길 금액 불일치"
            now -= a
            saved += a
        elif t == "SHORTFALL_REPAY":
            if r["from"] == POCKET_SAVED:
                saved -= a
            else:
                now -= a
        assert now >= 0 and saved >= 0, f"{fid} {r['date']} 잔액이 마이너스가 됐어요"
    for mrow in monthly:
        assert bills.get(mrow["cycle"], 0) == mrow["bill"], f"{fid} {mrow['cycle']} 청구액 불일치"

    open_cycle = cycle_of(today, payday)
    bill_open = bills.get(open_cycle, 0) if monthly and monthly[-1]["status"] == "진행 중" else 0
    pay_date = dt.date(int(open_cycle[:4]), int(open_cycle[5:]), payday)
    return {
        "date": today.isoformat(),
        "open_cycle": open_cycle if bill_open else None,
        "pocket_now": now,
        "pocket_saved": saved,
        "bill_until_today": bill_open,
        "payday": pay_date.isoformat() if bill_open else None,
        "days_to_payday": (pay_date - today).days if bill_open else None,
        "simple_gap": (now - bill_open) if bill_open else None,
    }


# ───────────────────────── 결과 정리 ─────────────────────────
def streaks(monthly):
    settled = [m for m in monthly if m["status"] in ("완납", "부족")]
    longest = cur = 0
    for m in settled:
        cur = cur + 1 if m["status"] == "완납" else 0
        longest = max(longest, cur)
    return {
        "settled_months": len(settled),
        "paid_months": sum(m["status"] == "완납" for m in settled),
        "current_streak": cur,
        "longest_streak": longest,
        "recent12_paid": sum(m["status"] == "완납" for m in settled[-12:]),
    }


def check_expect(fam, stats, snapshot, resolve_info):
    exp = fam.get("expect", {})
    got = dict(stats)
    got["bill_until_today"] = snapshot["bill_until_today"]
    got["self_resolved"] = sum(1 for k, _ in resolve_info if k == "스스로")
    got["resolve_days"] = [d for k, d in resolve_info if k == "스스로"]
    labels = {
        "paid_months": "완납한 달", "settled_months": "정산한 달", "current_streak": "지금 연속 완납",
        "longest_streak": "최장 연속 완납", "recent12_paid": "최근 12개월 완납",
        "bill_until_today": "기준일까지 청구 예정액", "self_resolved": "스스로 해소한 부족분",
        "resolve_days": "해소 기간(일)",
    }
    out = []
    for k, v in exp.items():
        out.append((labels.get(k, k), v, got.get(k), got.get(k) == v))
    return out


def category_totals(rows, cycle):
    tot = {}
    for r in rows:
        if r["cycle"] != cycle:
            continue
        if r["type"] == "CARD_PAYMENT":
            tot[r["category"]] = tot.get(r["category"], 0) + r["amount"]
        elif r["type"] == "CARD_CANCEL":
            tot[r["category"]] = tot.get(r["category"], 0) - r["amount"]
    return dict(sorted(tot.items(), key=lambda kv: -kv[1]))


def write_family(cfg, fam, rows, monthly, snapshot, resolve_info):
    fid = fam["id"].lower()
    d = OUT / f"family_{fid}"
    d.mkdir(parents=True, exist_ok=True)
    today = cfg["demo"]["today"]
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    # 원장 JSON (백엔드용, 영문 키)
    (d / "ledger.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")

    # 원장 CSV (사람용, 엑셀에서 바로 열림)
    with open(d / "원장.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["번호", "날짜", "시각", "종류", "금액", "어디서", "어디로", "항목", "가게", "메모", "정산 달", "관련 달"])
        for r in clean:
            w.writerow([r["id"], r["date"], r["time"], r["type_label"], r["amount"], r["from"], r["to"],
                        r["category"], r["merchant"], r["memo"], r["cycle"], r["for_cycle"]])

    # 월별 요약 CSV (정답지)
    with open(d / "월별요약.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["정산 달", "기간", "용돈", "자동 정산(차감)", "미션 보상", "정산 직전 이번 달 용돈",
                    "청구액", "정산액", "부족", "결과", "모은 돈으로", "남긴 비율", "모은 돈 잔액"])
        for m in monthly:
            w.writerow([m["cycle"], m["period"], m["allowance"], m["auto_recovery"], m["missions"],
                        m["balance_before_settle"], m["bill"], m["settled"], m["shortfall"], m["resolution"],
                        m["moved_to_saved"],
                        "" if m["keep_ratio"] is None else f"{m['keep_ratio']*100:.1f}%", m["saved_after"]])

    child_id, parent_id = f"child_{fid}", f"parent_{fid}"
    stats = streaks(monthly)
    seed = {
        "family": fam["id"],
        "demo_today": today.isoformat(),
        "child": {"id": child_id, "name": fam["child_name"], "role": "자녀",
                  "birth": fam["child_birth"].isoformat(), "age_today": age_on(fam["child_birth"], today)},
        "parent": {"id": parent_id, "name": fam["parent_name"], "role": "부모"},
        "accounts": {"bank": cfg["demo"].get("bank_name", "첫단추은행"),
                     "child_account": fam["child_account"], "parent_account": fam["parent_account"],
                     "pockets": [POCKET_NOW, POCKET_SAVED]},
        "card": {"type": "가족 신용카드", "limit": fam.get("card_limit", 100000)},
        "rules": cfg["rules"],
        "allowance_history": [{k: v for k, v in a.items()} for a in fam["allowance"]],
        "missions": {
            "paid": [{**m, "date": m["date"].isoformat()} for m in fam.get("missions_paid", [])],
            "now": [{k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in m.items()}
                    for m in fam.get("missions_now", [])],
        },
        "ledger": clean,
        "reference": {  # 백엔드가 직접 계산한 값과 맞춰보는 정답지
            "monthly": monthly,
            "stats": stats,
            "today": snapshot,
        },
    }
    (d / "seed.json").write_text(json.dumps(seed, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return stats


def write_report(cfg, results):
    today = cfg["demo"]["today"]
    L = [f"# 원장 생성 결과", "", f"데모 기준일 **{today.isoformat()}** · seed {cfg['demo'].get('seed', 42)}", ""]
    for fam, rows, monthly, snapshot, resolve_info, stats in results:
        L += [f"## 가족 {fam['id']} — {fam['child_name']} (부모 {fam['parent_name']})", ""]
        checks = check_expect(fam, stats, snapshot, resolve_info)
        if checks:
            L += ["### 이야기대로 나왔나", "", "| 확인 | 기대 | 결과 | |", "|---|---|---|---|"]
            for label, want, got, ok in checks:
                L.append(f"| {label} | {want} | {got} | {'✅' if ok else '❌'} |")
            L.append("")
        L += [f"원장 {len(rows)}줄 · 카드 결제 {sum(r['type']=='CARD_PAYMENT' for r in rows)}건", ""]
        L += ["### 월별 요약", "",
              "| 정산 달 | 용돈 | 미션 | 차감 | 정산 직전 | 청구액 | 결과 | 모은 돈으로 | 남긴 비율 | 모은 돈 |",
              "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|"]
        for m in monthly:
            kr = "" if m["keep_ratio"] is None else f"{m['keep_ratio']*100:.0f}%"
            moved = "" if m["moved_to_saved"] is None else f"{m['moved_to_saved']:,}"
            minus = m["auto_recovery"] + m["repay_from_now"]
            L.append(f"| {m['cycle']} | {m['allowance']:,} | {m['missions']:,} | {minus:,} | "
                     f"{m['balance_before_settle']:,} | {m['bill']:,} | {m['resolution']} | "
                     f"{moved} | {kr} | {m['saved_after']:,} |")
        L.append("")
        s = snapshot
        L += ["### 기준일 숫자", "",
              f"- 이번 달 용돈 주머니: **{won(s['pocket_now'])}**",
              f"- 모은 돈 주머니: **{won(s['pocket_saved'])}**"]
        if s["bill_until_today"]:
            L += [f"- 청구 예정액({s['open_cycle']}): **{won(s['bill_until_today'])}** · 결제일 {s['payday']} (D-{s['days_to_payday']})",
                  f"- 단순 차이(이번 달 용돈 − 청구 예정): **{won(s['simple_gap'])}**",
                  "", "이번 정산 기간 항목별:", ""]
            for k, v in category_totals(rows, s["open_cycle"]).items():
                L.append(f"- {k} {won(v)}")
        L.append("")
    (OUT / "report.md").write_text("\n".join(L), encoding="utf-8")


def main():
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "config.toml"
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    OUT.mkdir(exist_ok=True)

    results, personas = [], []
    try:
        for fam in cfg["family"]:
            rows, monthly, snapshot, resolve_info = build_family(cfg, fam)
            stats = write_family(cfg, fam, rows, monthly, snapshot, resolve_info)
            results.append((fam, rows, monthly, snapshot, resolve_info, stats))
            fid = fam["id"].lower()
            personas += [
                {"id": f"child_{fid}", "family": fam["id"], "name": fam["child_name"],
                 "role": "자녀" if age_on(fam["child_birth"], cfg["demo"]["today"]) < 19 else "본인"},
                {"id": f"parent_{fid}", "family": fam["id"], "name": fam["parent_name"], "role": "부모",
                 "child_id": f"child_{fid}"},
            ]
    except StoryError as e:
        print(f"\n⚠️  이야기표를 고쳐야 해요: {e}\n")
        sys.exit(1)

    (OUT / "personas.json").write_text(json.dumps(personas, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report(cfg, results)

    print("\n원장을 만들었어요.\n")
    for fam, rows, monthly, snapshot, resolve_info, stats in results:
        checks = check_expect(fam, stats, snapshot, resolve_info)
        bad = [c for c in checks if not c[3]]
        mark = "✅ 이야기대로" if not bad else f"❌ {len(bad)}개 다름"
        print(f"  가족 {fam['id']} {fam['child_name']}: 원장 {len(rows)}줄, 정산 {stats['settled_months']}번, "
              f"완납 {stats['paid_months']}번, 지금 연속 {stats['current_streak']}개월  {mark}")
        for label, want, got, ok in bad:
            print(f"      - {label}: 기대 {want} / 결과 {got}")
    print(f"\n  자세한 결과: {OUT / 'report.md'}\n")


if __name__ == "__main__":
    main()
