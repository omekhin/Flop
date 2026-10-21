#!/usr/bin/env python3
"""
Домашний дашборд — сервер.

  python3 server.py                 запустить
  python3 server.py --find Селигдар найти бумагу на Мосбирже (ISIN/SECID для config.json)
  DASH_MOCK=1 python3 server.py     демо-режим без интернета
"""
import argparse, asyncio, json, logging, os, ssl, sys, threading, time
import datetime as dt
import urllib.error, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
CFG_PATH = BASE / "config.json"
CFG = json.loads(CFG_PATH.read_text("utf-8"))
CFG_MTIME = CFG_PATH.stat().st_mtime
TZ = ZoneInfo(CFG.get("timezone", "Europe/Moscow"))
MOCK = os.environ.get("DASH_MOCK") == "1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("dash")

STATE = {"portfolio": None, "payments": None, "weather": None, "calendar": None, "indoor": None, "errors": {}}
LOCK = threading.Lock()
FILE_LOCK = threading.Lock()

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()


def now():
    return dt.datetime.now(TZ)


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "home-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read()


def http_get(url, timeout=20):
    try:
        return _get(url, timeout)
    except urllib.error.URLError as e:
        # Котировки Мосбиржи — открытые данные, их можно взять и по http, если не сошёлся сертификат.
        if isinstance(e.reason, ssl.SSLError) and url.startswith("https://iss.moex.com/"):
            return _get("http://" + url[len("https://"):], timeout)
        raise


def http_json(url, timeout=20):
    return json.loads(http_get(url, timeout).decode("utf-8"))


def load_json(name, default):
    p = DATA / name
    with FILE_LOCK:
        if p.exists():
            try:
                return json.loads(p.read_text("utf-8"))
            except Exception:
                log.exception("битый файл %s", p)
        return default


def save_json(name, obj):
    p = DATA / name
    with FILE_LOCK:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(p)


def set_state(key, value=None, error=None):
    with LOCK:
        if error is None:
            STATE[key] = value
            STATE["errors"].pop(key, None)
        else:
            STATE["errors"][key] = error


# ─────────────────────────── Мосбиржа ───────────────────────────

ISS = "https://iss.moex.com/iss"
BOARDS = {
    "shares": ["TQBR", "TQTF", "TQPI", "TQTD", "TQTE"],
    "bonds": ["TQOB", "TQCB", "TQIR", "TQOD", "TQOE", "TQRD"],
}
MARKET = {"share": "shares", "fund": "shares", "bond": "bonds"}
GROUPS = {"share": {"stock_shares", "stock_dr"}, "fund": {"stock_ppif", "stock_etf"}, "bond": {"stock_bonds"}}
TOLERANCE = {"share": 0.45, "fund": 0.25, "bond": 0.12}


def iss_rows(block):
    cols = block["columns"]
    return [dict(zip(cols, r)) for r in block["data"]]


def fetch_quotes(market, secids):
    """{SECID: {price, prev, face, aci, board, shortname}} — лучший режим торгов для каждой бумаги."""
    out = {}
    secids = [s for s in dict.fromkeys(secids) if s]
    for i in range(0, len(secids), 10):
        chunk = secids[i:i + 10]
        q = urllib.parse.urlencode({"securities": ",".join(chunk), "iss.meta": "off",
                                    "iss.only": "securities,marketdata"})
        d = http_json(f"{ISS}/engines/stock/markets/{market}/securities.json?{q}")
        md = {(r["SECID"], r["BOARDID"]): r for r in iss_rows(d["marketdata"])}
        boards = BOARDS[market]
        for s in iss_rows(d["securities"]):
            m = md.get((s["SECID"], s["BOARDID"]), {})
            prev = s.get("PREVPRICE") or s.get("PREVLEGALCLOSEPRICE") or s.get("PREVWAPRICE")
            price = m.get("LAST") or m.get("MARKETPRICE") or m.get("LCURRENTPRICE") or prev
            if not price:
                continue
            rank = boards.index(s["BOARDID"]) if s["BOARDID"] in boards else 99
            cur = out.get(s["SECID"])
            if cur and cur["rank"] <= rank:
                continue
            out[s["SECID"]] = {
                "rank": rank, "price": float(price), "prev": float(prev) if prev else None,
                "face": s.get("FACEVALUE"), "aci": s.get("ACCRUEDINT") or 0.0,
                "faceunit": s.get("FACEUNIT") or s.get("CURRENCYID"),
                "board": s["BOARDID"], "shortname": s.get("SHORTNAME"),
            }
    return out


def position_value(kind, qty, q, price=None):
    """Возвращает (стоимость, стоимость без НКД). price — подставить другую цену (напр. вчерашнюю)."""
    p = q["price"] if price is None else price
    if kind == "bond":
        face = float(q.get("face") or 1000)
        clean = p / 100 * face * qty
        return clean + float(q.get("aci") or 0) * qty, clean
    v = p * qty
    return v, v


