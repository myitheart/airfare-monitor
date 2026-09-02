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
from .models import FlightSnapshot, ItinerarySnapshot, LegResult, PreferredPriceReference, RunReport, RunStatus


def _price(value: object) -> str:
    return "—" if value is None else f"¥{value:,.0f}"


def _flight_price_text(flight: FlightSnapshot | None) -> str:
    """Show Tongcheng domestic mandatory-fee detail; keep other sources compact."""
    if flight is None:
        return "—"
    if flight.source_domain == "ly.com" and flight.base_price_cny is not None and flight.tax_cny is not None:
        return (
            f"票面 {_price(flight.base_price_cny)} + 机建燃油 {_price(flight.tax_cny)}"
            f" = 预计支付 {_price(flight.total_price_cny)}"
        )
    return _price(flight.total_price_cny)


def _duration_text(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}小时{remainder}分钟"
    if hours:
        return f"{hours}小时"
    return f"{remainder}分钟"


def _single_itinerary_text(flight: FlightSnapshot | ItinerarySnapshot) -> str:
    if flight.is_direct:
        return "直达"
    airports = f"，经 {flight.connection_airports_display}" if flight.connection_airports else ""
    return f"中转{flight.segment_count - 1}次{airports}，等待 {_duration_text(flight.layover_minutes)}"


def _itinerary_text(flight: FlightSnapshot) -> str:
    outbound = _single_itinerary_text(flight)
    inbound = flight.return_itinerary
    if inbound is None:
        return outbound
    return (
        f"去程 {outbound}；返程 {inbound.flight_codes_display} "
        f"{inbound.etd_local:%m-%d %H:%M} {_single_itinerary_text(inbound)}"
    )


