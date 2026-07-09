"""Report service — generates daily, weekly, monthly, and YTD maintenance reports.

Each report computes KPIs from ticket data for a given company and period:
- Total tickets opened / closed
- Average resolution time (hours)
- Top failing machines (most tickets)
- Urgency distribution (High/Medium/Low counts)
- Plant health % (machines without open tickets)
- Comparison to previous period (trend)

Reports can be sent via WhatsApp to owners/supervisors and are also
available via the REST API for the dashboard.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.infrastructure.logging import get_logger
from app.repositories.base import MachineRepository, TicketRepository

log = get_logger("turbofix.reports")


def _parse_dt(val) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    try:
        return datetime.strptime(str(val).strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _period_range(period: str, now: Optional[datetime] = None) -> tuple:
    """Return (start, end) datetimes for the given period."""
    now = now or datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "daily":
        return today_start - timedelta(days=1), today_start
    elif period == "weekly":
        week_start = today_start - timedelta(days=today_start.weekday())
        return week_start - timedelta(weeks=1), week_start
    elif period == "monthly":
        month_start = today_start.replace(day=1)
        prev_month = (month_start - timedelta(days=1)).replace(day=1)
        return prev_month, month_start
    elif period == "ytd":
        year_start = today_start.replace(month=1, day=1)
        return year_start, now
    else:
        raise ValueError(f"Unknown period: {period}")


def _previous_period_range(period: str, now: Optional[datetime] = None) -> tuple:
    """Return the range for the period BEFORE the current one (for trend comparison)."""
    now = now or datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "daily":
        end = today_start - timedelta(days=1)
        return end - timedelta(days=1), end
    elif period == "weekly":
        week_start = today_start - timedelta(days=today_start.weekday())
        prev_week_start = week_start - timedelta(weeks=1)
        return prev_week_start - timedelta(weeks=1), prev_week_start
    elif period == "monthly":
        month_start = today_start.replace(day=1)
        prev_month = (month_start - timedelta(days=1)).replace(day=1)
        prev_prev_month = (prev_month - timedelta(days=1)).replace(day=1)
        return prev_prev_month, prev_month
    elif period == "ytd":
        year_start = today_start.replace(month=1, day=1)
        prev_year_start = year_start.replace(year=year_start.year - 1)
        prev_year_same_day = now.replace(year=now.year - 1)
        return prev_year_start, prev_year_same_day
    else:
        raise ValueError(f"Unknown period: {period}")


def _filter_tickets_in_range(tickets: List[dict], start: datetime, end: datetime) -> List[dict]:
    result = []
    for t in tickets:
        reported = _parse_dt(t.get("reported_at"))
        if reported and start <= reported < end:
            result.append(t)
    return result


def _compute_metrics(tickets: List[dict], all_machines: List[dict]) -> dict:
    """Compute report metrics from a list of tickets."""
    total = len(tickets)
    opened = [t for t in tickets if t.get("status") == "Open"]
    closed = [t for t in tickets if t.get("status") == "Closed"]

    resolution_times = []
    for t in closed:
        reported = _parse_dt(t.get("reported_at"))
        closed_at = _parse_dt(t.get("closed_at"))
        if reported and closed_at:
            hours = (closed_at - reported).total_seconds() / 3600
            resolution_times.append(hours)

    avg_resolution = round(sum(resolution_times) / len(resolution_times), 1) if resolution_times else 0

    urgency_dist = {"High": 0, "Medium": 0, "Low": 0}
    for t in tickets:
        urg = t.get("urgency", "Medium")
        if urg in urgency_dist:
            urgency_dist[urg] += 1

    machine_counts = {}
    for t in tickets:
        mid = t.get("machine_id", "")
        mname = t.get("machine_name", mid)
        machine_counts[mid] = machine_counts.get(mid, {"name": mname, "count": 0})
        machine_counts[mid]["count"] += 1

    top_machines = sorted(machine_counts.values(), key=lambda x: x["count"], reverse=True)[:5]

    total_machines = len(all_machines)
    machines_with_open = len({t.get("machine_id") for t in opened})
    plant_health = round((total_machines - machines_with_open) / total_machines * 100) if total_machines else 100

    return {
        "total_tickets": total,
        "tickets_opened": len(opened),
        "tickets_closed": len(closed),
        "avg_resolution_hours": avg_resolution,
        "urgency_distribution": urgency_dist,
        "top_failing_machines": top_machines,
        "plant_health_pct": plant_health,
        "total_machines": total_machines,
    }


def generate_report(
    company_code: str,
    company_name: str,
    period: str,
    tickets_repo: TicketRepository,
    machines_repo: MachineRepository,
) -> dict:
    """Generate a report for a company and period."""
    all_tickets = tickets_repo.get_company_tickets(company_code)
    all_machines = machines_repo.get_company_machines(company_code)

    start, end = _period_range(period)
    period_tickets = _filter_tickets_in_range(all_tickets, start, end)
    current = _compute_metrics(period_tickets, all_machines)

    try:
        prev_start, prev_end = _previous_period_range(period)
        prev_tickets = _filter_tickets_in_range(all_tickets, prev_start, prev_end)
        previous = _compute_metrics(prev_tickets, all_machines)
    except Exception:
        previous = None

    trend = {}
    if previous and previous["total_tickets"] > 0:
        pct_change = round(
            (current["total_tickets"] - previous["total_tickets"]) / previous["total_tickets"] * 100, 1
        )
        trend["ticket_volume_change_pct"] = pct_change
        if previous["avg_resolution_hours"] > 0:
            res_change = round(
                (current["avg_resolution_hours"] - previous["avg_resolution_hours"])
                / previous["avg_resolution_hours"] * 100, 1
            )
            trend["resolution_time_change_pct"] = res_change

    period_labels = {
        "daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "ytd": "Year to Date",
    }

    return {
        "company_code": company_code,
        "company_name": company_name,
        "period": period,
        "period_label": period_labels.get(period, period),
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "metrics": current,
        "trend": trend,
    }


def format_report_text(report: dict) -> str:
    """Format a report dict into a human-readable text message."""
    m = report["metrics"]
    lines = [
        f"📊 *TurboFix {report['period_label']} Report*",
        f"Company: {report['company_name']}",
        f"Period: {report['start']} to {report['end']}",
        "",
        f"📋 Total Tickets: {m['total_tickets']}",
        f"  ✅ Closed: {m['tickets_closed']}",
        f"  🔴 Open: {m['tickets_opened']}",
        f"⏱ Avg Resolution: {m['avg_resolution_hours']} hours",
        f"🏥 Plant Health: {m['plant_health_pct']}%",
        "",
        f"🚨 Urgency: High {m['urgency_distribution']['High']} | "
        f"Medium {m['urgency_distribution']['Medium']} | "
        f"Low {m['urgency_distribution']['Low']}",
    ]

    if m["top_failing_machines"]:
        lines.append("")
        lines.append("🔧 Top Failing Machines:")
        for machine in m["top_failing_machines"][:3]:
            lines.append(f"  • {machine['name']}: {machine['count']} tickets")

    trend = report.get("trend", {})
    if trend:
        lines.append("")
        lines.append("📈 Trend vs Previous Period:")
        if "ticket_volume_change_pct" in trend:
            change = trend["ticket_volume_change_pct"]
            arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
            lines.append(f"  Ticket volume: {arrow} {abs(change)}%")
        if "resolution_time_change_pct" in trend:
            change = trend["resolution_time_change_pct"]
            arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
            lines.append(f"  Resolution time: {arrow} {abs(change)}%")

    return "\n".join(lines)
