import requests

from app.analysis import BetEvaluation
from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


def send_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    requests.post(
        API_URL,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )


def format_evaluations(evaluations: list[BetEvaluation]) -> str:
    if not evaluations:
        return ""
    m = evaluations[0].match
    lines = [
        f"*{m.home_team} vs {m.away_team}*",
        f"_{m.competition}_ -- {m.kickoff.strftime('%H:%M UTC')}",
        "",
    ]
    for i, ev in enumerate(evaluations, start=1):
        lines.append(
            f"{i}. {ev.market} -- {ev.selection} | cuota {ev.odds:.2f} | "
            f"prob {ev.prob_real*100:.1f}% | value {ev.value_percent:.1f}%"
        )

    first = evaluations[0]
    if first.home_context:
        lines.append(f"\n_{m.home_team}_: {first.home_context}")
    if first.away_context:
        lines.append(f"_{m.away_team}_: {first.away_context}")
    return "\n".join(lines)


def notify_match_results(evaluations: list[BetEvaluation]) -> None:
    text = format_evaluations(evaluations)
    if text:
        print("\n" + text + "\n")
        send_message(text)