def search_moex(query, kind=None, limit=30):
    q = urllib.parse.urlencode({"q": query, "iss.meta": "off", "is_trading": 1, "limit": limit})
    rows = iss_rows(http_json(f"{ISS}/securities.json?{q}")["securities"])
    if kind:
        rows = [r for r in rows if r.get("group") in GROUPS[kind]]
    return rows


def resolve_positions(positions):
    """Сопоставляет позиции с SECID. Подсказка из config проверяется по сумме со скриншота,
    если не подошла — ищем по названию и берём бумагу, только если подходящая ровно одна."""
    cache = load_json("resolved.json", {})
    changed = False
    for pos in positions:
        key = f'{pos["name"]}|{pos.get("secid", "")}|{pos.get("query", "")}'
        if key in cache:
            pos["_secid"], pos["_how"] = cache[key], "сохранено"
            continue
        kind, ref = pos["kind"], float(pos.get("ref_value") or 0)
        cands = [pos["secid"]] if pos.get("secid") else []
        try:
            q = fetch_quotes(MARKET[kind], cands) if cands else {}
        except Exception as e:
            log.warning("котировки %s: %s", pos["name"], e)
            pos.pop("_tried", None)  # сетевая ошибка — попробуем в следующий цикл
            continue

        def ok(secid, quotes):
            if secid not in quotes or not ref:
                return secid in quotes
            v, clean = position_value(kind, pos["qty"], quotes[secid])
            return min(abs(v - ref), abs(clean - ref)) / ref <= TOLERANCE[kind]

        chosen, how = None, ""
        if cands and cands[0] in q:   # код задан явно — ему доверяем, сумма со скриншота не нужна
            chosen, how = cands[0], "по коду из config"
        elif pos.get("query"):
            try:
                rows = search_moex(pos["query"], kind)
                words = [w.lower() for w in pos["query"].split() if len(w) >= 2]
                rows = [r for r in rows if any(
                    w in " ".join(str(r.get(f) or "") for f in ("secid", "shortname", "name", "isin", "emitent_title")).lower()
                    for w in words)]
                ids = [r["secid"] for r in rows]
                q2 = fetch_quotes(MARKET[kind], ids)
                good = [s for s in ids if ok(s, q2)]
                if len(good) == 1:
                    chosen, how = good[0], f'найдено по «{pos["query"]}»'
                elif good:
                    log.warning("«%s»: несколько похожих бумаг %s — впишите ISIN в config.json",
                                pos["name"], ", ".join(good[:6]))
                else:
                    log.warning("«%s»: не нашёл подходящую бумагу", pos["name"])
            except Exception as e:
                log.warning("поиск %s: %s", pos["name"], e)
                pos.pop("_tried", None)
        if not chosen and cands and not pos.get("query"):
            log.warning("«%s»: код %s не подошёл — проверьте тикер в config.json", pos["name"], cands[0])
        if chosen:
            pos["_secid"], pos["_how"] = chosen, how
            cache[key] = chosen
            changed = True
            log.info("«%s» → %s (%s)", pos["name"], chosen, how)
    if changed:
        save_json("resolved.json", cache)


def mock_quotes(positions):
    out = {}
    for p in positions:
        sid = p.get("_secid") or p["name"]
        unit = float(p["ref_value"]) / p["qty"]
        if p["kind"] == "bond":
            out[sid] = {"price": unit / 10, "prev": unit / 10 * 0.998, "face": 1000, "aci": 0, "shortname": p["name"]}
        else:
            drift = 1 + ((hash(p["name"]) % 41) - 20) / 1000
            out[sid] = {"price": unit * drift, "prev": unit, "shortname": p["name"]}
    return out


def deposit_value(d, today):
    """Вклад или выданный займ с процентами.
    daily  — проценты каждый день с капитализацией, amount = остаток на дату from;
    simple — простые проценты от amount с from по to (выплата в конце)."""
    amount, rate = float(d.get("amount") or 0), float(d.get("rate") or 0) / 100
    start = dt.date.fromisoformat(d["from"]) if d.get("from") else today
    end = dt.date.fromisoformat(d["to"]) if d.get("to") else None
    upto = min(today, end) if end else today
    days = max(0, (upto - start).days)
    if d.get("accrual") == "daily":
        value = amount * (1 + rate / 365) ** days
        at_end = amount * (1 + rate / 365) ** max(0, ((end or today) - start).days)
    else:
        value = amount * (1 + rate * days / 365)
        at_end = amount * (1 + rate * max(0, ((end or today) - start).days) / 365)
    return {"name": d["name"], "kind": d.get("kind", "deposit"), "value": round(value, 2),
            "principal": amount, "accrued": round(value - amount, 2), "rate": d.get("rate"),
            "to": end.isoformat() if end else None, "at_end": round(at_end, 2),
            "per_day": 0.0 if (end and today >= end) else round(
                (value if d.get("accrual") == "daily" else amount) * rate / 365, 2),
            "overdue": bool(end and today > end and d.get("kind") == "loan")}


