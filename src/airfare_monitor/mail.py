"""Build and optionally send one consolidated SMTP message."""

from __future__ import annotations

import html
import os
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

from .config import MailSettings
from .errors import ConfigError
from .models import LegResult, PreferredPriceReference, RunReport, RunStatus


def _price(value: object) -> str:
    return "—" if value is None else f"¥{value:,.0f}"


def _delta_text(value: object) -> str:
    if value is None:
        return "—"
    number = float(value)
    return f"{number:+,.0f}"


def _price_change(current: object, reference: object) -> str:
    if current is None or reference is None:
        return "—"
    difference = current - reference
    if difference > 0:
        return f"上涨 {_price(difference)}"
    if difference < 0:
        return f"下降 {_price(-difference)}"
    return "持平"


def _reference_price(value: object, captured_at: object) -> str:
    if value is None:
        return "—"
    stamp = f"（{captured_at:%m-%d %H:%M}）" if captured_at is not None else ""
    return f"{_price(value)}{stamp}"


def _preferred_reference(result: LegResult, index: int) -> PreferredPriceReference:
    if index < len(result.preferred_price_references):
        return result.preferred_price_references[index]
    return PreferredPriceReference()


def _preferred_schedule_text(result: LegResult, index: int) -> str:
    preferred = result.leg.preferred_schedules[index]
    arrival_date = result.leg.departure_date + timedelta(days=preferred.arrival_day_offset)
    schedule = (
        f"{preferred.label} · {result.leg.departure_date:%m-%d} {preferred.departure_time:%H:%M}"
        f" → {arrival_date:%m-%d} {preferred.arrival_time:%H:%M}"
    )
    flight = result.preferred_matches[index] if index < len(result.preferred_matches) else None
    if flight is None:
        tolerance = (
            f"起飞±{preferred.departure_tolerance_minutes}分钟/"
            f"到达±{preferred.arrival_tolerance_minutes}分钟"
        )
        return f"目标 {schedule} · {tolerance}内未找到匹配直达航班"
    actual = f"{flight.etd_local:%m-%d %H:%M} → {flight.eta_local:%m-%d %H:%M}"
    return f"目标 {schedule} · 实际 {actual} · {flight.flight_codes_display}"


def _preferred_plain_lines(result: LegResult, index: int) -> list[str]:
    flight = result.preferred_matches[index] if index < len(result.preferred_matches) else None
    current = flight.total_price_cny if flight else None
    reference = _preferred_reference(result, index)
    return [
        f"★ {_preferred_schedule_text(result, index)}",
        (
            f"  本次：{_price(current)}  "
            f"首次：{_reference_price(reference.first_total_price_cny, reference.first_captured_at)}  "
            f"较首次：{_price_change(current, reference.first_total_price_cny)}"
        ),
        (
            f"  上次：{_reference_price(reference.previous_total_price_cny, reference.previous_captured_at)}  "
            f"较上次：{_price_change(current, reference.previous_total_price_cny)}"
        ),
    ]


def _preferred_html(result: LegResult, index: int) -> str:
    flight = result.preferred_matches[index] if index < len(result.preferred_matches) else None
    current = flight.total_price_cny if flight else None
    reference = _preferred_reference(result, index)
    return f"""<div style="background:#fff8e1;border-left:4px solid #f5a623;padding:10px;margin:8px 0">
        <div><b>⭐ {html.escape(_preferred_schedule_text(result, index))}</b></div>
        <div style="font-size:18px;margin-top:6px"><b>本次 {_price(current)}</b></div>
        <div>首次 {_reference_price(reference.first_total_price_cny, reference.first_captured_at)} ·
        较首次 {_price_change(current, reference.first_total_price_cny)}</div>
        <div>上次 {_reference_price(reference.previous_total_price_cny, reference.previous_captured_at)} ·
        较上次 {_price_change(current, reference.previous_total_price_cny)}</div>
        </div>"""


def build_subject(report: RunReport, settings: MailSettings) -> str:
    stamp = report.finished_at.strftime("%Y-%m-%d %H:%M")
    total = len(report.legs)
    hits = report.confirmed_hits
    partial_marker = "[部分失败]" if report.status == RunStatus.PARTIAL else "[全部失败]" if report.status == RunStatus.FAILED else ""
    if hits:
        first = hits[0]
        comparison = (
            f"{first.leg.route_display} {_price(first.minimum_total_cny)} ≤ "
            f"{_price(first.leg.expected_total_price_cny)}"
        )
        more = f" 等{len(hits)}程" if len(hits) > 1 else ""
        return (
            f"{settings.threshold_subject_prefix}{partial_marker}[{len(hits)}/{total}程] "
            f"{comparison}{more} | {stamp}"
        )
    return f"{settings.normal_subject_prefix}{partial_marker} {total}程更新 | {stamp}"


