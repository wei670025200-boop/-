from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent / "data" / "ssq_history.csv"


@dataclass(frozen=True)
class LocalHistoryRow:
    issue: str
    date: str
    reds: tuple[int, int, int, int, int, int]
    blue: int
    sales_rmb: float
    pool_after_rmb: float
    t1: int
    t2: int
    t3: int | None
    t4: int | None
    t5: int | None
    t6: int | None
    promo_sixth_double: bool | None

    @property
    def has_full_tiers(self) -> bool:
        return all(v is not None for v in (self.t3, self.t4, self.t5, self.t6))


def _opt_int(value: str) -> int | None:
    value = value.strip()
    return None if value == "" else int(float(value))


def _opt_bool(value: str) -> bool | None:
    value = value.strip()
    if value == "":
        return None
    if value not in {"0", "1"}:
        raise ValueError(f"promo_sixth_double must be blank/0/1, got {value!r}")
    return value == "1"


def load_ssq_history(path: str | Path = DEFAULT_HISTORY_PATH) -> List[LocalHistoryRow]:
    p = Path(path)
    rows: list[LocalHistoryRow] = []
    with p.open("r", encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            reds = tuple(int(raw[f"r{i}"]) for i in range(1, 7))
            if len(set(reds)) != 6 or tuple(sorted(reds)) != reds or not all(1 <= x <= 33 for x in reds):
                raise ValueError(f"invalid reds at issue {raw['issue']}: {reds}")
            blue = int(raw["blue"])
            if not 1 <= blue <= 16:
                raise ValueError(f"invalid blue at issue {raw['issue']}: {blue}")
            rows.append(LocalHistoryRow(
                issue=str(raw["issue"]), date=raw["date"], reds=reds, blue=blue,
                sales_rmb=float(raw["sales_rmb"]), pool_after_rmb=float(raw["pool_after_rmb"]),
                t1=int(raw["t1"]), t2=int(raw["t2"]),
                t3=_opt_int(raw["t3"]), t4=_opt_int(raw["t4"]),
                t5=_opt_int(raw["t5"]), t6=_opt_int(raw["t6"]),
                promo_sixth_double=_opt_bool(raw["promo_sixth_double"]),
            ))
    if not rows:
        raise ValueError("empty SSQ history ledger")
    if len({r.issue for r in rows}) != len(rows):
        raise ValueError("duplicate issue in SSQ history ledger")
    if any(rows[i].date >= rows[i + 1].date for i in range(len(rows) - 1)):
        raise ValueError("history dates must be strictly increasing")
    return rows


def latest_completed(path: str | Path = DEFAULT_HISTORY_PATH) -> LocalHistoryRow:
    return load_ssq_history(path)[-1]


def full_tier_history(path: str | Path = DEFAULT_HISTORY_PATH) -> List[LocalHistoryRow]:
    return [r for r in load_ssq_history(path) if r.has_full_tiers]