def next_due(due, today):
    """due: число месяца (16) или "last" — последний день месяца. Возвращает ближайшую дату платежа."""
    import calendar as _cal
    for shift in (0, 1, 2):
        y, m = today.year + (today.month - 1 + shift) // 12, (today.month - 1 + shift) % 12 + 1
        last = _cal.monthrange(y, m)[1]
        day = last if str(due).lower() == "last" else min(int(due), last)
        d = dt.date(y, m, day)
        if d >= today:
            return d
    return None


def debt_info(d, today):
    """Долг. "schedule": [{"date","amount"}] — график платежей (берём ближайший неистёкший),
    либо "due": число месяца / "last" — платёж каждый месяц на всю сумму."""
    out = {"name": d["name"], "value": float(d.get("amount") or 0)}
    sched = sorted(d.get("schedule") or [], key=lambda x: x["date"])
    upcoming = [x for x in sched if x["date"] >= today.isoformat()]
    if upcoming:
        nxt = upcoming[0]
        nd = dt.date.fromisoformat(nxt["date"])
        out.update(due_date=nxt["date"], due_days=(nd - today).days, due_amount=float(nxt["amount"]),
                   later=[{"date": x["date"], "amount": float(x["amount"])} for x in upcoming[1:3]])
    elif d.get("due"):
        nd = next_due(d["due"], today)
        if nd:
            out.update(due_date=nd.isoformat(), due_days=(nd - today).days, due_amount=out["value"])
    return out


# ─────────────────────────── Ожидаемые поступления ───────────────────────────

def update_workcal():
    """Производственный календарь РФ (isdayoff.ru): строка по дням года, 0/2 — рабочий день."""
    cal = load_json("workcal.json", {})
    changed = False
    for y in (now().year, now().year + 1):
        if str(y) in cal or MOCK:
            continue
        try:
            txt = http_get(f"https://isdayoff.ru/api/getdata?year={y}&cc=ru", 20).decode().strip()
            if len(txt) >= 365 and set(txt) <= set("0124"):
                cal[str(y)] = txt
                changed = True
        except Exception as e:
            log.warning("производственный календарь %s: %s", y, e)
    if changed:
        save_json("workcal.json", cal)


def is_workday(d, cal):
    days = cal.get(str(d.year))
    if days and len(days) >= d.timetuple().tm_yday:
        return days[d.timetuple().tm_yday - 1] in "02"
    # нет календаря — выходные и основные госпраздники (без переносов)
    fixed = {(1, i) for i in range(1, 9)} | {(2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)}
    return d.weekday() < 5 and (d.month, d.day) not in fixed


def incoming_info(today, horizon_days=90):
    """"incoming" в config.json:
    {"name","amount","days":[3,18],"workday":"before"} — по числам месяца, с переносом на рабочий день раньше;
    {"name","amount","start":"2026-10-23","every_days":30,"transfer":true} — каждые N дней."""
    import calendar as _cal
    cal = load_json("workcal.json", {})
    end = today + dt.timedelta(days=horizon_days)
    out = []
    for inc in CFG.get("incoming", []):
        dates = []
        if inc.get("days"):
            y, m = today.year, today.month
            for _ in range(5):
                last = _cal.monthrange(y, m)[1]
                for day in inc["days"]:
                    d = dt.date(y, m, min(int(day), last))
                    if inc.get("workday", "before") == "before":
                        while not is_workday(d, cal):
                            d -= dt.timedelta(days=1)
                    dates.append(d)
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        elif inc.get("start") and inc.get("every_days"):
            d, step = dt.date.fromisoformat(inc["start"]), dt.timedelta(days=int(inc["every_days"]))
            while d < today:
                d += step
            while d <= end:
                dates.append(d)
                d += step
        for d in sorted(set(dates)):
            if today <= d <= end:
                out.append({"date": d.isoformat(), "name": inc["name"], "amount": float(inc["amount"]),
                            "kind": "перекладка" if inc.get("transfer") else inc.get("kind", "доход"),
                            "transfer": bool(inc.get("transfer"))})
    return out


def bills_info(today):
    """Регулярные платежи (аренда и т.п.): "bills": [{"name","amount","day"}]."""
    out = []
    for b in CFG.get("bills", []):
        day = b.get("day", 1)
        nd = next_due(day, today)
        last = str(day).lower() == "last"
        paid_this_month = (today.day >= int(day)) if not last else False
        out.append({"name": b["name"], "amount": float(b["amount"]), "due_date": nd.isoformat(),
                    "due_days": (nd - today).days, "paid_this_month": paid_this_month,
                    "is_spending": b.get("spending", True)})
    return out


FX_CACHE = {}


