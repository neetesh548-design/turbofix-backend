"""Dashboard service — compute per-company KPIs from live ticket/machine data.

Design notes (system hardening):
  1. Objective signals only: `machines_down` and `plant_health_pct` are derived from
     server-computed open ticket counts, never from the `has_open_tickets` formula column
     which a supervisor could leave blank.
  2. Per-machine risk tiers: Low / Medium / High — each machine gets a risk badge so the
     dashboard reflects live ticket pressure, not just a binary up/down state.
  3. Stale detection: a machine quiet for > STALE_MACHINE_DAYS is flagged "stale" so
     silence doesn't masquerade as health.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app import config
from app.repositories.base import CustomKpiRepository, MachineRepository, TicketRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(value) -> Optional[datetime]:
    """Tolerant parser for dates stored in various formats by Excel/Sheets backends."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _machine_risk(open_count: int, tickets_30d: int, last_activity_at: str, now: datetime) -> str:
    """Return 'stale' | 'low' | 'medium' | 'high'.

    Stale wins over everything else — a silent machine is unknown, not healthy.
    Risk tiers are purely server-computed; nothing a supervisor self-declares affects them.
    """
    # Stale: blank means feature predates this field, treat as unknown
    if last_activity_at == "" or last_activity_at is None:
        return "stale"
    ts = _parse_dt(last_activity_at)
    if ts is None or (now - ts).days > config.STALE_MACHINE_DAYS:
        return "stale"
    if open_count >= 1 or tickets_30d >= 4:
        return "high"
    if tickets_30d >= 1:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_kpis(
    company_code: str,
    company_name: str,
    tickets_repo: TicketRepository,
    machines_repo: MachineRepository,
    supervisor_id: Optional[str] = None,
) -> dict:
    """Compute live KPI dashboard for a company. Pure function — no I/O calls."""
    machines = machines_repo.get_company_machines(company_code)
    tickets = tickets_repo.get_company_tickets(company_code)

    if supervisor_id:
        machines = [m for m in machines if m.get("supervisor_id") == supervisor_id]
        machine_ids = {m["machine_id"] for m in machines}
        tickets = [t for t in tickets if t.get("machine_id") in machine_ids]

    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    # ---- Objective server-side counts per machine --------------------------------
    open_by_machine: dict = {}
    tickets_30d_by_machine: dict = {}
    for t in tickets:
        mid = t.get("machine_id", "")
        if t.get("status") == "Open":
            open_by_machine[mid] = open_by_machine.get(mid, 0) + 1
        ts = _parse_dt(t.get("reported_at"))
        if ts and ts >= thirty_days_ago:
            tickets_30d_by_machine[mid] = tickets_30d_by_machine.get(mid, 0) + 1

    # ---- Per-machine risk tiers --------------------------------------------------
    machine_risk_map: dict = {}
    for m in machines:
        mid = m["machine_id"]
        machine_risk_map[mid] = _machine_risk(
            open_by_machine.get(mid, 0),
            tickets_30d_by_machine.get(mid, 0),
            m.get("last_activity_at", ""),
            now,
        )

    # ---- Fleet KPIs --------------------------------------------------------------
    open_tickets = sum(open_by_machine.values())
    closed_today = sum(
        1 for t in tickets
        if t.get("status") == "Closed"
        and _parse_dt(t.get("closed_at")) is not None
        and _parse_dt(t["closed_at"]).date() == now.date()
    )
    # machines_down: objective server-computed, never the formula column
    machines_down = sum(1 for m in machines if open_by_machine.get(m["machine_id"], 0) > 0)
    stale_machines = sum(1 for r in machine_risk_map.values() if r == "stale")
    high_risk_machines = sum(1 for r in machine_risk_map.values() if r == "high")
    total_tickets = len(tickets)
    total_machines = len(machines)

    # plant_health: unhealthy = actively broken OR unknown (stale)
    unhealthy = machines_down + stale_machines
    plant_health = (
        100 if total_machines == 0
        else max(0, int((total_machines - unhealthy) / total_machines * 100))
    )

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

    # Open tickets needing action — most urgent first, oldest within same urgency
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
    today = now.date()
    this_week_start = today - timedelta(days=today.weekday())
    week_starts = [this_week_start - timedelta(weeks=i) for i in range(5, -1, -1)]
    week_counts = {ws: 0 for ws in week_starts}
    for t in tickets:
        parsed = _parse_dt(t.get("reported_at"))
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
            "stale_machines": stale_machines,
            "high_risk_machines": high_risk_machines,
        },
        "auto_insights": compute_auto_insights(tickets, machines),
        "machine_risk_map": machine_risk_map,
        "recent_activity": recent,
        "needs_attention": needs_attention,
        "weekly_trend": weekly_trend,
    }


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def compute_auto_insights(tickets: List[dict], machines: List[dict]) -> dict:
    """Derive MTBF, MTTR, repeat breakdown %, and top problem machines from ticket data."""
    closed = [t for t in tickets if t.get("status") == "Closed"]

    # MTTR — mean time to repair (hours), from closed tickets with hours_to_fix
    mttr_values = []
    for t in closed:
        try:
            h = float(t.get("hours_to_fix", 0))
            if h > 0:
                mttr_values.append(h)
        except (ValueError, TypeError):
            pass
    mttr = round(sum(mttr_values) / len(mttr_values), 1) if mttr_values else 0

    # MTBF — mean time between failures per machine (hours)
    machine_tickets: dict[str, list[datetime]] = {}
    for t in tickets:
        mid = t.get("machine_id", "")
        dt = _parse_dt(t.get("reported_at"))
        if mid and dt:
            machine_tickets.setdefault(mid, []).append(dt)

    mtbf_intervals = []
    for mid, times in machine_tickets.items():
        times.sort()
        for i in range(1, len(times)):
            gap = (times[i] - times[i - 1]).total_seconds() / 3600
            if gap > 0.5:
                mtbf_intervals.append(gap)
    mtbf = round(sum(mtbf_intervals) / len(mtbf_intervals), 1) if mtbf_intervals else 0

    # Repeat breakdown % — machines with 3+ tickets in last 30 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent_by_machine: dict[str, int] = Counter()
    for t in tickets:
        dt = _parse_dt(t.get("reported_at"))
        if dt and dt >= cutoff:
            recent_by_machine[t.get("machine_id", "")] += 1
    total_with_tickets = len(recent_by_machine)
    repeaters = sum(1 for c in recent_by_machine.values() if c >= 3)
    repeat_pct = round(repeaters / total_with_tickets * 100) if total_with_tickets else 0

    # Top problem machines — most tickets in last 30 days
    top_machines = sorted(recent_by_machine.items(), key=lambda x: x[1], reverse=True)[:3]
    machine_names = {m.get("machine_id", ""): m.get("machine_name", "") for m in machines}
    top_problem = [
        {"machine_id": mid, "machine_name": machine_names.get(mid, mid), "ticket_count": cnt}
        for mid, cnt in top_machines
    ]

    # First response time (avg hours from reported_at to first status change)
    # Approximated as MTTR for now since we don't track intermediate status changes

    return {
        "mtbf_hours": mtbf,
        "mttr_hours": mttr,
        "repeat_breakdown_pct": repeat_pct,
        "top_problem_machines": top_problem,
    }


