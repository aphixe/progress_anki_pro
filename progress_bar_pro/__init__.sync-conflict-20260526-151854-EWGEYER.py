from __future__ import annotations

from collections import deque
import csv
from datetime import date, datetime, timedelta
import html
import json
import math
import os
import re
import time
from typing import Any

from aqt import gui_hooks, mw
from aqt.qt import (
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QLinearGradient,
    QMenu,
    QPainter,
    QPen,
    QPixmap,
    QPointF,
    QPolygonF,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QColor,
    QColorDialog,
    Qt,
    qconnect,
)
from aqt.reviewer import Reviewer
from aqt.utils import showInfo


ADDON_NAME = "Progress Bar Pro"
DEFAULT_CONFIG = {
    "bar_color": "#2f80ed",
    "background_color": "#d9dde7",
    "bubble_color": "#ffffff",
    "bubble_text_color": "#000000",
    "bubble_text": "{left} left",
    "bubble_duration_ms": 1800,
    "show_answer_time": True,
    "show_answer_time_chart": True,
    "always_show_bubble": False,
    "show_estimated_time": True,
    "show_finish_time": True,
    "chart_good_gradient_top": "#a855f7",
    "chart_good_gradient_bottom": "#4c1d95",
    "chart_gradient_top": "#a855f7",
    "chart_gradient_bottom": "#0f172a",
    "chart_again_gradient_top": "#ef4444",
    "chart_again_gradient_bottom": "#7f1d1d",
    "database_location": "",
    "position": "bottom",
}
BUBBLE_TEXT_PRESETS = [
    ("Cards left", "{left} left"),
    ("Progress count", "{done}/{total} done"),
    ("Percent done", "{percent}% done"),
    ("Cards left + percent", "{left} left ({percent}%)"),
    ("Cards remaining", "Remaining: {left}"),
]
BACKUP_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "user_files", "settings_backup.json"
)
DEFAULT_TIMING_HISTORY_PATH = os.path.join(
    os.path.dirname(__file__), "user_files", "timing_history.json"
)
DEFAULT_DAILY_PROGRESS_PATH = os.path.join(
    os.path.dirname(__file__), "user_files", "daily_progress.json"
)
TIMING_HISTORY_DAYS = 15
DAILY_PROGRESS_DAYS = 31
MAX_HISTORY_RECORDS = 20000

_session_total = 0
_show_bubble_once = False
_question_started_at: float | None = None
_last_answer_seconds: float | None = None
_answer_seconds_ema: float | None = None
_answer_seconds_samples: deque[float] = deque(maxlen=30)
_answer_time_chart_samples: deque[dict[str, Any]] = deque(maxlen=5)
_history_loaded = False
_timing_history: list[dict[str, Any]] = []
_daily_progress_loaded = False
_daily_progress: dict[str, dict[str, int]] = {}


def _config() -> dict[str, Any]:
    config = mw.addonManager.getConfig(__name__) or {}
    merged = DEFAULT_CONFIG.copy()
    merged.update(config)
    merged.update(_backup_config())
    return merged


def _save_config(config: dict[str, Any]) -> None:
    merged = DEFAULT_CONFIG.copy()
    merged.update(config)
    mw.addonManager.writeConfig(__name__, merged)
    _write_backup_config(merged)


def _backup_config() -> dict[str, Any]:
    try:
        with open(BACKUP_CONFIG_PATH, encoding="utf-8") as backup_file:
            data = json.load(backup_file)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key in DEFAULT_CONFIG}


def _write_backup_config(config: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(BACKUP_CONFIG_PATH), exist_ok=True)
        with open(BACKUP_CONFIG_PATH, "w", encoding="utf-8") as backup_file:
            json.dump(config, backup_file, indent=2, sort_keys=True)
            backup_file.write("\n")
    except Exception:
        pass


def _database_location(config: dict[str, Any] | None = None) -> str:
    if config is None:
        config = _config()
    location = str(config.get("database_location") or "").strip()
    if not location:
        return ""
    return os.path.abspath(os.path.expanduser(location))


def _timing_history_path(config: dict[str, Any] | None = None) -> str:
    location = _database_location(config)
    if not location:
        return DEFAULT_TIMING_HISTORY_PATH
    return os.path.join(location, "timing_history.json")


def _daily_progress_path(config: dict[str, Any] | None = None) -> str:
    location = _database_location(config)
    if not location:
        return DEFAULT_DAILY_PROGRESS_PATH
    return os.path.join(location, "daily_progress.json")