def fx_rate(cur):
    """Курс валюты к рублю: индикативный курс Мосбиржи, запасной — ЦБ. Последний удачный курс сохраняется."""
    cur = cur.upper()
    if cur in ("RUB", "RUR"):
        return 1.0, "₽"
    hit = FX_CACHE.get(cur)
    if hit and time.time() - hit[1] < 3600:
        return hit[0], hit[2]
    if MOCK:
        return {"USD": 82.0, "EUR": 95.0, "CNY": 11.4}.get(cur, 1.0), "демо"
    rate, src = None, ""
    try:
        d = http_json(f"{ISS}/statistics/engines/futures/markets/indicativerates/securities.json?iss.meta=off")
        rows = [r for r in iss_rows(d["securities"]) if str(r.get("secid", "")).upper() == f"{cur}/RUB"]
        if rows:
            rate, src = float(rows[-1]["rate"]), "Мосбиржа"
    except Exception as e:
        log.warning("курс %s (Мосбиржа): %s", cur, e)
    if rate is None:
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(http_get("https://www.cbr.ru/scripts/XML_daily.asp"))
            for v in root.findall("Valute"):
                if v.findtext("CharCode") == cur:
                    rate = float(v.findtext("Value").replace(",", ".")) / float(v.findtext("Nominal"))
                    src = "ЦБ"
        except Exception as e:
            log.warning("курс %s (ЦБ): %s", cur, e)
    saved = load_json("fx.json", {})
    if rate:
        saved[cur] = {"rate": rate, "src": src, "ts": now().isoformat(timespec="seconds")}
        save_json("fx.json", saved)
    elif cur in saved:
        rate, src = saved[cur]["rate"], saved[cur]["src"] + ", сохранённый"
    if rate:
        FX_CACHE[cur] = (rate, time.time(), src)
    return rate, src


def month_breakdown(hist, market_today, accrual_today):
    """Раскладывает изменение капитала с начала месяца:
    биржа (переоценка бумаг) + проценты (вклады и займ) + ваши изменения (всё остальное:
    пополнения, снятия, погашение долгов, правки config.json)."""
    today = now().date().isoformat()
    flows = load_json("flows.json", {})
    flows[today] = {"market": market_today, "accrual": accrual_today}
    flows = dict(sorted(flows.items())[-400:])
    save_json("flows.json", flows)

    m_start = today[:8] + "01"
    before = [d for d in hist if d < m_start]
    in_month = sorted(d for d in hist if d >= m_start)
    if before:
        base = max(before)
    elif in_month:
        base = in_month[0]      # история началась в этом месяце — считаем от первого дня
    else:
        return None
    # раскладка по источникам ведётся не с первого дня истории — начинаем с дня перед первой записью
    first_flow = min(flows)
    if base < first_flow:
        prior = [d for d in hist if d < first_flow]
        base = max(prior) if prior else first_flow
    days = [d for d in flows if base < d <= today]
    market = sum(flows[d]["market"] for d in days)
    accrual = sum(flows[d]["accrual"] for d in days)
    change = hist[today] - hist[base]
    return {"since": base if base >= m_start else m_start, "change": round(change, 2),
            "market": round(market, 2), "accrual": round(accrual, 2),
            "manual": round(change - market - accrual, 2)}


