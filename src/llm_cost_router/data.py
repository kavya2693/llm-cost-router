"""Seeded generator for the 200-case evaluation suite and a year of request traffic.

Every case carries its answer by construction: the generator picks the fields, labels,
facts or arithmetic first and then writes the text around them, so grading never depends
on a model judging another model.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import random
from pathlib import Path
from typing import Any

from llm_cost_router.config import SEED, TASK_TYPES

CASES_PER_TYPE = 50
DEV_PER_TYPE = 20  # the router's thresholds are tuned on these; the rest is held out

COMPANIES = [
    "Harbor Point Foods",
    "Kestrel Hardware",
    "Northgate Pharmacy",
    "Bluewater Marine Supply",
    "Cedar & Pine Interiors",
    "Atlas Office Systems",
    "Greenfield Bakeries",
    "Summit Auto Parts",
    "Lakeside Clinics",
    "Orchid Hotels",
    "Ironbridge Construction",
    "Meadowlark Pet Care",
    "Silverline Electronics",
    "Copperleaf Cafes",
    "Redstone Logistics",
    "Brightside Schools",
]
CITIES = [
    "Dubai",
    "Abu Dhabi",
    "Sharjah",
    "Riyadh",
    "Doha",
    "Muscat",
    "Manama",
    "Jeddah",
    "Al Ain",
    "Kuwait City",
    "Dammam",
    "Ras Al Khaimah",
]
PRODUCTS = [
    ("nitrile gloves, box of 100", "GLV"),
    ("thermal receipt rolls", "RCP"),
    ("A4 copy paper", "PAP"),
    ("LED panel light 60x60", "LED"),
    ("stainless prep table", "TBL"),
    ("hand sanitiser 5L", "SAN"),
    ("cable ties 300mm", "CTI"),
    ("safety boots size 42", "BOT"),
    ("coffee beans 1kg", "COF"),
    ("printer toner black", "TON"),
    ("bubble wrap roll", "BWR"),
    ("hi-vis vest", "VST"),
]
FIRST_NAMES = ["Aisha", "Omar", "Priya", "Daniel", "Fatima", "Rahul", "Sara", "Yusuf", "Leena", "Marco"]
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _date_text(rng: random.Random, d: dt.date) -> str:
    style = rng.randrange(4)
    if style == 0:
        return d.isoformat()
    if style == 1:
        return f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    if style == 2:
        return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    return f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}"


def _typo(rng: random.Random, text: str, rate: float) -> str:
    """Swap adjacent letters in a few words, the way hurried emails read."""
    words = text.split(" ")
    for i, w in enumerate(words):
        if len(w) > 4 and w.isalpha() and rng.random() < rate:
            j = rng.randrange(1, len(w) - 2)
            words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2 :]
    return " ".join(words)


# --------------------------------------------------------------------------- extraction


def _extraction_case(rng: random.Random, idx: int, hard: bool) -> dict[str, Any]:
    company = rng.choice(COMPANIES)
    city = rng.choice(CITIES)
    product, prefix = rng.choice(PRODUCTS)
    sku = f"{prefix}-{rng.randint(1000, 9999)}"
    qty = rng.choice([rng.randint(2, 60), rng.randint(60, 400), rng.randint(400, 2500)])
    order_id = f"PO-{rng.randint(10000, 99999)}"
    base = dt.date(2026, 1, 5) + dt.timedelta(days=rng.randint(0, 330))
    has_date = rng.random() > 0.22  # about one email in five gives no delivery date
    sender = rng.choice(FIRST_NAMES)

    lines = [rng.choice(["Hi team,", "Hello,", "Dear sales,", "Good morning,"])]
    if hard:
        old_qty = qty + rng.choice([-1, 1]) * rng.randint(5, max(6, qty // 2))
        old_qty = max(1, old_qty)
        old_id = f"PO-{rng.randint(10000, 99999)}"
        lines.append(
            f"Following up on our last order {old_id}, which arrived fine. "
            f"Please raise a new order {order_id} for {company}."
        )
        lines.append(
            f"We need {product} (SKU {sku}). I first wrote {old_qty} units, but please make it "
            f"{qty} units instead."
        )
        lines.append(f"Our phone is +971 4 {rng.randint(100, 999)} {rng.randint(1000, 9999)}.")
    else:
        lines.append(f"Please book order {order_id} for {company}: {qty} units of {product}, SKU {sku}.")
    if has_date:
        lines.append(f"Delivery to our {city} site by {_date_text(rng, base)}.")
    else:
        lines.append(f"Ship to our {city} site; date to be confirmed next week.")
    lines.append(f"Thanks, {sender}")
    text = "\n".join(lines)
    if hard:
        text = _typo(rng, text, 0.08)

    answer = {
        "order_id": order_id,
        "customer": company,
        "sku": sku,
        "quantity": qty,
        "delivery_city": city,
        "delivery_date": base.isoformat() if has_date else None,
    }
    return {"input": text, "answer": answer}


# ------------------------------------------------------------------------ classification

LABELS = ("billing", "shipping", "returns", "account_access", "product_question", "cancellation")

EASY_TICKETS: dict[str, list[str]] = {
    "billing": [
        "I was charged twice for order {n}, please refund the duplicate payment.",
        "The invoice for {n} shows the wrong VAT number, can you reissue it?",
        "My card was debited {amt} AED but the order total was lower.",
    ],
    "shipping": [
        "Order {n} has not arrived and tracking has not moved in {d} days.",
        "Courier marked {n} as delivered but nothing came to my door.",
        "Can you tell me when order {n} will be dispatched?",
    ],
    "returns": [
        "The blender in order {n} arrived cracked, I want to send it back.",
        "How do I return the shoes from {n}? They are the wrong size.",
        "I posted the return label for {n} last week, when will it be processed?",
    ],
    "account_access": [
        "I cannot log in, the reset password email never arrives.",
        "My account is locked after too many attempts, please unlock it.",
        "The two-factor code is going to my old phone number.",
    ],
    "product_question": [
        "Does the {p} work with 110V or only 220V?",
        "Is the {p} dishwasher safe?",
        "What is the warranty period on the {p}?",
    ],
    "cancellation": [
        "Please cancel order {n}, I ordered it by mistake.",
        "I want to cancel my subscription before the next renewal.",
        "Cancel {n} please, I found it cheaper elsewhere.",
    ],
}

# Hard tickets mention a second topic; the label is what the customer is actually asking for.
HARD_TICKETS: dict[str, list[str]] = {
    "billing": [
        "Order {n} arrived late but that is fine. What is not fine is that I was charged the "
        "express delivery fee anyway, I want that fee back.",
        "I returned an item last month and that went smoothly, but my statement still shows "
        "the full {amt} AED. The refund amount was never credited.",
    ],
    "shipping": [
        "I was going to cancel {n} because it is so late, but I would rather just have it. "
        "Where is it? Tracking still says label created after {d} days.",
        "Not asking for a refund. I just need to know why {n} went to the wrong emirate and "
        "when it will get to me.",
    ],
    "returns": [
        "The payment for {n} went through fine. The problem is the {p} stopped working after "
        "two days and I want to send it back for a replacement.",
        "Delivery was quick, thanks. Unfortunately the {p} is the wrong colour, how do I send it back?",
    ],
    "account_access": [
        "I want to check the status of {n} but I cannot get into my account, it says my email "
        "is not registered even though I ordered with it.",
        "Before I place another order I need to get back into my profile, the reset link "
        "says it has expired every time.",
    ],
    "product_question": [
        "I am thinking of returning the {p} I already have and buying the bigger model instead. "
        "Does the bigger one fit a 60cm shelf?",
        "Not a complaint, just a question before I buy a second {p}: is the newer version "
        "compatible with the old charger?",
    ],
    "cancellation": [
        "Order {n} has not shipped yet and I no longer need it. Please stop it and do not charge me.",
        "Tracking says {n} is still at the warehouse. I do not want it anymore, please call it "
        "off before it is dispatched.",
    ],
}

TICKET_PRODUCTS = ["air fryer", "phone case", "office chair", "kettle", "baby monitor", "desk lamp"]


def _classification_case(rng: random.Random, idx: int, hard: bool) -> dict[str, Any]:
    label = LABELS[idx % len(LABELS)]
    pool = HARD_TICKETS[label] if hard else EASY_TICKETS[label]
    text = rng.choice(pool).format(
        n=f"#{rng.randint(4000000, 4999999)}",
        d=rng.randint(4, 15),
        amt=rng.randint(49, 1900),
        p=rng.choice(TICKET_PRODUCTS),
    )
    if rng.random() < 0.3:
        text = text.lower()
    if rng.random() < 0.4:
        text += rng.choice([" Thanks.", " Regards, " + rng.choice(FIRST_NAMES), " Urgent!!", ""])
    if hard:
        text = _typo(rng, text, 0.06)
    return {"input": text, "answer": label}


# ------------------------------------------------------------------------- summarization

CAUSES = {
    "conveyor": ("a conveyor motor failed", ["conveyor"]),
    "staffing": ("two pickers called in sick", ["sick", "staff", "picker", "absen", "short"]),
    "system": ("the warehouse management system went offline", ["system", "offline", "outage", "wms"]),
    "weather": ("a sandstorm closed the access road", ["sandstorm", "storm", "weather", "road"]),
    "carrier": ("the line-haul carrier missed its pickup", ["carrier", "pickup", "truck", "haul"]),
}
ACTIONS = {
    "overtime": ("approved an extra overtime shift", ["overtime", "extra shift"]),
    "reroute": ("rerouted the orders through the backup site", ["rerout", "backup"]),
    "spare": ("ordered a spare part for next-day fitting", ["spare", "part"]),
    "notify": ("emailed every affected customer with a new date", ["email", "notif", "contact", "inform"]),
}
SITES = ["Jebel Ali", "Al Quoz", "Mussafah", "KIZAD", "Dubai South", "Sharjah Airport Free Zone"]

FILLER = [
    "The morning shift started on time and the safety briefing was completed.",
    "Canteen service was normal.",
    "Inbound volumes were in line with the weekly plan.",
    "The quarterly fire drill is still scheduled for next month.",
    "Cycle counts in aisle {a} matched the system to within {p} units.",
    "A visitor from the regional office toured the mezzanine.",
    "Temperature in the cold room stayed between 2 and 6 degrees.",
]


def _summarization_case(rng: random.Random, idx: int, hard: bool) -> dict[str, Any]:
    site = rng.choice(SITES)
    cause_key = rng.choice(list(CAUSES))
    action_key = rng.choice(list(ACTIONS))
    affected = rng.randint(40, 3200)
    cause_text, cause_kw = CAUSES[cause_key]
    action_text, action_kw = ACTIONS[action_key]

    facts = [
        f"At the {site} warehouse {affected} outbound orders missed their cut-off yesterday "
        f"because {cause_text}.",
        f"The site manager {action_text}.",
    ]
    filler = [f.format(a=rng.randint(3, 40), p=rng.randint(1, 9)) for f in FILLER]
    rng.shuffle(filler)
    n_filler = rng.randint(4, 6) if hard else rng.randint(1, 2)
    sentences = filler[:n_filler]
    if hard:
        # A second, unrelated number makes it easy to report the wrong count.
        sentences.append(
            f"For comparison, {affected + rng.randint(500, 4000)} orders shipped on time across "
            f"all sites the same day."
        )
    for fact in facts:
        sentences.insert(rng.randint(0, len(sentences)), fact)
    text = " ".join(sentences)
    answer = {
        "site": site,
        "affected_orders": affected,
        "cause_keywords": cause_kw,
        "action_keywords": action_kw,
    }
    return {"input": text, "answer": answer}


# ----------------------------------------------------------------------------- reasoning


def _reasoning_case(rng: random.Random, idx: int, hard: bool) -> dict[str, Any]:
    if not hard:
        kind = idx % 3
        if kind == 0:
            boxes, per_box = rng.randint(12, 90), rng.choice([6, 8, 12, 24])
            sold = rng.randint(10, boxes * per_box // 2)
            text = (
                f"A store receives {boxes} boxes of water bottles with {per_box} bottles in each box. "
                f"It sells {sold} bottles. How many bottles are left?"
            )
            ans = boxes * per_box - sold
        elif kind == 1:
            price, qty = rng.randint(12, 240), rng.randint(3, 40)
            fee = rng.choice([15, 25, 30, 45])
            text = (
                f"A customer buys {qty} chairs at {price} AED each and pays a delivery fee of {fee} AED. "
                f"What is the total in AED?"
            )
            ans = price * qty + fee
        else:
            per_hour, hours = rng.randint(40, 180), rng.randint(3, 11)
            text = (
                f"A packing line packs {per_hour} parcels per hour and runs for {hours} hours. "
                f"How many parcels does it pack?"
            )
            ans = per_hour * hours
        return {"input": text, "answer": ans}

    kind = idx % 3
    if kind == 0:
        stock, daily = rng.randint(800, 4000), rng.randint(40, 160)
        days = rng.randint(5, 14)
        incoming = rng.randint(10, 45) * 20  # a multiple of 20 keeps the damaged share whole
        damaged_pct = rng.choice([5, 10, 20, 25])
        text = (
            f"A warehouse holds {stock} units. It ships {daily} units a day for {days} days. "
            f"Halfway through, a delivery of {incoming} units arrives, but {damaged_pct}% of that "
            f"delivery is damaged and thrown away. The warehouse phone extension is "
            f"{rng.randint(200, 999)}. How many units are left after the {days} days?"
        )
        ans = stock - daily * days + incoming - incoming * damaged_pct // 100
    elif kind == 1:
        unit, qty = rng.randint(20, 200), rng.randint(10, 60)
        disc = rng.choice([10, 15, 20, 25])
        vat = 5
        text = (
            f"An order has {qty} items at {unit} AED each. A {disc}% discount applies to the whole "
            f"order, then {vat}% VAT is added on the discounted amount. Delivery is free because the "
            f"order is over 100 AED. What is the final amount in AED, rounded down to a whole number?"
        )
        # integer arithmetic so the floor is exact
        ans = unit * qty * (100 - disc) * (100 + vat) // 10_000
    else:
        drivers, routes_each = rng.randint(6, 30), rng.randint(2, 5)
        stops, sick = rng.randint(8, 20), rng.randint(1, 4)
        text = (
            f"A fleet has {drivers} drivers. Each driver runs {routes_each} routes a day and each route "
            f"has {stops} stops. On Monday {sick} drivers are off sick and their routes are not covered. "
            f"Each van holds 900 kg. How many stops are served on Monday?"
        )
        ans = (drivers - sick) * routes_each * stops
    return {"input": text, "answer": ans}


BUILDERS = {
    "extraction": _extraction_case,
    "classification": _classification_case,
    "summarization": _summarization_case,
    "reasoning": _reasoning_case,
}


def generate_suite(seed: int = SEED) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    cases: list[dict[str, Any]] = []
    for task in TASK_TYPES:
        for i in range(CASES_PER_TYPE):
            hard = i % 2 == 1
            case = BUILDERS[task](rng, i, hard)
            cases.append(
                {
                    "id": f"{task[:3]}-{i:03d}",
                    "task": task,
                    "difficulty": "hard" if hard else "easy",
                    "split": "dev" if i < DEV_PER_TYPE else "test",
                    **case,
                }
            )
    return cases


# ------------------------------------------------------------------------------- traffic

TRAFFIC_MIX = {"extraction": 0.42, "classification": 0.33, "summarization": 0.15, "reasoning": 0.10}


def _holiday_lift(d: dt.date) -> float:
    """Request volume lifts around the retail peaks the support desk actually feels."""
    lift = 1.0
    if dt.date(2026, 2, 18) <= d <= dt.date(2026, 3, 22):  # Ramadan and Eid al-Fitr
        lift *= 1.25
    if dt.date(2026, 11, 23) <= d <= dt.date(2026, 12, 2):  # White Friday week
        lift *= 1.9
    if dt.date(2026, 12, 15) <= d <= dt.date(2026, 12, 31):  # year-end sales
        lift *= 1.35
    if dt.date(2026, 5, 26) <= d <= dt.date(2026, 5, 30):  # Eid al-Adha, offices closed
        lift *= 0.7
    return lift


def generate_traffic(seed: int = SEED, base_daily: int = 38_000) -> list[dict[str, Any]]:
    """Daily request counts by task type for one year, with gaps where logging dropped out."""
    rng = random.Random(seed + 1)
    weekday = [1.08, 1.12, 1.10, 1.05, 0.92, 0.80, 0.93]  # Mon..Sun
    rows: list[dict[str, Any]] = []
    start = dt.date(2026, 1, 1)
    for k in range(365):
        d = start + dt.timedelta(days=k)
        trend = 1.0 + 0.18 * k / 365
        volume = base_daily * trend * weekday[d.weekday()] * _holiday_lift(d) * rng.lognormvariate(0, 0.07)
        if rng.random() < 0.01:  # an incident or a bot storm
            volume *= rng.uniform(2.0, 3.5)
        missing = rng.random() < 0.02
        row: dict[str, Any] = {"date": d.isoformat()}
        for task, share in TRAFFIC_MIX.items():
            row[task] = "" if missing else round(volume * share * rng.uniform(0.95, 1.05))
        rows.append(row)
    return rows


def write_suite(path: Path, cases: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for case in cases:
            f.write(json.dumps(case) + "\n")


def write_traffic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", *TASK_TYPES])
        writer.writeheader()
        writer.writerows(rows)


def load_suite(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def load_traffic(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return list(csv.DictReader(f))