def _leg_plain(result: LegResult, confirmed: bool) -> list[str]:
    minimum = result.minimum_total_cny
    threshold_delta = minimum - result.leg.expected_total_price_cny if minimum is not None else None
    previous_delta = minimum - result.previous_min_total_cny if minimum is not None and result.previous_min_total_cny is not None else None
    lines = [
        f"{result.leg.id} {result.leg.route_display} {result.leg.departure_date} {result.leg.etd_window.display()}",
        f"状态：{result.status}  最低：{_price(minimum)}  心理价位：{_price(result.leg.expected_total_price_cny)}",
        f"与阈值：{_delta_text(threshold_delta)}  较上次：{_delta_text(previous_delta)}  确认命中：{'是' if confirmed else '否'}",
    ]
    if result.leg.preferred_schedules:
        lines.append("【重点关注时段价格】")
        for index in range(len(result.leg.preferred_schedules)):
            lines.extend(_preferred_plain_lines(result, index))
    lines.append("最低价备选（前3条）：")
    if result.flights:
        for flight in result.flights[:3]:
            lines.append(
                f"- {flight.flight_codes_display} {flight.etd_local:%m-%d %H:%M} {_price(flight.total_price_cny)}"
            )
    else:
        lines.append("- 无符合条件的航班")
    if result.error_message:
        lines.append(f"错误：{result.error_message}")
    return lines


def build_message(
    report: RunReport,
    settings: MailSettings,
    *,
    sender: str,
    recipients: list[str],
    attachment: Path | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = build_subject(report, settings)
    message["From"] = sender
    message["To"] = ", ".join(recipients)

    plain = [f"采集时间：{report.finished_at:%Y-%m-%d %H:%M}", f"总体状态：{report.status}", ""]
    cards: list[str] = []
    for result in report.legs:
        confirmed = result.leg.id in report.threshold_confirmed_leg_ids
        plain.extend(_leg_plain(result, confirmed))
        plain.append("")
        minimum = result.minimum_total_cny
        threshold_delta = minimum - result.leg.expected_total_price_cny if minimum is not None else None
        previous_delta = minimum - result.previous_min_total_cny if minimum is not None and result.previous_min_total_cny is not None else None
        flights = "".join(
            f"<li>{html.escape(flight.flight_codes_display)} · {flight.etd_local:%m-%d %H:%M} · {_price(flight.total_price_cny)}</li>"
            for flight in result.flights[:3]
        ) or "<li>无符合条件的航班</li>"
        preferred_flights = ""
        if result.leg.preferred_schedules:
            preferred_flights = '<div style="margin-top:10px"><b>重点关注时段价格</b>' + "".join(
                _preferred_html(result, index) for index in range(len(result.leg.preferred_schedules))
            ) + "</div>"
        cards.append(
            f"""<section style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0">
            <h3 style="margin:0 0 8px">{html.escape(result.leg.id)} · {html.escape(result.leg.route_display)}</h3>
            <div>{result.leg.departure_date} · ETD {result.leg.etd_window.display()}</div>
            <div><b>最低 {_price(minimum)}</b> · 心理价位 {_price(result.leg.expected_total_price_cny)}</div>
            <div>与阈值 {_delta_text(threshold_delta)} · 较上次 {_delta_text(previous_delta)}</div>
            <div>状态 {html.escape(str(result.status))} · 确认命中 {'是' if confirmed else '否'}</div>
            {preferred_flights}
            <div style="margin-top:10px"><b>最低价备选（前3条）</b>
            <ul style="padding-left:20px">{flights}</ul></div>
            {f'<div style="color:#b00020">{html.escape(result.error_message)}</div>' if result.error_message else ''}
            </section>"""
        )
    reminder = "价格为采集时观察到的含税总价，库存和价格可能变化，请务必在 App 中最终确认。"
    plain.append(reminder)
    message.set_content("\n".join(plain))
    message.add_alternative(
        f"""<!doctype html><html><body style="font-family:Arial,sans-serif;max-width:640px;margin:auto;padding:12px">
        <h2>航价监控摘要</h2><div>采集时间：{report.finished_at:%Y-%m-%d %H:%M}</div>
        <div>总体状态：{html.escape(str(report.status))}</div>{''.join(cards)}
        <p style="color:#666">{reminder}</p></body></html>""",
        subtype="html",
    )
    if attachment is not None and settings.attach_excel:
        data = attachment.read_bytes()
        message.add_attachment(
            data,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=attachment.name,
        )
    return message


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"缺少环境变量 {name}")
    return value


def send_report(report: RunReport, settings: MailSettings, attachment: Path | None = None) -> None:
    """Send mail only when explicitly reached by the runtime and mail.enabled is true."""
    if not settings.enabled:
        return
    username = _required_env(settings.username_env)
    password = _required_env(settings.password_env)
    sender = _required_env(settings.sender_env)
    recipients = [item.strip() for item in _required_env(settings.recipients_env).split(",") if item.strip()]
    if not recipients:
        raise ConfigError(f"环境变量 {settings.recipients_env} 未包含有效收件地址")
    message = build_message(report, settings, sender=sender, recipients=recipients, attachment=attachment)
    context = ssl.create_default_context()
    if settings.security == "ssl":
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=30) as client:
            client.login(username, password)
            client.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as client:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
            client.login(username, password)
            client.send_message(message)