def update_portfolio():
    all_pos = [p for a in CFG["accounts"] for p in a["positions"]]
    if MOCK:
        for p in all_pos:
            p["_secid"], p["_how"] = p.get("secid") or p["name"], "демо"
        quotes = mock_quotes(all_pos)
    else:
        todo = [p for p in all_pos if "_secid" not in p and time.time() - p.get("_tried", 0) > 3600]
        if todo:
            for p in todo:
                p["_tried"] = time.time()
            resolve_positions(todo)
        quotes = {}
        for kind in ("share", "fund", "bond"):
            ids = [p["_secid"] for p in all_pos if p["kind"] == kind and p.get("_secid")]
            if ids:
                quotes.update(fetch_quotes(MARKET[kind], ids))

    accounts, total, total_prev, rows = [], 0.0, 0.0, []
    for acc in CFG["accounts"]:
        a_val = float(acc.get("cash") or 0)
        a_prev = a_val
        for p in acc["positions"]:
            q = quotes.get(p.get("_secid"))
            if q and q.get("faceunit") not in (None, "SUR", "RUB"):
                q = None  # валютные бумаги не пересчитываем — берём сумму со скриншота
            if q:
                v, _ = position_value(p["kind"], p["qty"], q)
                pv = position_value(p["kind"], p["qty"], q, q["prev"])[0] if q.get("prev") else v
                live = True
            else:
                v = pv = float(p.get("ref_value") or 0)
                live = False
            a_val += v
            a_prev += pv
            rows.append({"name": p["name"], "kind": p["kind"], "value": round(v, 2),
                         "change": round(v - pv, 2), "change_pct": round((v / pv - 1) * 100, 2) if pv else 0,
                         "live": live, "secid": p.get("_secid"), "matched": p.get("_how"),
                         "moex_name": (q or {}).get("shortname"), "account": acc["name"]})
        accounts.append({"name": acc["name"], "value": round(a_val, 2), "change": round(a_val - a_prev, 2)})
        total += a_val
        total_prev += a_prev

    today = now().date()
    deposits = [deposit_value(d, today) for d in CFG.get("deposits", [])]
    debts = [debt_info(d, today) for d in CFG.get("debts", [])]
    cash_fx = []
    for c in CFG.get("cash_fx", []):
        rate, src = fx_rate(c.get("currency", "USD"))
        cash_fx.append({"name": c["name"], "amount": float(c["amount"]), "currency": c.get("currency", "USD").upper(),
                        "rate": rate, "src": src, "value": round(float(c["amount"]) * rate, 2) if rate else 0.0,
                        "no_rate": not rate})
    assets = total + sum(d["value"] for d in deposits) + sum(c["value"] for c in cash_fx)
    debt_total = sum(d["value"] for d in debts)
    savings = assets - debt_total

    hist = load_json("history.json", {})
    hist[now().date().isoformat()] = round(savings, 2)
    hist = dict(sorted(hist.items())[-400:])
    save_json("history.json", hist)
    month = month_breakdown(hist, round(total - total_prev, 2),
                            round(sum(d.get("per_day") or 0 for d in deposits), 2))

    alloc = {"Акции": 0.0, "Облигации": 0.0, "Фонды": 0.0}
    for r in rows:
        alloc[{"share": "Акции", "bond": "Облигации", "fund": "Фонды"}[r["kind"]]] += r["value"]
    alloc["Деньги"] = sum(float(a.get("cash") or 0) for a in CFG["accounts"])
    alloc["Валюта"] = sum(c["value"] for c in cash_fx)
    alloc["Вклады"] = sum(d["value"] for d in deposits if d["kind"] != "loan")
    alloc["Займ"] = sum(d["value"] for d in deposits if d["kind"] == "loan")
    alloc = [{"name": k, "value": round(v, 2)} for k, v in alloc.items() if v > 0]

    movers = sorted([r for r in rows if r["live"] and r["kind"] in ("share", "fund") and r["change"]],
                    key=lambda r: abs(r["change"]), reverse=True)[:3]
    accrual_today = round(sum(d.get("per_day") or 0 for d in deposits), 2)
    set_state("portfolio", {
        "total": round(total, 2), "change": round(total - total_prev, 2),
        "accrual_today": accrual_today, "month": month, "day_total": round(total - total_prev + accrual_today, 2),
        "change_pct": round((total / total_prev - 1) * 100, 2) if total_prev else 0,
        "accounts": accounts, "deposits": deposits, "debts": debts, "cash_fx": cash_fx, "savings": round(savings, 2),
        "assets": round(assets, 2), "debt_total": round(debt_total, 2),
        "history": [{"d": k, "v": v} for k, v in hist.items()][-120:],
        "movers": movers, "positions": rows, "alloc": alloc,
        "stale": [r["name"] for r in rows if not r["live"]] + [f'{c["name"]} (нет курса {c["currency"]})' for c in cash_fx if c["no_rate"]],
        "updated": now().isoformat(),
    })


# ─────────────────────────── Купоны и дивиденды ───────────────────────────

def iss_all(url, block):
    """Забирает все страницы блока ISS (по 100 строк)."""
    rows, start = [], 0
    while True:
        sep = "&" if "?" in url else "?"
        d = http_json(f"{url}{sep}iss.meta=off&limit=100&start={start}")
        part = iss_rows(d[block]) if block in d else []
        rows += part
        if len(part) < 100 or start > 1000:
            return rows, d
        start += 100


def update_payments():
    today = now().date()
    horizon = today + dt.timedelta(days=int(CFG.get("payments_days_ahead", 90)))
    all_pos = [p for a in CFG["accounts"] for p in a["positions"]]
    items = []
    if MOCK:
        demo = [("ОФЗ 26238", "купон", 6, 2984.0), ("Сбербанк", "дивиденд", 21, None),
                ("Селигдар 001P-10", "купон", 11, 263.5), ("ОФЗ 26252", "купон", 34, 13398.0),
                ("Балтийский лизинг БО", "амортизация", 40, 2100.0)]
        for n, k, dd, a in demo:
            items.append({"date": (today + dt.timedelta(days=dd)).isoformat(), "name": n, "kind": k, "amount": a})
    else:
        for p in all_pos:
            sid, qty = p.get("_secid"), p["qty"]
            if not sid:
                continue
            try:
                if p["kind"] == "bond":
                    url = f"{ISS}/statistics/engines/stock/markets/bonds/bondization/{sid}.json?iss.only=coupons,amortizations"
                    coupons, d = iss_all(url, "coupons")
                    for c in coupons:
                        day = str(c.get("coupondate") or "")[:10]
                        if day and today <= dt.date.fromisoformat(day) <= horizon:
                            v = c.get("value_rub") or c.get("value")
                            items.append({"date": day, "name": p["name"], "kind": "купон",
                                          "amount": round(float(v) * qty, 2) if v else None})
                    for a in iss_rows(d.get("amortizations", {"columns": [], "data": []})):
                        day = str(a.get("amortdate") or "")[:10]
                        v = a.get("value_rub") or a.get("value")
                        face = a.get("facevalue") or a.get("initialfacevalue")
                        if day and today <= dt.date.fromisoformat(day) <= horizon and v:
                            full = face and abs(float(v) - float(face)) < 0.01 and a.get("valueprc") in (100, 100.0, None)
                            items.append({"date": day, "name": p["name"], "kind": "погашение" if full else "амортизация",
                                          "amount": round(float(v) * qty, 2)})
                else:
                    divs, _ = iss_all(f"{ISS}/securities/{sid}/dividends.json", "dividends")
                    for v in divs:
                        day = str(v.get("registryclosedate") or "")[:10]
                        if day and today <= dt.date.fromisoformat(day) <= horizon and v.get("value"):
                            items.append({"date": day, "name": p["name"], "kind": "дивиденд",
                                          "amount": round(float(v["value"]) * qty, 2)})
            except Exception as e:
                log.warning("выплаты %s: %s", p["name"], e)
    items.sort(key=lambda x: x["date"])
    month_end = today + dt.timedelta(days=30)
    set_state("payments", {
        "items": items[:12],
        "next30": round(sum(i["amount"] or 0 for i in items if i["date"] <= month_end.isoformat()), 2),
        "updated": now().isoformat()})