def _legacy_seat_text(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        count = int(text)
        return "9张或以上（平台提示）" if count >= 9 else f"{count}张"
    return text


def _seat_summary(flight: FlightSnapshot | None) -> str:
    if flight is None:
        return "余票：未返回"
    overall = (
        flight.seat_availability.display()
        if flight.seat_availability
        else _legacy_seat_text(flight.remaining_seats)
    )
    outbound = (
        flight.outbound_seat_availability.display()
        if flight.outbound_seat_availability
        else overall
    )
    inbound = flight.return_itinerary
    if inbound is None:
        return f"余票：{overall or outbound or '未返回'}"
    inbound_text = inbound.seat_availability.display() if inbound.seat_availability else None
    return (
        f"余票：整体 {overall or '未返回'} · "
        f"去程 {outbound or '未返回'} · 返程 {inbound_text or '未返回'}"
    )


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


def _flight_reference(result: LegResult, flight: FlightSnapshot) -> PreferredPriceReference:
    reference = result.flight_price_references.get(flight.flight_signature)
    if reference is not None:
        return reference
    return PreferredPriceReference(
        first_total_price_cny=flight.total_price_cny,
        first_captured_at=result.captured_at,
    )


def _candidate_plain_lines(result: LegResult, flight: FlightSnapshot, rank: int) -> list[str]:
    reference = _flight_reference(result, flight)
    current = flight.total_price_cny
    inbound = flight.return_itinerary
    if inbound is None:
        lines = [
            f"[{rank}] {_flight_price_text(flight)} · {flight.flight_codes_display}",
            (
                f"    {flight.origin_airport_iata} → {flight.destination_airport_iata} · "
                f"{flight.etd_local:%m-%d %H:%M} → {flight.eta_local:%m-%d %H:%M}"
            ),
            f"    {_single_itinerary_text(flight)} · {_seat_summary(flight)}",
        ]
    else:
        lines = [
            f"[{rank}] 往返合计 {_flight_price_text(flight)}",
            (
                f"    去程 {flight.flight_codes_display} · "
                f"{flight.etd_local:%m-%d %H:%M} → {flight.eta_local:%m-%d %H:%M} · "
                f"{_single_itinerary_text(flight)}"
            ),
            (
                f"    返程 {inbound.flight_codes_display} · "
                f"{inbound.etd_local:%m-%d %H:%M} → {inbound.eta_local:%m-%d %H:%M} · "
                f"{_single_itinerary_text(inbound)}"
            ),
            f"    {_seat_summary(flight)}",
        ]
    lines.extend(
        [
            (
                f"    首次：{_reference_price(reference.first_total_price_cny, reference.first_captured_at)} · "
                f"较首次：{_price_change(current, reference.first_total_price_cny)}"
            ),
            (
                f"    上次：{_reference_price(reference.previous_total_price_cny, reference.previous_captured_at)} · "
                f"较上次：{_price_change(current, reference.previous_total_price_cny)}"
            ),
        ]
    )
    return lines


def _candidate_html(result: LegResult, flight: FlightSnapshot, rank: int) -> str:
    reference = _flight_reference(result, flight)
    current = flight.total_price_cny
    inbound = flight.return_itinerary
    if inbound is None:
        itinerary_rows = f"""
        <div style="margin-top:7px"><b>{html.escape(flight.flight_codes_display)}</b> ·
        {flight.origin_airport_iata} → {flight.destination_airport_iata}</div>
        <div>{flight.etd_local:%m-%d %H:%M} → {flight.eta_local:%m-%d %H:%M}</div>
        <div>{html.escape(_single_itinerary_text(flight))}</div>"""
    else:
        itinerary_rows = f"""
        <div style="margin-top:7px"><b>去程</b> {html.escape(flight.flight_codes_display)} ·
        {flight.etd_local:%m-%d %H:%M} → {flight.eta_local:%m-%d %H:%M}</div>
        <div style="color:#555">{html.escape(_single_itinerary_text(flight))}</div>
        <div style="margin-top:5px"><b>返程</b> {html.escape(inbound.flight_codes_display)} ·
        {inbound.etd_local:%m-%d %H:%M} → {inbound.eta_local:%m-%d %H:%M}</div>
        <div style="color:#555">{html.escape(_single_itinerary_text(inbound))}</div>"""
    price_label = f"往返合计 {_flight_price_text(flight)}" if inbound else _flight_price_text(flight)
    return f"""<div style="border:1px solid #d8dee8;border-radius:8px;padding:11px;margin:9px 0;background:#f8fafc">
        <div style="font-size:17px"><span style="color:#64748b">#{rank}</span>
        <b style="color:#b45309">{html.escape(price_label)}</b></div>
        {itinerary_rows}
        <div style="margin-top:6px;color:#7c3aed">{html.escape(_seat_summary(flight))}</div>
        <div style="border-top:1px solid #e5e7eb;margin-top:8px;padding-top:6px;font-size:13px;color:#334155">
        <div><b>首次</b> {_reference_price(reference.first_total_price_cny, reference.first_captured_at)}</div>
        <div>较首次：{_price_change(current, reference.first_total_price_cny)}</div>
        <div style="margin-top:4px"><b>上次</b> {_reference_price(reference.previous_total_price_cny, reference.previous_captured_at)}</div>
        <div>较上次：{_price_change(current, reference.previous_total_price_cny)}</div>
        </div></div>"""


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
        return f"目标 {schedule} · {tolerance}内未找到匹配航班"
    actual = f"{flight.etd_local:%m-%d %H:%M} → {flight.eta_local:%m-%d %H:%M}"
    return f"目标 {schedule} · 实际 {actual} · {flight.flight_codes_display} · {_itinerary_text(flight)}"


def _preferred_plain_lines(result: LegResult, index: int) -> list[str]:
    flight = result.preferred_matches[index] if index < len(result.preferred_matches) else None
    current = flight.total_price_cny if flight else None
    reference = _preferred_reference(result, index)
    current_label = "本次往返合计" if result.leg.is_round_trip else "本次"
    return [
        f"★ {_preferred_schedule_text(result, index)}",
        (
            f"  {current_label}：{_flight_price_text(flight)}  "
            f"首次：{_reference_price(reference.first_total_price_cny, reference.first_captured_at)}  "
            f"较首次：{_price_change(current, reference.first_total_price_cny)}"
        ),
        f"  {_seat_summary(flight)}",
        (
            f"  上次：{_reference_price(reference.previous_total_price_cny, reference.previous_captured_at)}  "
            f"较上次：{_price_change(current, reference.previous_total_price_cny)}"
        ),
    ]


def _preferred_html(result: LegResult, index: int) -> str:
    flight = result.preferred_matches[index] if index < len(result.preferred_matches) else None
    current = flight.total_price_cny if flight else None
    reference = _preferred_reference(result, index)
    current_label = "本次往返合计" if result.leg.is_round_trip else "本次"
    return f"""<div style="background:#fff8e1;border-left:4px solid #f5a623;padding:10px;margin:8px 0">
        <div><b>⭐ {html.escape(_preferred_schedule_text(result, index))}</b></div>
        <div style="font-size:18px;margin-top:6px"><b>{current_label} {html.escape(_flight_price_text(flight))}</b></div>
        <div>{html.escape(_seat_summary(flight))}</div>
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
    round_trip_count = sum(result.leg.is_round_trip for result in report.legs)
    if round_trip_count == total:
        update_label = f"{total}组往返更新"
        count_unit = "组"
    elif round_trip_count:
        update_label = f"{total}项更新（含{round_trip_count}组往返）"
        count_unit = "项"
    else:
        update_label = f"{total}程更新"
        count_unit = "程"
    if hits:
        first = hits[0]
        comparison = (
            f"{first.leg.route_display} {_price(first.minimum_total_cny)} ≤ "
            f"{_price(first.leg.expected_total_price_cny)}"
        )
        more = f" 等{len(hits)}程" if len(hits) > 1 else ""
        return (
            f"{settings.threshold_subject_prefix}{partial_marker}[{len(hits)}/{total}{count_unit}] "
            f"{comparison}{more} | {stamp}"
        )
    return f"{settings.normal_subject_prefix}{partial_marker} {update_label} | {stamp}"


def _leg_plain(result: LegResult, confirmed: bool) -> list[str]:
    minimum = result.minimum_total_cny
    threshold = result.leg.expected_total_price_cny
    threshold_delta = minimum - threshold if minimum is not None and threshold is not None else None
    previous_delta = minimum - result.previous_min_total_cny if minimum is not None and result.previous_min_total_cny is not None else None
    lines = [
        (
            f"{result.leg.id} {result.leg.route_display} "
            f"去程 {result.leg.departure_date} {result.leg.etd_window.display()}"
            + (
                f"  返程 {result.leg.return_date} {result.leg.return_etd_window.display()}"
                if result.leg.return_date and result.leg.return_etd_window
                else ""
            )
        ),
        f"状态：{result.status}  {'往返合计最低' if result.leg.is_round_trip else '最低'}：{_flight_price_text(result.flights[0] if result.flights else None)}  心理价位：{_price(result.leg.expected_total_price_cny)}",
        f"与阈值：{_delta_text(threshold_delta)}  较上次：{_delta_text(previous_delta)}  确认命中：{'是' if confirmed else '否'}",
    ]
    if result.leg.preferred_schedules:
        lines.append("【重点关注时段价格】")
        for index in range(len(result.leg.preferred_schedules)):
            lines.extend(_preferred_plain_lines(result, index))
    lines.append("【最低价候选（前5条）】")
    if result.flights:
        for rank, flight in enumerate(result.flights[:5], start=1):
            lines.extend(_candidate_plain_lines(result, flight, rank))
            lines.append("")
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
        threshold = result.leg.expected_total_price_cny
        threshold_delta = minimum - threshold if minimum is not None and threshold is not None else None
        previous_delta = minimum - result.previous_min_total_cny if minimum is not None and result.previous_min_total_cny is not None else None
        flights = "".join(
            _candidate_html(result, flight, rank)
            for rank, flight in enumerate(result.flights[:5], start=1)
        ) or '<div style="padding:10px;color:#666">无符合条件的航班</div>'
        preferred_flights = ""
        if result.leg.preferred_schedules:
            preferred_flights = '<div style="margin-top:10px"><b>重点关注时段价格</b>' + "".join(
                _preferred_html(result, index) for index in range(len(result.leg.preferred_schedules))
            ) + "</div>"
        cards.append(
            f"""<section style="border:1px solid #cbd5e1;border-radius:10px;padding:13px;margin:12px 0;background:#fff;line-height:1.55">
            <h3 style="margin:0 0 8px;font-size:19px">{html.escape(result.leg.id)} · {html.escape(result.leg.route_display)}</h3>
            <div style="color:#475569">去程 {result.leg.departure_date} · ETD {result.leg.etd_window.display()}</div>
            {f'<div style="color:#475569">返程 {result.leg.return_date} · ETD {result.leg.return_etd_window.display()}</div>' if result.leg.return_date and result.leg.return_etd_window else ''}
            <div style="font-size:18px;margin-top:8px"><b>{'往返合计最低' if result.leg.is_round_trip else '最低'} {html.escape(_flight_price_text(result.flights[0] if result.flights else None))}</b></div>
            <div>心理价位 {_price(result.leg.expected_total_price_cny)} · 与阈值 {_delta_text(threshold_delta)}</div>
            <div>较上次最低价 {_delta_text(previous_delta)} · 状态 {html.escape(str(result.status))} · 确认命中 {'是' if confirmed else '否'}</div>
            {preferred_flights}
            <div style="margin-top:14px"><b style="font-size:17px">最低价候选（前5条）</b>
            {flights}</div>
            {f'<div style="color:#b00020">{html.escape(result.error_message)}</div>' if result.error_message else ''}
            </section>"""
        )
    reminder = "国际航班显示采集时的含税总价；国内航班同时显示票面价、机建燃油和预计支付总价。余票是平台搜索结果中的库存提示，其中“9张或以上”并非精确库存；库存和价格都可能变化，请务必在 App 中最终确认。"
    plain.append(reminder)
    message.set_content("\n".join(plain))
    message.add_alternative(
        f"""<!doctype html><html><body style="font-family:Arial,'Microsoft YaHei',sans-serif;max-width:680px;margin:auto;padding:12px;background:#f1f5f9;color:#0f172a;line-height:1.55">
        <h2 style="margin:4px 0">航价监控摘要</h2><div>采集时间：{report.finished_at:%Y-%m-%d %H:%M}</div>
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
