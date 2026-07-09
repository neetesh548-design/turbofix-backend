"""Dashboard service — compute per-company KPIs from live ticket/machine data.

Extracted from the _compute_kpis() function in vault_router.py.
"""

from datetime import datetime, timedelta, timezone

from app.repositories.base import MachineRepository, TicketRepository


def compute_kpis(
    company_code: str,
    company_name: str,
    tickets_repo: TicketRepository,
    machines_repo: MachineRepository,
) -> dict:
    """Compute live KPI dashboard for a company. Pure function — no I/O calls."""
    machines = machines_repo.get_company_machines(company_code)
    tickets = tickets_repo.get_company_tickets(company_code)

    open_tickets = sum(1 for t in tickets if t.get("status") == "Open")
    closed_today = sum(
        1 for t in tickets
        if t.get("status") == "Closed"
        and t.get("closed_at")
        and datetime.fromisoformat(
            str(t["closed_at"]).replace("Z", "+00:00")
        ).date() == datetime.now(timezone.utc).date()
    )
    machines_down = sum(1 for m in machines if m.get("has_open_tickets"))
    total_tickets = len(tickets)
    total_machines = len(machines)

    # Average hours to fix (closed tickets only)
    closed_tickets = [t for t in tickets if t.get("status") == "Closed"]
    avg_hours = 0.0
    if closed_tickets:
        hours_sum, count = 0.0, 0
        for t in closed_tickets:
            try:
                if t.get("hours_to_fix"):
                    hours_sum += float(t["hours_to_fix"])
                    count += 1
            except (ValueError, TypeError):
                pass
        avg_hours = hours_sum / count if count > 0 else 0.0

    plant_health = (
        100 if total_machines == 0
        else int((total_machines - machines_down) / total_machines * 100)
    )

    # Recent activity (last 5 tickets, most recent first)
    recent = sorted(
        [
            {
                "ticket_id": t.get("ticket_id"),
                "machine_id": t.get("machine_id"),
                "machine_name": t.get("machine_name"),
                "status": t.get("status"),
                "urgency": t.get("urgency"),
                "reported_at": t.get("reported_at"),
            }
            for t in tickets
        ],
        key=lambda x: x.get("reported_at") or "2000-01-01",
        reverse=True,
    )[:5]

    # Open tickets needing action, most urgent first, oldest first within same urgency
    urgency_rank = {"High": 0, "Medium": 1, "Low": 2}
    needs_attention = sorted(
        [
            {
                "machine_name": t.get("machine_name"),
                "urgency": t.get("urgency") or "",
                "description": t.get("description") or t.get("ai_summary") or "",
                "reported_at": t.get("reported_at"),
            }
            for t in tickets
            if t.get("status") == "Open"
        ],
        key=lambda x: (urgency_rank.get(x["urgency"], 3), str(x["reported_at"] or "9999")),
    )
    urgent_open = sum(1 for t in needs_attention if t["urgency"] == "High")

    # Tickets per ISO week, last 6 weeks (zero-filled)
    def _parse_reported(value):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value), fmt)
            except (ValueError, TypeError):
                continue
        return None

    today = datetime.now(timezone.utc).date()
    this_week_start = today - timedelta(days=today.weekday())
    week_starts = [this_week_start - timedelta(weeks=i) for i in range(5, -1, -1)]
    week_counts = {ws: 0 for ws in week_starts}
    for t in tickets:
        parsed = _parse_reported(t.get("reported_at"))
        if parsed is None:
            continue
        ws = parsed.date() - timedelta(days=parsed.weekday())
        if ws in week_counts:
            week_counts[ws] += 1
    weekly_trend = [
        {"week_start": ws.strftime("%d %b"), "count": week_counts[ws]}
        for ws in week_starts
    ]

    return {
        "company_code": company_code,
        "company_name": company_name,
        "kpis": {
            "open_tickets": open_tickets,
            "machines_down": machines_down,
            "closed_today": closed_today,
            "total_tickets": total_tickets,
            "avg_hours_to_fix": round(avg_hours, 1),
            "plant_health_pct": plant_health,
            "total_machines": total_machines,
            "urgent_open": urgent_open,
        },
        "recent_activity": recent,
        "needs_attention": needs_attention,
        "weekly_trend": weekly_trend,
    }