# ─────────────────────────── Погода ───────────────────────────

def update_weather():
    w = CFG["weather"]
    if MOCK:
        set_state("weather", {"name": w["name"], "temp": 7.4, "feels": 4.1, "humidity": 81, "wind": 4.2,
                              "code": 3, "tmax": 9.0, "tmin": 3.2, "precip": 40,
                              "tomorrow": {"tmax": 11.0, "tmin": 4.0, "code": 61, "precip": 70},
                              "updated": now().isoformat()})
        return
    q = urllib.parse.urlencode({
        "latitude": w["lat"], "longitude": w["lon"], "timezone": CFG.get("timezone", "auto"),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
        "wind_speed_unit": "ms", "forecast_days": 2})
    d = http_json(f"https://api.open-meteo.com/v1/forecast?{q}")
    c, day = d["current"], d["daily"]
    set_state("weather", {
        "name": w["name"], "temp": c["temperature_2m"], "feels": c["apparent_temperature"],
        "humidity": c["relative_humidity_2m"], "wind": c["wind_speed_10m"], "code": c["weather_code"],
        "tmax": day["temperature_2m_max"][0], "tmin": day["temperature_2m_min"][0],
        "precip": day["precipitation_probability_max"][0],
        "tomorrow": {"tmax": day["temperature_2m_max"][1], "tmin": day["temperature_2m_min"][1],
                     "code": day["weather_code"][1], "precip": day["precipitation_probability_max"][1]},
        "updated": now().isoformat()})


# ─────────────────────────── Календарь iCloud ───────────────────────────

def update_calendar():
    days = int(CFG.get("calendar_days_ahead", 14))
    start = now()
    if MOCK:
        base = start.replace(minute=0, second=0, microsecond=0)
        ev = [("Синк с Артуром", 1, 0.5), ("Бюджет Q4 — ревью", 3, 1), ("Ужин", 9, 2),
              ("Стоматолог", 26, 1), ("Планёрка", 48, 0.5), ("Отпуск", 24 * 6, 0)]
        set_state("calendar", {"events": [
            {"title": t, "start": (base + dt.timedelta(hours=h)).isoformat(),
             "end": (base + dt.timedelta(hours=h + (d or 24))).isoformat(), "allday": d == 0}
            for t, h, d in ev], "updated": now().isoformat()})
        return
    try:
        import icalendar, recurring_ical_events
    except ImportError:
        raise RuntimeError("не установлены icalendar / recurring_ical_events (pip install -r requirements.txt)")
    events = []
    for url in CFG.get("calendars", []):
        if "ВСТАВЬТЕ" in url:
            raise RuntimeError("добавьте ссылку на календарь iCloud в config.json")
        url = url.replace("webcal://", "https://", 1)
        cal = icalendar.Calendar.from_ical(http_get(url, 30))
        for e in recurring_ical_events.of(cal).between(start - dt.timedelta(hours=12), start + dt.timedelta(days=days)):
            s, en = e.get("DTSTART").dt, (e.get("DTEND").dt if e.get("DTEND") else None)
            allday = not isinstance(s, dt.datetime)
            if allday:
                s = dt.datetime.combine(s, dt.time(), TZ)
                en = dt.datetime.combine(en, dt.time(), TZ) if en else s + dt.timedelta(days=1)
            else:
                s = s.astimezone(TZ) if s.tzinfo else s.replace(tzinfo=TZ)
                en = (en.astimezone(TZ) if en.tzinfo else en.replace(tzinfo=TZ)) if en else s
            if en <= start:
                continue
            events.append({"title": str(e.get("SUMMARY", "Без названия")), "start": s.isoformat(),
                           "end": en.isoformat(), "allday": allday,
                           "location": str(e.get("LOCATION", "") or "")})
    events.sort(key=lambda x: x["start"])
    set_state("calendar", {"events": events[:12], "updated": now().isoformat()})