def _merge_history_records(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    seen: set[tuple[float, int | None, float]] = set()
    merged: list[dict[str, Any]] = []
    for record in first + second:
        answered_at = record.get("answered_at")
        seconds = record.get("seconds")
        deck_id = record.get("deck_id")
        if not isinstance(answered_at, (int, float)) or not isinstance(
            seconds, (int, float)
        ):
            continue
        deck_key = int(deck_id) if isinstance(deck_id, int) else None
        normalized = {
            "answered_at": float(answered_at),
            "deck_id": deck_key,
            "seconds": min(600.0, max(0.2, float(seconds))),
        }
        key = (
            round(normalized["answered_at"], 3),
            normalized["deck_id"],
            round(normalized["seconds"], 3),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    merged.sort(key=lambda item: item["answered_at"])
    return merged[-MAX_HISTORY_RECORDS:]


def _read_history_file(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as history_file:
            return _normalized_history_records(json.load(history_file))
    except Exception:
        return []


def _migrate_timing_history(old_path: str, new_path: str) -> None:
    global _history_loaded, _timing_history
    if old_path == new_path:
        return
    old_records = _read_history_file(old_path)
    new_records = _read_history_file(new_path)
    current_records = _timing_history if _history_loaded else []
    merged = _merge_history_records(old_records, new_records)
    merged = _merge_history_records(merged, current_records)
    _history_loaded = True
    _timing_history = merged
    _write_timing_history()
    if old_path != DEFAULT_TIMING_HISTORY_PATH:
        return
    try:
        if os.path.exists(old_path):
            os.remove(old_path)
    except Exception:
        pass


def _valid_hex(value: Any, fallback: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value
    return fallback


def _lighter_hex(value: str, amount: float = 0.42) -> str:
    color = QColor(value)
    if not color.isValid():
        color = QColor(DEFAULT_CONFIG["bar_color"])
    r = int(color.red() + (255 - color.red()) * amount)
    g = int(color.green() + (255 - color.green()) * amount)
    b = int(color.blue() + (255 - color.blue()) * amount)
    return QColor(r, g, b).name()


def _remaining_for_current_card(reviewer: Reviewer) -> int:
    card = getattr(reviewer, "card", None)
    if not card or not mw.col:
        return 0

    try:
        counts = mw.col.sched.counts(card)
    except TypeError:
        counts = mw.col.sched.counts()
    except Exception:
        return 0

    try:
        return max(0, int(sum(counts)))
    except Exception:
        return 0


def _deck_id_for_current_card(reviewer: Reviewer) -> int | None:
    card = getattr(reviewer, "card", None)
    if not card:
        return None
    try:
        return int(card.did)
    except Exception:
        return None


def _deck_id_for_progress(reviewer: Reviewer) -> int | None:
    if mw.col:
        try:
            deck = mw.col.decks.current()
            deck_id = deck.get("id") if isinstance(deck, dict) else None
            if deck_id is not None:
                return int(deck_id)
        except Exception:
            pass
    return _deck_id_for_current_card(reviewer)


def _load_daily_progress() -> None:
    global _daily_progress_loaded, _daily_progress
    if _daily_progress_loaded:
        return
    _daily_progress_loaded = True
    try:
        with open(_daily_progress_path(), encoding="utf-8") as progress_file:
            data = json.load(progress_file)
    except Exception:
        _daily_progress = {}
        return
    if not isinstance(data, dict):
        _daily_progress = {}
        return

    normalized: dict[str, dict[str, int]] = {}
    for date_key, decks in data.items():
        if not isinstance(date_key, str) or not isinstance(decks, dict):
            continue
        normalized_decks: dict[str, int] = {}
        for deck_key, total in decks.items():
            if not isinstance(deck_key, str):
                continue
            try:
                total_int = int(total)
            except Exception:
                continue
            if total_int > 0:
                normalized_decks[deck_key] = total_int
        if normalized_decks:
            normalized[date_key] = normalized_decks
    _daily_progress = normalized
    _prune_daily_progress()


def _prune_daily_progress() -> None:
    cutoff = datetime.now().date() - timedelta(days=DAILY_PROGRESS_DAYS - 1)
    for date_key in list(_daily_progress):
        if not _date_key_is_recent(date_key, cutoff) or not _daily_progress[date_key]:
            del _daily_progress[date_key]


def _date_key_is_recent(date_key: str, cutoff: date) -> bool:
    try:
        return datetime.fromisoformat(date_key).date() >= cutoff
    except Exception:
        return False


def _write_daily_progress() -> None:
    try:
        path = _daily_progress_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as progress_file:
            json.dump(_daily_progress, progress_file, indent=2, sort_keys=True)
            progress_file.write("\n")
    except Exception:
        pass


def _daily_start_total(reviewer: Reviewer, left: int) -> int | None:
    _load_daily_progress()
    deck_id = _deck_id_for_progress(reviewer)
    if deck_id is None:
        return None

    today = datetime.now().date().isoformat()
    deck_key = str(deck_id)
    day_progress = _daily_progress.setdefault(today, {})
    stored_total = day_progress.get(deck_key)
    if isinstance(stored_total, int) and stored_total > 0:
        return stored_total
    if left <= 0:
        return None

    day_progress[deck_key] = left
    _prune_daily_progress()
    _write_daily_progress()
    return left


def _progress_payload(reviewer: Reviewer) -> dict[str, Any]:
    global _session_total, _show_bubble_once

    left = _remaining_for_current_card(reviewer)
    daily_total = _daily_start_total(reviewer, left)
    if daily_total is None and left > _session_total:
        _session_total = left
    total = max(daily_total or _session_total, 1)
    done = max(0, total - left)
    percent = 100 if left == 0 else min(100, max(0, round((done / total) * 100)))

    config = _config()
    bar_color = _valid_hex(config.get("bar_color"), DEFAULT_CONFIG["bar_color"])
    bg_color = _valid_hex(
        config.get("background_color"), DEFAULT_CONFIG["background_color"]
    )
    bubble_color = _valid_hex(
        config.get("bubble_color"), DEFAULT_CONFIG["bubble_color"]
    )
    bubble_text_color = _valid_hex(
        config.get("bubble_text_color"), DEFAULT_CONFIG["bubble_text_color"]
    )
    chart_top = _valid_hex(
        config.get("chart_gradient_top"), DEFAULT_CONFIG["chart_gradient_top"]
    )
    chart_bottom = _valid_hex(
        config.get("chart_gradient_bottom"),
        DEFAULT_CONFIG["chart_gradient_bottom"],
    )
    chart_good_top = _valid_hex(
        config.get("chart_good_gradient_top"),
        DEFAULT_CONFIG["chart_good_gradient_top"],
    )
    chart_good_bottom = _valid_hex(
        config.get("chart_good_gradient_bottom"),
        DEFAULT_CONFIG["chart_good_gradient_bottom"],
    )
    chart_again_top = _valid_hex(
        config.get("chart_again_gradient_top"),
        DEFAULT_CONFIG["chart_again_gradient_top"],
    )
    chart_again_bottom = _valid_hex(
        config.get("chart_again_gradient_bottom"),
        DEFAULT_CONFIG["chart_again_gradient_bottom"],
    )
    template = str(config.get("bubble_text") or DEFAULT_CONFIG["bubble_text"])
    text = template.format(left=left, done=done, total=total, percent=percent)
    if config.get("show_answer_time", True) and _last_answer_seconds is not None:
        text = f"{text} | {_format_seconds(_last_answer_seconds)}"
    detail_text = ""
    if config.get("show_estimated_time", True):
        estimate_seconds = _estimate_remaining_seconds(
            left, _deck_id_for_current_card(reviewer)
        )
        if estimate_seconds is not None:
            detail_text = f"Est. left {_format_duration(estimate_seconds)}"
            if config.get("show_finish_time", True):
                detail_text = (
                    f"{detail_text} ({_format_finish_time(estimate_seconds)})"
                )
    position = "top" if config.get("position") == "top" else "bottom"
    try:
        duration_ms = int(config.get("bubble_duration_ms", 1800))
    except Exception:
        duration_ms = int(DEFAULT_CONFIG["bubble_duration_ms"])
    duration_ms = min(10000, max(250, duration_ms))
    show_bubble = _show_bubble_once and done > 0
    _show_bubble_once = False

    return {
        "left": left,
        "done": done,
        "total": total,
        "percent": percent,
        "text": text,
        "detailText": detail_text,
        "barColor": bar_color,
        "barColorLight": _lighter_hex(bar_color),
        "backgroundColor": bg_color,
        "bubbleColor": bubble_color,
        "bubbleTextColor": bubble_text_color,
        "answerTimeChart": list(_answer_time_chart_samples),
        "chartGradientTop": chart_top,
        "chartGradientBottom": chart_bottom,
        "chartGoodGradientTop": chart_good_top,
        "chartGoodGradientBottom": chart_good_bottom,
        "chartAgainGradientTop": chart_again_top,
        "chartAgainGradientBottom": chart_again_bottom,
        "position": position,
        "bubbleDurationMs": duration_ms,
        "showBubble": show_bubble,
        "showAnswerTimeChart": bool(config.get("show_answer_time_chart", True)),
        "alwaysShowBubble": bool(config.get("always_show_bubble", False)),
    }


def _format_seconds(seconds: float) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remaining = int(seconds % 60)
    return f"{minutes}m {remaining:02d}s"


def _format_duration(seconds: float) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return _format_seconds(seconds)
    if seconds < 3600:
        minutes = int(seconds // 60)
        remaining = int(seconds % 60)
        return f"{minutes}m {remaining:02d}s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes:02d}m"


def _format_finish_time(seconds: float) -> str:
    finish = datetime.now() + timedelta(seconds=max(0, seconds))
    return finish.strftime("%I:%M %p").lstrip("0").lower()


def _estimate_remaining_seconds(left: int, deck_id: int | None) -> float | None:
    if left <= 0:
        return None

    session_estimate = _session_seconds_per_card()
    deck_recent_estimate = _history_seconds_per_card(deck_id, recent_only=True)
    global_recent_estimate = _history_seconds_per_card(None, recent_only=True)
    deck_all_time_estimate = _history_seconds_per_card(deck_id, recent_only=False)
    global_all_time_estimate = _history_seconds_per_card(None, recent_only=False)

    weighted_estimates: list[tuple[float, float]] = []
    if session_estimate is not None:
        session_weight = min(0.72, 0.24 + (len(_answer_seconds_samples) * 0.08))
        weighted_estimates.append((session_estimate, session_weight))
    if deck_recent_estimate is not None:
        weighted_estimates.append((deck_recent_estimate, 0.52))
    if global_recent_estimate is not None:
        weighted_estimates.append((global_recent_estimate, 0.26))
    if deck_all_time_estimate is not None:
        weighted_estimates.append((deck_all_time_estimate, 0.28))
    if global_all_time_estimate is not None:
        weighted_estimates.append((global_all_time_estimate, 0.12))
    if not weighted_estimates:
        return None

    total_weight = sum(weight for _, weight in weighted_estimates)
    seconds_per_card = (
        sum(value * weight for value, weight in weighted_estimates) / total_weight
    )

    return left * max(0.5, seconds_per_card)


def _session_seconds_per_card() -> float | None:
    if not _answer_seconds_samples:
        return None

    samples = sorted(_answer_seconds_samples)
    median = samples[len(samples) // 2]
    session_mean = sum(samples) / len(samples)
    ema = _answer_seconds_ema if _answer_seconds_ema is not None else median

    if len(samples) < 4:
        return (ema * 0.7) + (session_mean * 0.3)
    return (ema * 0.55) + (median * 0.3) + (session_mean * 0.15)


def _history_seconds_per_card(deck_id: int | None, recent_only: bool) -> float | None:
    _load_timing_history()
    records = _timing_history
    if recent_only:
        cutoff = time.time() - (TIMING_HISTORY_DAYS * 86400)
        records = [
            record
            for record in records
            if isinstance(record.get("answered_at"), (int, float))
            and record["answered_at"] >= cutoff
        ]
    if deck_id is not None:
        deck_records = [record for record in records if record.get("deck_id") == deck_id]
        if len(deck_records) >= 3:
            records = deck_records
    if not records:
        return None

    now = time.time()
    weighted: list[tuple[float, float]] = []
    for record in records:
        seconds = record.get("seconds")
        answered_at = record.get("answered_at")
        if not isinstance(seconds, (int, float)) or not isinstance(
            answered_at, (int, float)
        ):
            continue
        age_days = max(0.0, (now - answered_at) / 86400)
        if recent_only:
            recency_weight = max(0.2, 1 - (age_days / TIMING_HISTORY_DAYS))
        else:
            recency_weight = max(0.15, 1 / (1 + (age_days / 30)))
        weighted.append((float(seconds), recency_weight))
    if not weighted:
        return None

    weighted.sort(key=lambda item: item[0])
    trim_count = max(0, int(len(weighted) * 0.1))
    if trim_count and len(weighted) > trim_count * 2:
        weighted = weighted[trim_count:-trim_count]

    total_weight = sum(weight for _, weight in weighted)
    return sum(seconds * weight for seconds, weight in weighted) / total_weight


def _record_answer_seconds(seconds: float, ease: int) -> None:
    global _answer_seconds_ema

    seconds = min(600.0, max(0.2, seconds))
    _answer_seconds_samples.append(seconds)
    _answer_time_chart_samples.append(
        {
            "seconds": round(seconds, 3),
            "again": ease == 1,
        }
    )
    if _answer_seconds_ema is None:
        _answer_seconds_ema = seconds
    else:
        alpha = 0.28
        _answer_seconds_ema = (alpha * seconds) + ((1 - alpha) * _answer_seconds_ema)


def _record_answer_history(reviewer: Reviewer, seconds: float) -> None:
    _load_timing_history()
    deck_id = _deck_id_for_current_card(reviewer)
    _timing_history.append(
        {
            "answered_at": time.time(),
            "deck_id": deck_id,
            "seconds": min(600.0, max(0.2, seconds)),
        }
    )
    _prune_timing_history()
    _write_timing_history()


def _load_timing_history() -> None:
    global _history_loaded, _timing_history
    if _history_loaded:
        return
    _history_loaded = True
    try:
        with open(_timing_history_path(), encoding="utf-8") as history_file:
            data = json.load(history_file)
    except Exception:
        _timing_history = []
        return
    if not isinstance(data, list):
        _timing_history = []
        return
    _timing_history = [record for record in data if isinstance(record, dict)]
    _prune_timing_history()


def _prune_timing_history() -> None:
    kept = [
        record
        for record in _timing_history
        if isinstance(record.get("answered_at"), (int, float))
        and isinstance(record.get("seconds"), (int, float))
    ]
    kept.sort(key=lambda record: record["answered_at"])
    del kept[:-MAX_HISTORY_RECORDS]
    _timing_history[:] = kept


def _write_timing_history() -> None:
    path = _timing_history_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as history_file:
            json.dump(_timing_history, history_file, separators=(",", ":"))
            history_file.write("\n")
    except Exception:
        pass


def _deck_name(deck_id: int | None) -> str:
    if deck_id is None or mw.col is None:
        return "Unknown"
    try:
        name = mw.col.decks.name(deck_id)
    except Exception:
        name = None
    return str(name or f"Deck {deck_id}")


def _history_records_for_display() -> list[dict[str, Any]]:
    _load_timing_history()
    _prune_timing_history()
    cutoff = time.time() - (TIMING_HISTORY_DAYS * 86400)
    return [
        record
        for record in _timing_history
        if isinstance(record.get("answered_at"), (int, float))
        and record["answered_at"] >= cutoff
    ]


def _history_records_all_time() -> list[dict[str, Any]]:
    _load_timing_history()
    _prune_timing_history()
    return list(_timing_history)


def _normalized_history_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        data = data["records"]
    if not isinstance(data, list):
        raise ValueError("Timing history JSON must be a list of records.")

    records: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        answered_at = item.get("answered_at")
        seconds = item.get("seconds")
        deck_id = item.get("deck_id")
        if not isinstance(answered_at, (int, float)) or not isinstance(
            seconds, (int, float)
        ):
            continue
        if deck_id is not None:
            try:
                deck_id = int(deck_id)
            except Exception:
                deck_id = None
        records.append(
            {
                "answered_at": float(answered_at),
                "deck_id": deck_id,
                "seconds": min(600.0, max(0.2, float(seconds))),
            }
        )
    records.sort(key=lambda record: record["answered_at"])
    return records[-MAX_HISTORY_RECORDS:]


def _timing_history_export_payload() -> dict[str, Any]:
    return {
        "name": ADDON_NAME,
        "version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "records": _history_records_all_time(),
    }


def _replace_timing_history(records: list[dict[str, Any]]) -> None:
    global _history_loaded, _timing_history
    _history_loaded = True
    _timing_history = records
    _prune_timing_history()
    _write_timing_history()


def _all_time_summary_text() -> str:
    records = _history_records_all_time()
    if not records:
        return "All-time estimate: no timing history yet."
    estimate = _history_seconds_per_card(None, recent_only=False)
    deck_ids = {record.get("deck_id") for record in records}
    first = datetime.fromtimestamp(records[0]["answered_at"]).date().isoformat()
    last = datetime.fromtimestamp(records[-1]["answered_at"]).date().isoformat()
    estimate_text = _format_seconds(estimate) if estimate is not None else "-"
    return (
        f"All-time estimate: {estimate_text} per card from {len(records)} records "
        f"across {len(deck_ids - {None})} decks ({first} to {last})."
    )


def _best_recent_day_text(summary: list[dict[str, Any]]) -> str:
    active_days = [day for day in summary if int(day["cards"]) > 0]
    if not active_days:
        return "Best day: no answered cards in the last 15 days yet."

    best = min(
        active_days,
        key=lambda day: (float(day["avg_seconds"]), -int(day["cards"])),
    )
    label_date = datetime.fromisoformat(str(best["date"]))
    return (
        f"Best day: {label_date:%b} {label_date.day} with "
        f"{_format_seconds(float(best['avg_seconds']))} avg answer time "
        f"across {int(best['cards'])} cards."
    )


def _daily_history_summary() -> list[dict[str, Any]]:
    records = _history_records_for_display()
    today = datetime.now().date()
    days = [today - timedelta(days=offset) for offset in range(TIMING_HISTORY_DAYS - 1, -1, -1)]
    by_day: dict[str, list[dict[str, Any]]] = {day.isoformat(): [] for day in days}

    for record in records:
        answered_at = record.get("answered_at")
        if not isinstance(answered_at, (int, float)):
            continue
        day = datetime.fromtimestamp(answered_at).date().isoformat()
        if day in by_day:
            by_day[day].append(record)

    summary = []
    for day in days:
        key = day.isoformat()
        day_records = by_day[key]
        seconds_values = [
            float(record["seconds"])
            for record in day_records
            if isinstance(record.get("seconds"), (int, float))
        ]
        total_seconds = sum(seconds_values)
        deck_ids = {record.get("deck_id") for record in day_records}
        summary.append(
            {
                "date": key,
                "cards": len(seconds_values),
                "avg_seconds": (total_seconds / len(seconds_values))
                if seconds_values
                else 0,
                "total_seconds": total_seconds,
                "decks": len(deck_ids - {None}),
            }
        )
    return summary


def _estimate_graph_pixmap(summary: list[dict[str, Any]]) -> QPixmap:
    config = _config()
    top_color = QColor(
        _valid_hex(config.get("chart_gradient_top"), DEFAULT_CONFIG["chart_gradient_top"])
    )
    bottom_color = QColor(
        _valid_hex(
            config.get("chart_gradient_bottom"),
            DEFAULT_CONFIG["chart_gradient_bottom"],
        )
    )

    width = 1040
    height = 280
    left = 54
    right = 24
    top = 28
    bottom = 44
    graph_width = width - left - right
    graph_height = height - top - bottom
    baseline = top + graph_height

    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#15171c"))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillRect(0, 0, width, height, QColor("#15171c"))

    painter.setPen(QPen(QColor("#eef2ff"), 1))
    painter.drawText(18, 22, "Last 15 days - smoothed density")

    grid_pen = QPen(QColor("#30343d"), 1)
    painter.setPen(grid_pen)
    for index in range(5):
        y = top + round((graph_height / 4) * index)
        painter.drawLine(left, y, width - right, y)

    cards_values = [int(day["cards"]) for day in summary]
    max_cards = max(cards_values) if cards_values else 0
    count = max(len(summary), 1)

    bandwidth = 1.15
    sample_count = 180
    density_values: list[float] = []
    if max_cards == 0:
        density_values = [0.0 for _ in range(sample_count)]
    else:
        for sample_index in range(sample_count):
            position = (
                (sample_index / (sample_count - 1)) * (count - 1)
                if sample_count > 1
                else 0
            )
            density = 0.0
            for day_index, cards in enumerate(cards_values):
                if cards <= 0:
                    continue
                distance = (position - day_index) / bandwidth
                density += cards * math.exp(-0.5 * distance * distance)
            density_values.append(density)

    density_max = max(density_values) if density_values else 0
    scale_max = max(density_max, 1)
    points: list[QPointF] = []
    for sample_index, density in enumerate(density_values):
        x = left + ((sample_index / (sample_count - 1)) * graph_width)
        y = baseline - ((density / scale_max) * graph_height)
        if max_cards == 0:
            y = baseline - 6
        points.append(QPointF(x, y))

    area = QPolygonF()
    area.append(QPointF(left, baseline))
    for point in points:
        area.append(point)
    area.append(QPointF(width - right, baseline))

    gradient = QLinearGradient(0, top, 0, baseline)
    gradient.setColorAt(0, top_color)
    gradient.setColorAt(1, bottom_color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawPolygon(area)

    line_pen = QPen(top_color.lighter(125), 3)
    painter.setPen(line_pen)
    for index in range(1, len(points)):
        painter.drawLine(points[index - 1], points[index])

    painter.setPen(QPen(QColor("#f8fafc"), 1))
    x_step = graph_width / (count - 1) if count > 1 else 0
    for index, cards in enumerate(cards_values):
        x = left + (x_step * index)
        y = baseline - ((cards / max(max_cards, 1)) * graph_height)
        if max_cards == 0:
            y = baseline - 6
        painter.drawEllipse(QPointF(x, y), 3, 3)
        if cards:
            painter.drawText(round(x) - 8, round(y) - 10, str(cards))

    label_pen = QPen(QColor("#cbd5e1"), 1)
    painter.setPen(label_pen)
    for index, day in enumerate(summary):
        if index % 2 and len(summary) > 10:
            continue
        label_date = datetime.fromisoformat(str(day["date"]))
        label = f"{label_date:%b} {label_date.day}"
        x = left + (x_step * index)
        painter.drawText(round(x) - 18, height - 18, label)

    painter.setPen(QPen(QColor("#94a3b8"), 1))
    painter.drawText(18, baseline + 4, "0")
    painter.drawText(12, top + 4, str(max_cards))
    painter.end()
    return pixmap


def _history_csv_rows() -> list[list[str]]:
    rows = [["answered_at", "date", "deck_id", "deck_name", "seconds"]]
    for record in _history_records_all_time():
        answered_at = record.get("answered_at")
        seconds = record.get("seconds")
        deck_id = record.get("deck_id")
        if not isinstance(answered_at, (int, float)) or not isinstance(
            seconds, (int, float)
        ):
            continue
        answered_dt = datetime.fromtimestamp(answered_at)
        deck_id_int = int(deck_id) if isinstance(deck_id, int) else None
        rows.append(
            [
                answered_dt.isoformat(timespec="seconds"),
                answered_dt.date().isoformat(),
                "" if deck_id_int is None else str(deck_id_int),
                _deck_name(deck_id_int),
                f"{float(seconds):.3f}",
            ]
        )
    return rows


def _inject_progress_bar(html_text: str, card: Any, context: str) -> str:
    if context not in ("reviewQuestion", "reviewAnswer"):
        return html_text
    reviewer = getattr(mw, "reviewer", None)
    if not reviewer:
        return html_text

    payload = _progress_payload(reviewer)
    payload_json = json.dumps(payload)
    script = f"""
<script>
(function() {{
  const payload = {payload_json};
  const existing = document.getElementById("progress-bar-pro");
  if (existing) {{
    existing.remove();
  }}

  const styleId = "progress-bar-pro-animation-style";
  let style = document.getElementById(styleId);
  if (!style) {{
    style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      @keyframes progressBarProBubble {{
        0% {{
          opacity: 0;
          transform: translate(-50%, 42%) scale(0.96);
        }}
        13% {{
          opacity: 1;
          transform: translate(-50%, -62%) scale(1.035);
        }}
        22% {{
          opacity: 1;
          transform: translate(-50%, -46%) scale(0.99);
        }}
        30% {{
          opacity: 1;
          transform: translate(-50%, -50%) scale(1);
        }}
        78% {{
          opacity: 1;
          transform: translate(-50%, -50%) scale(1);
        }}
        88% {{
          opacity: 1;
          transform: translate(-50%, -76%) scale(1.018);
        }}
        100% {{
          opacity: 0;
          transform: translate(-50%, -126%) scale(0.97);
        }}
      }}
      @keyframes progressBarProChart {{
        0% {{
          opacity: 0;
          transform: translateY(42%) scale(0.96);
        }}
        13% {{
          opacity: 1;
          transform: translateY(-62%) scale(1.035);
        }}
        22% {{
          opacity: 1;
          transform: translateY(-46%) scale(0.99);
        }}
        30% {{
          opacity: 1;
          transform: translateY(-50%) scale(1);
        }}
        78% {{
          opacity: 1;
          transform: translateY(-50%) scale(1);
        }}
        88% {{
          opacity: 1;
          transform: translateY(-76%) scale(1.018);
        }}
        100% {{
          opacity: 0;
          transform: translateY(-126%) scale(0.97);
        }}
      }}
      @keyframes progressBarProBubbleBottom {{
        0% {{
          opacity: 0;
          transform: translate(-50%, -40%) scale(0.96);
        }}
        13% {{
          opacity: 1;
          transform: translate(-50%, -125%) scale(1.035);
        }}
        22% {{
          opacity: 1;
          transform: translate(-50%, -108%) scale(0.99);
        }}
        30% {{
          opacity: 1;
          transform: translate(-50%, -112%) scale(1);
        }}
        78% {{
          opacity: 1;
          transform: translate(-50%, -112%) scale(1);
        }}
        88% {{
          opacity: 1;
          transform: translate(-50%, -132%) scale(1.018);
        }}
        100% {{
          opacity: 0;
          transform: translate(-50%, -165%) scale(0.97);
        }}
      }}
      @keyframes progressBarProChartBottom {{
        0% {{
          opacity: 0;
          transform: translateY(-40%) scale(0.96);
        }}
        13% {{
          opacity: 1;
          transform: translateY(-125%) scale(1.035);
        }}
        22% {{
          opacity: 1;
          transform: translateY(-108%) scale(0.99);
        }}
        30% {{
          opacity: 1;
          transform: translateY(-112%) scale(1);
        }}
        78% {{
          opacity: 1;
          transform: translateY(-112%) scale(1);
        }}
        88% {{
          opacity: 1;
          transform: translateY(-132%) scale(1.018);
        }}
        100% {{
          opacity: 0;
          transform: translateY(-165%) scale(0.97);
        }}
      }}
      @keyframes progressBarProBubbleTop {{
        0% {{
          opacity: 0;
          transform: translate(-50%, -20%) scale(0.96);
        }}
        13% {{
          opacity: 1;
          transform: translate(-50%, 58%) scale(1.035);
        }}
        22% {{
          opacity: 1;
          transform: translate(-50%, 38%) scale(0.99);
        }}
        30% {{
          opacity: 1;
          transform: translate(-50%, 42%) scale(1);
        }}
        78% {{
          opacity: 1;
          transform: translate(-50%, 42%) scale(1);
        }}
        88% {{
          opacity: 1;
          transform: translate(-50%, 62%) scale(1.018);
        }}
        100% {{
          opacity: 0;
          transform: translate(-50%, 96%) scale(0.97);
        }}
      }}
      @keyframes progressBarProChartTop {{
        0% {{
          opacity: 0;
          transform: translateY(-20%) scale(0.96);
        }}
        13% {{
          opacity: 1;
          transform: translateY(58%) scale(1.035);
        }}
        22% {{
          opacity: 1;
          transform: translateY(38%) scale(0.99);
        }}
        30% {{
          opacity: 1;
          transform: translateY(42%) scale(1);
        }}
        78% {{
          opacity: 1;
          transform: translateY(42%) scale(1);
        }}
        88% {{
          opacity: 1;
          transform: translateY(62%) scale(1.018);
        }}
        100% {{
          opacity: 0;
          transform: translateY(96%) scale(0.97);
        }}
      }}
      @keyframes progressBarProChartFadeIn {{
        0% {{
          opacity: 0;
          transform: translateY(42%) scale(0.98);
        }}
        100% {{
          opacity: 1;
          transform: translateY(42%) scale(1);
        }}
      }}
      @keyframes progressBarProChartFadeInBottom {{
        0% {{
          opacity: 0;
          transform: translateY(-112%) scale(0.98);
        }}
        100% {{
          opacity: 1;
          transform: translateY(-112%) scale(1);
        }}
      }}
    `;
    document.head.appendChild(style);
  }}

  const applyTopSpacing = () => {{
    if (!document.body) {{
      return;
    }}
    if (!document.body.hasAttribute("data-progress-bar-pro-original-padding-top")) {{
      document.body.dataset.progressBarProOriginalPaddingTop = document.body.style.paddingTop || "";
    }}
    const originalPadding = document.body.dataset.progressBarProOriginalPaddingTop;
    if (payload.position === "top") {{
      const reservedTopSpace = payload.detailText || payload.alwaysShowBubble ? 68 : 54;
      document.body.style.paddingTop = originalPadding
        ? "calc(" + originalPadding + " + " + reservedTopSpace + "px)"
        : reservedTopSpace + "px";
      document.body.style.boxSizing = "border-box";
    }} else {{
      document.body.style.paddingTop = originalPadding;
    }}
  }};
  applyTopSpacing();

  const root = document.createElement("div");
  root.id = "progress-bar-pro";
  root.setAttribute("aria-hidden", "true");
  const vertical = payload.position === "top" ? "top: 12px;" : "bottom: 78px;";
  root.style.cssText = [
    "position: fixed",
    "left: max(16px, env(safe-area-inset-left))",
    "right: max(16px, env(safe-area-inset-right))",
    vertical,
    "height: 18px",
    "z-index: 2147483647",
    "pointer-events: none",
    "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
  ].join("; ");

  const track = document.createElement("div");
  track.style.cssText = [
    "position: relative",
    "width: 100%",
    "height: 100%",
    "overflow: visible",
    "border-radius: 999px",
    "background: " + payload.backgroundColor,
    "box-shadow: 0 1px 3px rgba(0,0,0,0.18)"
  ].join("; ");

  const fill = document.createElement("div");
  fill.style.cssText = [
    "height: 100%",
    "width: " + payload.percent + "%",
    "min-width: " + (payload.percent > 0 ? "10px" : "0"),
    "border-radius: 999px",
    "background: linear-gradient(90deg, " + payload.barColorLight + ", " + payload.barColor + ")",
    "transition: width 220ms ease"
  ].join("; ");

  const bubble = document.createElement("div");
  bubble.style.cssText = [
    "position: absolute",
    "left: 50%",
    "top: 50%",
    "transform: translate(-50%, 42%) scale(0.96)",
    "min-width: 72px",
    "max-width: min(70vw, 360px)",
    "min-height: " + (payload.detailText ? "42px" : "28px"),
    "padding: " + (payload.detailText ? "5px 13px" : "0 12px"),
    "box-sizing: border-box",
    "display: flex",
    "flex-direction: column",
    "align-items: center",
    "justify-content: center",
    "border-radius: 999px",
    "background: " + payload.bubbleColor,
    "color: " + payload.bubbleTextColor,
    "overflow: hidden",
    "box-shadow: 0 8px 18px rgba(0,0,0,0.24), 0 2px 5px rgba(0,0,0,0.18)",
    "opacity: 0",
    "transform-origin: 50% 50%",
    "will-change: opacity, transform"
  ].join("; ");

  const primary = document.createElement("div");
  primary.textContent = payload.text;
  primary.style.cssText = [
    "max-width: 100%",
    "font-size: 13px",
    "font-weight: 700",
    "line-height: 1.05",
    "white-space: nowrap",
    "overflow: hidden",
    "text-overflow: ellipsis"
  ].join("; ");
  bubble.appendChild(primary);

  if (payload.detailText) {{
    const secondary = document.createElement("div");
    secondary.textContent = payload.detailText;
    secondary.style.cssText = [
      "max-width: 100%",
      "margin-top: 3px",
      "font-size: 11px",
      "font-weight: 600",
      "line-height: 1",
      "color: " + payload.bubbleTextColor,
      "white-space: nowrap",
      "overflow: hidden",
      "text-overflow: ellipsis"
    ].join("; ");
    bubble.appendChild(secondary);
  }}

  track.appendChild(fill);
  track.appendChild(bubble);

  const shouldShowBubble = payload.showBubble || payload.alwaysShowBubble;
  const shouldShowChart = shouldShowBubble
    && payload.showAnswerTimeChart
    && payload.answerTimeChart.length;
  const bottomMode = payload.position !== "top";
  const bubbleVisibleTransform = bottomMode
    ? "translate(-50%, -112%) scale(1)"
    : "translate(-50%, 42%) scale(1)";
  const chartVisibleTransform = bottomMode
    ? "translateY(-112%) scale(1)"
    : "translateY(42%) scale(1)";
  const bubbleAnimation = bottomMode
    ? "progressBarProBubbleBottom"
    : "progressBarProBubbleTop";
  const chartAnimation = bottomMode
    ? "progressBarProChartBottom"
    : "progressBarProChartTop";
  const chartFadeInAnimation = bottomMode
    ? "progressBarProChartFadeInBottom"
    : "progressBarProChartFadeIn";

  if (shouldShowChart) {{
    const chart = document.createElement("div");
    chart.style.cssText = [
      "position: absolute",
      "top: 50%",
      "width: 86px",
      "height: 28px",
      "border-radius: 999px",
      "background: #000000",
      "box-shadow: 0 8px 18px rgba(0,0,0,0.24), 0 2px 5px rgba(0,0,0,0.18)",
      "overflow: hidden",
      "opacity: 0",
      "transform: translateY(-50%) scale(0.96)",
      "transform-origin: 0 50%",
      "will-change: opacity, transform"
    ].join("; ");

    const baseCanvas = document.createElement("canvas");
    const againCanvas = document.createElement("canvas");
    [baseCanvas, againCanvas].forEach((canvas) => {{
      canvas.width = 180;
      canvas.height = 84;
      canvas.style.cssText = [
        "position: absolute",
        "inset: 0",
        "width: 100%",
        "height: 100%",
        "-webkit-mask-image: linear-gradient(90deg, transparent 0%, #000 18%, #000 82%, transparent 100%)",
        "mask-image: linear-gradient(90deg, transparent 0%, #000 18%, #000 82%, transparent 100%)"
      ].join("; ");
      chart.appendChild(canvas);
    }});
    againCanvas.style.opacity = "0";
    againCanvas.style.transition = "opacity 170ms ease-out";
    track.appendChild(chart);

    const values = payload.answerTimeChart.map((item) =>
      Math.max(0, Number(item.seconds) || 0)
    );
    const latestPoint = payload.answerTimeChart[payload.answerTimeChart.length - 1];
    const latestIsAgain = Boolean(latestPoint && latestPoint.again);
    const hasAgainPoint = payload.answerTimeChart.some((item) => item.again);

    const chartGeometry = (canvas) => {{
      const width = canvas.width;
      const height = canvas.height;
      const maxValue = Math.max(1, ...values);
      const leftPad = 12;
      const rightPad = 12;
      const topPad = 10;
      const bottomPad = 13;
      const plotWidth = width - leftPad - rightPad;
      const plotHeight = height - topPad - bottomPad;
      const points = values.map((value, index) => {{
        const denominator = Math.max(1, values.length - 1);
        return {{
          x: leftPad + ((plotWidth * index) / denominator),
          y: topPad + plotHeight - ((value / maxValue) * plotHeight)
        }};
      }});
      const baseline = height - bottomPad;
      const area = new Path2D();
      area.moveTo(points[0].x, baseline);
      points.forEach((point) => area.lineTo(point.x, point.y));
      area.lineTo(points[points.length - 1].x, baseline);
      area.closePath();
      return {{
        area,
        baseline,
        bottomPad,
        height,
        plotWidth,
        points,
        topPad,
        width
      }};
    }};

    const drawChart = (canvas, topColor, bottomColor) => {{
      const ctx = canvas.getContext("2d");
      const geometry = chartGeometry(canvas);
      ctx.clearRect(0, 0, geometry.width, geometry.height);
      ctx.lineJoin = "round";
      ctx.lineCap = "round";

      const gradient = ctx.createLinearGradient(
        0,
        geometry.topPad,
        0,
        geometry.baseline
      );
      gradient.addColorStop(0, topColor);
      gradient.addColorStop(1, bottomColor);
      ctx.fillStyle = gradient;
      ctx.globalAlpha = 0.9;
      ctx.fill(geometry.area);
      ctx.globalAlpha = 1;

      const line = new Path2D();
      geometry.points.forEach((point, index) => {{
        if (index === 0) {{
          line.moveTo(point.x, point.y);
        }} else {{
          line.lineTo(point.x, point.y);
        }}
      }});
      ctx.strokeStyle = topColor;
      ctx.lineWidth = 4;
      ctx.stroke(line);
    }};

    const drawAgainOverlay = (canvas) => {{
      const ctx = canvas.getContext("2d");
      const geometry = chartGeometry(canvas);
      ctx.clearRect(0, 0, geometry.width, geometry.height);
      if (!hasAgainPoint) {{
        return;
      }}

      const pointSpacing = geometry.points.length > 1
        ? geometry.plotWidth / (geometry.points.length - 1)
        : geometry.plotWidth;
      const halfWidth = Math.max(18, pointSpacing * 0.58);
      const verticalGradient = ctx.createLinearGradient(
        0,
        geometry.topPad,
        0,
        geometry.baseline
      );
      verticalGradient.addColorStop(0, payload.chartAgainGradientTop);
      verticalGradient.addColorStop(1, payload.chartAgainGradientBottom);

      ctx.save();
      ctx.clip(geometry.area);
      payload.answerTimeChart.forEach((item, index) => {{
        if (!item.again) {{
          return;
        }}
        const centerX = geometry.points[index].x;
        for (let step = 6; step >= 1; step -= 1) {{
          const width = (halfWidth * step) / 6;
          ctx.globalAlpha = 0.04 + ((7 - step) * 0.035);
          ctx.fillStyle = verticalGradient;
          ctx.fillRect(
            centerX - width,
            geometry.topPad,
            width * 2,
            geometry.baseline - geometry.topPad
          );
        }}
      }});
      ctx.restore();

      ctx.globalAlpha = 0.82;
      ctx.strokeStyle = payload.chartAgainGradientTop;
      ctx.lineWidth = 4;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      payload.answerTimeChart.forEach((item, index) => {{
        if (!item.again) {{
          return;
        }}
        const point = geometry.points[index];
        const before = geometry.points[Math.max(0, index - 1)];
        const after = geometry.points[Math.min(geometry.points.length - 1, index + 1)];
        const segment = new Path2D();
        segment.moveTo((before.x + point.x) / 2, (before.y + point.y) / 2);
        segment.lineTo(point.x, point.y);
        segment.lineTo((after.x + point.x) / 2, (after.y + point.y) / 2);
        ctx.stroke(segment);
      }});
      ctx.globalAlpha = 1;
    }};

    const drawAllCharts = () => {{
      drawChart(
        baseCanvas,
        payload.chartGoodGradientTop,
        payload.chartGoodGradientBottom
      );
      drawAgainOverlay(againCanvas);
      againCanvas.style.opacity = hasAgainPoint && !latestIsAgain ? "1" : "0";
    }};

    const positionChart = () => {{
      const bubbleRect = bubble.getBoundingClientRect();
      const trackRect = track.getBoundingClientRect();
      const gap = 8;
      const chartHeight = Math.max(28, Math.round(bubbleRect.height));
      const chartWidth = chart.getBoundingClientRect().width || 86;
      const preferredLeft = Math.round((bubbleRect.right - trackRect.left) + gap);
      const maxLeft = Math.max(gap, Math.round(trackRect.width - chartWidth));
      chart.style.height = chartHeight + "px";
      chart.style.left = Math.min(preferredLeft, maxLeft) + "px";
      if (payload.alwaysShowBubble) {{
        const seenChartKey = "progressBarProSawAnswerTimeChart";
        if (sessionStorage.getItem(seenChartKey)) {{
          chart.style.opacity = "1";
          chart.style.transform = chartVisibleTransform;
        }} else {{
          sessionStorage.setItem(seenChartKey, "1");
          chart.style.animation = chartFadeInAnimation + " 180ms ease-out both";
        }}
      }} else {{
        chart.style.animation = chartAnimation + " "
          + Math.max(650, payload.bubbleDurationMs)
          + "ms cubic-bezier(0.22, 1, 0.36, 1) both";
      }}
      if (latestIsAgain) {{
        requestAnimationFrame(() => {{
          againCanvas.style.opacity = "1";
        }});
      }}
    }};

    drawAllCharts();
    requestAnimationFrame(positionChart);
  }}
  root.appendChild(track);
  document.body.appendChild(root);

  if (payload.alwaysShowBubble) {{
    bubble.style.opacity = "1";
    bubble.style.transform = bubbleVisibleTransform;
  }} else if (payload.showBubble) {{
    bubble.style.animation = bubbleAnimation + " " + Math.max(650, payload.bubbleDurationMs) + "ms cubic-bezier(0.22, 1, 0.36, 1) both";
  }}
}})();
</script>
"""
    if context == "reviewQuestion":
        _start_question_timer()
    return html_text + script


class _ColorButton(QPushButton):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self._value = _valid_hex(value, "#000000")
        self._refresh()
        qconnect(self.clicked, self._choose)

    def value(self) -> str:
        return self._value

    def _choose(self) -> None:
        color = QColorDialog.getColor(QColor(self._value), self.window())
        if color.isValid():
            self._value = color.name()
            self._refresh()

    def _refresh(self) -> None:
        self.setText(self._value)
        self.setStyleSheet(
            f"QPushButton {{ background: {self._value}; color: {_readable_text(self._value)}; }}"
        )


def _readable_text(background: str) -> str:
    color = QColor(background)
    brightness = (color.red() * 299 + color.green() * 587 + color.blue() * 114) / 1000
    return "#000000" if brightness > 150 else "#ffffff"


class OptionsDialog(QDialog):
    def __init__(self) -> None:
        super().__init__(mw)
        self.setWindowTitle(ADDON_NAME)
        self.setMinimumWidth(540)

        config = _config()
        self.bar_color = _ColorButton(
            _valid_hex(config.get("bar_color"), DEFAULT_CONFIG["bar_color"])
        )
        self.background_color = _ColorButton(
            _valid_hex(
                config.get("background_color"), DEFAULT_CONFIG["background_color"]
            )
        )
        self.bubble_color = _ColorButton(
            _valid_hex(config.get("bubble_color"), DEFAULT_CONFIG["bubble_color"])
        )
        self.bubble_text_color = _ColorButton(
            _valid_hex(
                config.get("bubble_text_color"),
                DEFAULT_CONFIG["bubble_text_color"],
            )
        )
        self.chart_good_gradient_top = _ColorButton(
            _valid_hex(
                config.get("chart_good_gradient_top"),
                DEFAULT_CONFIG["chart_good_gradient_top"],
            )
        )
        self.chart_good_gradient_bottom = _ColorButton(
            _valid_hex(
                config.get("chart_good_gradient_bottom"),
                DEFAULT_CONFIG["chart_good_gradient_bottom"],
            )
        )
        self.chart_gradient_top = _ColorButton(
            _valid_hex(
                config.get("chart_gradient_top"), DEFAULT_CONFIG["chart_gradient_top"]
            )
        )
        self.chart_gradient_bottom = _ColorButton(
            _valid_hex(
                config.get("chart_gradient_bottom"),
                DEFAULT_CONFIG["chart_gradient_bottom"],
            )
        )
        self.chart_again_gradient_top = _ColorButton(
            _valid_hex(
                config.get("chart_again_gradient_top"),
                DEFAULT_CONFIG["chart_again_gradient_top"],
            )
        )
        self.chart_again_gradient_bottom = _ColorButton(
            _valid_hex(
                config.get("chart_again_gradient_bottom"),
                DEFAULT_CONFIG["chart_again_gradient_bottom"],
            )
        )
        self.database_location = QLineEdit(_database_location(config))
        self.database_location.setPlaceholderText("Default add-on user_files folder")
        browse_database = QPushButton("Browse")
        qconnect(browse_database.clicked, self._choose_database_location)
        clear_database = QPushButton("Use default")
        qconnect(clear_database.clicked, lambda: self.database_location.setText(""))
        database_location_row = QHBoxLayout()
        database_location_row.addWidget(self.database_location)
        database_location_row.addWidget(browse_database)
        database_location_row.addWidget(clear_database)

        current_bubble_text = str(
            config.get("bubble_text") or DEFAULT_CONFIG["bubble_text"]
        )
        self.bubble_text = QComboBox()
        for label, template in BUBBLE_TEXT_PRESETS:
            self.bubble_text.addItem(label, template)
        preset_index = self.bubble_text.findData(current_bubble_text)
        if preset_index < 0:
            self.bubble_text.addItem("Current custom", current_bubble_text)
            preset_index = self.bubble_text.count() - 1
        self.bubble_text.setCurrentIndex(preset_index)
        self.bubble_duration = QSpinBox()
        self.bubble_duration.setRange(250, 10000)
        self.bubble_duration.setSingleStep(250)
        self.bubble_duration.setSuffix(" ms")
        try:
            duration_ms = int(config.get("bubble_duration_ms", 1800))
        except Exception:
            duration_ms = int(DEFAULT_CONFIG["bubble_duration_ms"])
        self.bubble_duration.setValue(min(10000, max(250, duration_ms)))
        self.show_answer_time = QCheckBox("Show answer time")
        self.show_answer_time.setChecked(bool(config.get("show_answer_time", True)))
        self.show_answer_time_chart = QCheckBox("Show answer time chart")
        self.show_answer_time_chart.setChecked(
            bool(config.get("show_answer_time_chart", True))
        )
        self.always_show_bubble = QCheckBox("Keep bubble and chart on screen")
        self.always_show_bubble.setChecked(
            bool(config.get("always_show_bubble", False))
        )
        self.show_estimated_time = QCheckBox("Show estimated time left")
        self.show_estimated_time.setChecked(
            bool(config.get("show_estimated_time", True))
        )
        self.show_finish_time = QCheckBox("Show estimated finish clock time")
        self.show_finish_time.setChecked(bool(config.get("show_finish_time", True)))

        self.position = QComboBox()
        self.position.addItem("Bottom near buttons", "bottom")
        self.position.addItem("Top area", "top")
        current_position = "top" if config.get("position") == "top" else "bottom"
        self.position.setCurrentIndex(1 if current_position == "top" else 0)

        hint = QLabel("Choose what the center bubble shows during review.")
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        qconnect(buttons.accepted, self.accept)
        qconnect(buttons.rejected, self.reject)

        export_history = QPushButton("Export estimate data JSON")
        qconnect(export_history.clicked, self._export_history_json)
        import_history = QPushButton("Import estimate data JSON")
        qconnect(import_history.clicked, self._import_history_json)
        history_buttons = QHBoxLayout()
        history_buttons.addWidget(export_history)
        history_buttons.addWidget(import_history)

        tabs = QTabWidget()

        general_tab = QWidget()
        general_layout = QFormLayout(general_tab)
        general_layout.addRow("Bubble display", self.bubble_text)
        general_layout.addRow("Bubble time", self.bubble_duration)
        general_layout.addRow("", self.show_answer_time)
        general_layout.addRow("", self.show_answer_time_chart)
        general_layout.addRow("", self.always_show_bubble)
        general_layout.addRow("", self.show_estimated_time)
        general_layout.addRow("", self.show_finish_time)
        general_layout.addRow("Position", self.position)
        general_layout.addRow(hint)
        tabs.addTab(general_tab, "General")

        colors_tab = QWidget()
        colors_layout = QVBoxLayout(colors_tab)

        progress_group = QGroupBox("Progress bar")
        progress_layout = QFormLayout(progress_group)
        progress_layout.addRow("Filled", self.bar_color)
        progress_layout.addRow("Unfilled", self.background_color)
        colors_layout.addWidget(progress_group)

        bubble_group = QGroupBox("Bubble")
        bubble_layout = QFormLayout(bubble_group)
        bubble_layout.addRow("Background", self.bubble_color)
        bubble_layout.addRow("Text", self.bubble_text_color)
        colors_layout.addWidget(bubble_group)

        answer_chart_group = QGroupBox("Answer time chart")
        answer_chart_layout = QFormLayout(answer_chart_group)
        answer_chart_layout.addRow("Good top", self.chart_good_gradient_top)
        answer_chart_layout.addRow("Good bottom", self.chart_good_gradient_bottom)
        answer_chart_layout.addRow("Again top", self.chart_again_gradient_top)
        answer_chart_layout.addRow("Again bottom", self.chart_again_gradient_bottom)
        colors_layout.addWidget(answer_chart_group)

        estimate_chart_group = QGroupBox("Estimate history chart")
        estimate_chart_layout = QFormLayout(estimate_chart_group)
        estimate_chart_layout.addRow("Top", self.chart_gradient_top)
        estimate_chart_layout.addRow("Bottom", self.chart_gradient_bottom)
        colors_layout.addWidget(estimate_chart_group)
        colors_layout.addStretch(1)
        tabs.addTab(colors_tab, "Colors")

        data_tab = QWidget()
        data_layout = QFormLayout(data_tab)
        data_layout.addRow("Database location", database_location_row)
        data_layout.addRow("Estimate history", history_buttons)
        tabs.addTab(data_tab, "Data")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def accept(self) -> None:
        text_template = self.bubble_text.currentData() or DEFAULT_CONFIG["bubble_text"]
        try:
            text_template.format(left=1, done=2, total=3, percent=67)
        except Exception as exc:
            showInfo(f"Bubble display has an invalid setting: {html.escape(str(exc))}")
            return

        old_path = _timing_history_path()
        database_location = self.database_location.text().strip()
        if database_location:
            database_location = os.path.abspath(os.path.expanduser(database_location))
            try:
                os.makedirs(database_location, exist_ok=True)
            except Exception as exc:
                showInfo(
                    f"Could not use database location: {html.escape(str(exc))}"
                )
                return

        next_config = {
            "bar_color": self.bar_color.value(),
            "background_color": self.background_color.value(),
            "bubble_color": self.bubble_color.value(),
            "bubble_text_color": self.bubble_text_color.value(),
            "chart_good_gradient_top": self.chart_good_gradient_top.value(),
            "chart_good_gradient_bottom": self.chart_good_gradient_bottom.value(),
            "chart_gradient_top": self.chart_gradient_top.value(),
            "chart_gradient_bottom": self.chart_gradient_bottom.value(),
            "chart_again_gradient_top": self.chart_again_gradient_top.value(),
            "chart_again_gradient_bottom": self.chart_again_gradient_bottom.value(),
            "database_location": database_location,
            "bubble_text": text_template,
            "bubble_duration_ms": self.bubble_duration.value(),
            "show_answer_time": self.show_answer_time.isChecked(),
            "show_answer_time_chart": self.show_answer_time_chart.isChecked(),
            "always_show_bubble": self.always_show_bubble.isChecked(),
            "show_estimated_time": self.show_estimated_time.isChecked(),
            "show_finish_time": self.show_finish_time.isChecked(),
            "position": self.position.currentData(),
        }
        _save_config(
            next_config
        )
        _migrate_timing_history(old_path, _timing_history_path(next_config))
        super().accept()

    def _choose_database_location(self) -> None:
        current = self.database_location.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose Progress Bar Pro database folder",
            os.path.expanduser(current),
        )
        if path:
            self.database_location.setText(path)

    def _export_history_json(self) -> None:
        default_path = os.path.join(
            os.path.expanduser("~"), "progress_bar_pro_timing_history.json"
        )
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Progress Bar Pro estimate history",
            default_path,
            "JSON files (*.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as export_file:
                json.dump(
                    _timing_history_export_payload(),
                    export_file,
                    indent=2,
                    sort_keys=True,
                )
                export_file.write("\n")
        except Exception as exc:
            showInfo(f"Could not export estimate history: {html.escape(str(exc))}")
            return
        showInfo(f"Exported estimate history to:\n{path}")

    def _import_history_json(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Import Progress Bar Pro estimate history",
            os.path.expanduser("~"),
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as import_file:
                records = _normalized_history_records(json.load(import_file))
            _replace_timing_history(records)
        except Exception as exc:
            showInfo(f"Could not import estimate history: {html.escape(str(exc))}")
            return
        showInfo(f"Imported {len(records)} estimate history records.")


class EstimateDataDialog(QDialog):
    def __init__(self) -> None:
        super().__init__(mw)
        self.setWindowTitle("Progress Bar Pro - Estimate Data")
        self.setMinimumSize(760, 560)

        summary = _daily_history_summary()
        records = _history_records_for_display()

        graph = QLabel()
        graph.setPixmap(_estimate_graph_pixmap(summary))
        graph.setScaledContents(True)
        graph.setMinimumHeight(280)

        table = QTableWidget(len(summary), 5)
        table.setHorizontalHeaderLabels(
            ["Date", "Cards", "Avg answer", "Total answer time", "Decks"]
        )
        for row, day in enumerate(summary):
            values = [
                str(day["date"]),
                str(day["cards"]),
                _format_seconds(float(day["avg_seconds"]))
                if int(day["cards"])
                else "-",
                _format_duration(float(day["total_seconds"]))
                if int(day["cards"])
                else "-",
                str(day["decks"]),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column > 0:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(row, column, item)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        try:
            table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Stretch
            )
        except Exception:
            pass

        export_button = QPushButton("Export CSV")
        qconnect(export_button.clicked, self._export_csv)

        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        qconnect(close_buttons.rejected, self.reject)

        footer = QLabel(
            f"{len(records)} answer timing records stored for the last {TIMING_HISTORY_DAYS} days."
        )
        footer.setWordWrap(True)
        best_day_footer = QLabel(_best_recent_day_text(summary))
        best_day_footer.setWordWrap(True)
        all_time_footer = QLabel(_all_time_summary_text())
        all_time_footer.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(graph)
        layout.addWidget(table)
        layout.addWidget(footer)
        layout.addWidget(best_day_footer)
        layout.addWidget(all_time_footer)
        layout.addWidget(export_button)
        layout.addWidget(close_buttons)

    def _export_csv(self) -> None:
        default_path = os.path.join(
            os.path.expanduser("~"), "progress_bar_pro_estimate_data.csv"
        )
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Progress Bar Pro estimate data",
            default_path,
            "CSV files (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        try:
            with open(path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerows(_history_csv_rows())
        except Exception as exc:
            showInfo(f"Could not export CSV: {html.escape(str(exc))}")
            return

        showInfo(f"Exported estimate data to:\n{path}")


def _show_options() -> None:
    dialog = OptionsDialog()
    dialog.exec()


def _show_estimate_data() -> None:
    dialog = EstimateDataDialog()
    dialog.exec()


def _reset_session_total(*args: Any, **kwargs: Any) -> None:
    global _session_total, _show_bubble_once, _question_started_at, _last_answer_seconds
    global _answer_seconds_ema
    _session_total = 0
    _show_bubble_once = False
    _question_started_at = None
    _last_answer_seconds = None
    _answer_seconds_ema = None
    _answer_seconds_samples.clear()
    _answer_time_chart_samples.clear()


def _start_question_timer() -> None:
    global _question_started_at
    _question_started_at = time.monotonic()


def _on_reviewer_did_answer_card(reviewer: Reviewer, card: Any, ease: int) -> None:
    global _show_bubble_once, _last_answer_seconds
    if _question_started_at is not None:
        _last_answer_seconds = time.monotonic() - _question_started_at
        _record_answer_seconds(_last_answer_seconds, ease)
        _record_answer_history(reviewer, _last_answer_seconds)
    else:
        _last_answer_seconds = None
    _show_bubble_once = True


def _on_state_did_change(new_state: str, old_state: str) -> None:
    if new_state == "review" or old_state == "review":
        _reset_session_total()


def _install_menu() -> None:
    menu = QMenu("Progress Bar Pro", mw)

    options_action = QAction("Options", mw)
    qconnect(options_action.triggered, _show_options)
    menu.addAction(options_action)

    view_action = QAction("View estimate data", mw)
    qconnect(view_action.triggered, _show_estimate_data)
    menu.addAction(view_action)

    mw.form.menuTools.addMenu(menu)
    mw.addonManager.setConfigAction(__name__, _show_options)


gui_hooks.card_will_show.append(_inject_progress_bar)
gui_hooks.reviewer_did_answer_card.append(_on_reviewer_did_answer_card)
gui_hooks.state_did_change.append(_on_state_did_change)
_install_menu()