def build_custom_kpi_values(
    company_code: str,
    kpi_configs: List[dict],
    kpi_data: List[dict],
    auto_insights: dict,
    base_kpis: dict,
) -> List[dict]:
    """Build the final custom KPI tile values for the dashboard."""
    results = []
    for cfg in kpi_configs:
        kpi_id = cfg.get("kpi_id", "")
        kpi_type = cfg.get("kpi_type", "manual")
        unit = cfg.get("unit", "")
        target = cfg.get("target_value", "")
        warning_th = cfg.get("warning_threshold", "")
        critical_th = cfg.get("critical_threshold", "")

        value = ""
        status = "normal"

        if kpi_type == "calc" and cfg.get("kpi_name", "").lower().startswith("downtime cost"):
            cost_rate = _safe_float(cfg.get("cost_per_hour", 0))
            hours_lost = base_kpis.get("avg_hours_to_fix", 0) * base_kpis.get("open_tickets", 0)
            total = cost_rate * hours_lost
            value = f"Rs {total:,.0f}"
        elif kpi_type == "auto":
            name_lower = cfg.get("kpi_name", "").lower()
            if "mtbf" in name_lower:
                value = f"{auto_insights.get('mtbf_hours', 0)} hrs"
            elif "mttr" in name_lower:
                value = f"{auto_insights.get('mttr_hours', 0)} hrs"
            elif "repeat" in name_lower:
                value = f"{auto_insights.get('repeat_breakdown_pct', 0)}%"
        else:
            entries = [d for d in kpi_data if d.get("kpi_id") == kpi_id]
            if entries:
                value = f"{entries[0].get('value', '')} {unit}".strip()
            else:
                value = "—"

        if critical_th and value != "—":
            num_val = _safe_float(value.replace("Rs", "").replace(",", "").replace("%", "").replace("hrs", "").strip())
            if num_val and _safe_float(critical_th) and num_val >= _safe_float(critical_th):
                status = "critical"
            elif warning_th and _safe_float(warning_th) and num_val >= _safe_float(warning_th):
                status = "warning"

        results.append({
            "kpi_id": kpi_id,
            "kpi_name": cfg.get("kpi_name", ""),
            "kpi_type": kpi_type,
            "value": value,
            "unit": unit,
            "target": target,
            "status": status,
        })
    return results


def _safe_float(val) -> float:
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0