# ─────────────────────────── Датчик дома (BLE, прошивка pvvx) ───────────────────────────

def parse_pvvx(data: bytes):
    if len(data) >= 15:   # формат pvvx (custom): температура ×0.01, влажность ×0.01, little-endian
        t = int.from_bytes(data[6:8], "little", signed=True) / 100
        h = int.from_bytes(data[8:10], "little") / 100
        return t, h, data[12]
    if len(data) == 13:   # формат atc1441: температура ×0.1 big-endian, влажность в %
        t = int.from_bytes(data[6:8], "big", signed=True) / 10
        return t, data[8], data[9]
    return None


def ble_thread():
    try:
        from bleak import BleakScanner
    except ImportError:
        set_state("indoor", error="bleak не установлен (pip install bleak)")
        return
    uuid = "0000181a-0000-1000-8000-00805f9b34fb"
    prefix = CFG["indoor_sensor"].get("name_prefix", "")

    def cb(dev, adv):
        raw = adv.service_data.get(uuid)
        if not raw or (prefix and not (adv.local_name or dev.name or "").startswith(prefix)):
            return
        r = parse_pvvx(bytes(raw))
        if r:
            set_state("indoor", {"temp": round(r[0], 1), "humidity": round(r[1]), "battery": r[2],
                                 "source": "датчик", "updated": now().isoformat()})

    async def run():
        while True:
            try:
                async with BleakScanner(cb):
                    await asyncio.sleep(3600)
            except Exception as e:
                log.warning("Bluetooth: %s", e)
                await asyncio.sleep(30)

    asyncio.run(run())


# ─────────────────────────── Траты и шаги ───────────────────────────

def spend_summary(fname="spend.json"):
    items = load_json(fname, [])
    n = now()
    month = [i for i in items if i["ts"][:7] == n.strftime("%Y-%m")]
    today = [i for i in month if i["ts"][:10] == n.date().isoformat()]
    by_day = {}
    for i in month:
        by_day[i["ts"][:10]] = by_day.get(i["ts"][:10], 0) + i["amount"]
    return {"month_total": round(sum(i["amount"] for i in month), 2),
            "today_total": round(sum(i["amount"] for i in today), 2),
            "count": len(month), "last": list(reversed(month[-4:])),
            "by_day": by_day, "budget": CFG.get("spend_budget_month") or 0,
            "days_in_month": ((n.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)).day,
            "day": n.day}


def steps_summary():
    s = load_json("steps.json", {})
    today = now().date()
    week = []
    for k in range(6, -1, -1):
        d = (today - dt.timedelta(days=k)).isoformat()
        week.append({"d": d, "v": s.get(d, {}).get("steps", 0) if isinstance(s.get(d), dict) else s.get(d, 0)})
    t = s.get(today.isoformat())
    return {"today": week[-1]["v"], "goal": CFG.get("steps_goal", 10000), "week": week,
            "updated": t.get("updated") if isinstance(t, dict) else None}


def to_number(x):
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace(" ", "").replace(" ", "").replace("₽", "").replace(",", ".")
    return float(s)


# ─────────────────────────── HTTP ───────────────────────────

def snapshot():
    with LOCK:
        st = json.loads(json.dumps(STATE, ensure_ascii=False))
    st["spend"] = spend_summary()
    st["income"] = spend_summary("income.json")
    st["bills"] = bills_info(now().date())
    st["incoming"] = incoming_info(now().date())
    st["steps"] = steps_summary()
    st["server_time"] = now().isoformat()
    st["mock"] = MOCK
    if st.get("portfolio"):
        st["portfolio"].pop("positions", None)
    return st


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeDash/1.0"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, query):
        tok = self.headers.get("X-Token") or (query.get("token") or [""])[0]
        try:  # заголовки приходят в latin-1 — возвращаем кириллицу в нормальный вид
            tok = tok.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return tok and tok == CFG.get("token")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self.send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/state":
            return self.send(200, snapshot())
        if u.path == "/api/positions":
            with LOCK:
                p = STATE.get("portfolio") or {}
            return self.send(200, {"positions": p.get("positions", []), "updated": p.get("updated")})
        if u.path.startswith("/static/"):
            f = (STATIC / u.path[len("/static/"):]).resolve()
            if STATIC in f.parents and f.is_file():
                ct = {"css": "text/css", "js": "application/javascript", "svg": "image/svg+xml",
                      "png": "image/png"}.get(f.suffix[1:], "application/octet-stream")
                return self.send(200, f.read_bytes(), ct)
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(u.query)
        if not self.authorized(query):
            return self.send(401, {"ok": False, "error": "неверный токен"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8") if n else ""
            body = json.loads(raw) if raw.strip().startswith("{") else dict(urllib.parse.parse_qsl(raw))
        except Exception:
            return self.send(400, {"ok": False, "error": "не разобрал тело запроса"})
        body.update({k: v[0] for k, v in query.items() if k != "token"})

        try:
            ledgers = {"/api/spend": ("spend.json", "Трата"), "/api/income": ("income.json", "Доход")}
            if u.path in ledgers:
                fname, title = ledgers[u.path]
                amount = to_number(body.get("amount"))
                if not (0 < amount < 100_000_000):
                    raise ValueError
                items = load_json(fname, [])
                items.append({"ts": now().isoformat(timespec="seconds"), "amount": round(amount, 2),
                              "note": str(body.get("note") or "").strip()[:80]})
                save_json(fname, items)
                sm = spend_summary(fname)
                return self.send(200, {"ok": True, "month_total": sm["month_total"],
                                       "message": f"Записал: {title.lower()} {amount:,.0f} ₽. За месяц: {sm['month_total']:,.0f} ₽".replace(",", " ")})
            if u.path.endswith("/undo") and u.path[:-5] in ledgers:
                fname, title = ledgers[u.path[:-5]]
                items = load_json(fname, [])
                if not items:
                    return self.send(200, {"ok": False, "message": "Нечего отменять"})
                last = items.pop()
                save_json(fname, items)
                return self.send(200, {"ok": True, "message": f"Удалил: {title.lower()} {last['amount']:,.0f} ₽".replace(",", " ")})
            if u.path == "/api/steps":
                steps = int(to_number(body.get("steps")))
                d = body.get("date") or now().date().isoformat()
                s = load_json("steps.json", {})
                s[d] = {"steps": steps, "updated": now().isoformat(timespec="seconds")}
                save_json("steps.json", dict(sorted(s.items())[-120:]))
                return self.send(200, {"ok": True, "message": f"Шаги: {steps}"})
            if u.path == "/api/indoor":
                set_state("indoor", {"temp": to_number(body["temp"]), "humidity": round(to_number(body["humidity"])),
                                     "source": "вручную", "updated": now().isoformat()})
                return self.send(200, {"ok": True})
        except (ValueError, TypeError, KeyError):
            return self.send(400, {"ok": False, "error": "проверьте число"})
        return self.send(404, {"ok": False, "error": "not found"})


def watch_config():
    """Подхватывает правки config.json без перезапуска сервера."""
    global CFG, CFG_MTIME
    while True:
        time.sleep(5)
        try:
            m = CFG_PATH.stat().st_mtime
            if m == CFG_MTIME:
                continue
            CFG_MTIME = m
            new = json.loads(CFG_PATH.read_text("utf-8"))
        except json.JSONDecodeError as e:
            log.warning("config.json с ошибкой (строка %d): %s — работаю на прежних настройках", e.lineno, e.msg)
            set_state("config", error=f"config.json: ошибка в строке {e.lineno} — {e.msg}")
            continue
        except Exception as e:
            log.warning("config.json: %s", e)
            continue
        CFG = new
        set_state("config", True)
        log.info("config.json перечитан")
        try:
            update_portfolio()
        except Exception as e:
            log.warning("portfolio: %s", e)


def loop(name, fn, every):
    def run():
        while True:
            try:
                fn()
            except Exception as e:
                log.warning("%s: %s", name, e)
                set_state(name, error=str(e))
            time.sleep(every if name not in STATE["errors"] else min(every, 120))
    threading.Thread(target=run, daemon=True, name=name).start()


def cli_find(query):
    rows = search_moex(query, None, 50)
    if not rows:
        print("Ничего не нашёл.")
    for r in rows:
        print(f'{r["secid"]:<16} {r.get("isin") or "":<14} {r.get("group",""):<14} {r["shortname"]:<22} {r.get("name","")}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--find", help="поиск бумаги на Мосбирже")
    ap.add_argument("--rematch", action="store_true", help="забыть сопоставления бумаг и подобрать заново")
    a = ap.parse_args()
    if a.find:
        return cli_find(a.find)
    if a.rematch and (DATA / "resolved.json").exists():
        (DATA / "resolved.json").unlink()

    loop("portfolio", update_portfolio, 300)
    threading.Thread(target=watch_config, daemon=True, name="config").start()
    loop("weather", update_weather, 900)
    loop("calendar", update_calendar, 600)
    loop("workcal", update_workcal, 24 * 3600)

    def payments_later():
        time.sleep(5 if MOCK else 60)   # сначала портфель должен сопоставить бумаги
        loop("payments", update_payments, 6 * 3600)
    threading.Thread(target=payments_later, daemon=True).start()
    if CFG.get("indoor_sensor", {}).get("enabled") and not MOCK:
        threading.Thread(target=ble_thread, daemon=True, name="ble").start()
    elif MOCK:
        set_state("indoor", {"temp": 23.4, "humidity": 38, "battery": 87, "source": "демо",
                             "updated": now().isoformat()})

    port = int(CFG.get("port", 8765))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("Дашборд: http://localhost:%d %s", port, "(демо-режим)" if MOCK else "")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
