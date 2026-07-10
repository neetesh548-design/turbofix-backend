"""Tests for the four system-hardening additions:

1. Health score reacts to tickets (risk tiers: low / medium / high)
2. Stale machine detection (last_activity_at gap)
3. Objective KPI signals (machines_down from server-computed counts)
4. Approval escalation fires email for overdue registrations
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.services.dashboard_service import compute_kpis, _machine_risk, _parse_dt
from app.services.escalation_service import run_escalation_check


# ---------------------------------------------------------------------------
# 1. Unit tests for _machine_risk
# ---------------------------------------------------------------------------

class TestMachineRisk:
    NOW = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    FRESH = (NOW - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")   # 5 days ago
    STALE = (NOW - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")  # 40 days ago

    def test_low_risk(self):
        risk = _machine_risk(0, 0, self.FRESH, self.NOW)
        assert risk == "low"

    def test_medium_risk_when_tickets_in_30d(self):
        risk = _machine_risk(0, 2, self.FRESH, self.NOW)
        assert risk == "medium"

    def test_high_risk_when_open_ticket(self):
        risk = _machine_risk(1, 0, self.FRESH, self.NOW)
        assert risk == "high"

    def test_high_risk_when_many_tickets(self):
        risk = _machine_risk(0, 5, self.FRESH, self.NOW)
        assert risk == "high"

    def test_stale_when_no_activity_field(self):
        risk = _machine_risk(0, 0, "", self.NOW)
        assert risk == "stale"

    def test_stale_when_old_timestamp(self):
        risk = _machine_risk(0, 0, self.STALE, self.NOW)
        assert risk == "stale"

    def test_stale_overrides_open_ticket(self):
        """Even if there's an open ticket, stale wins — data integrity above all."""
        risk = _machine_risk(1, 3, self.STALE, self.NOW)
        assert risk == "stale"


# ---------------------------------------------------------------------------
# 2. compute_kpis integration tests (using mock repos)
# ---------------------------------------------------------------------------

def _make_machine(mid, company="TEST", supervisor_id="", last_activity_at=""):
    return {
        "machine_id": mid,
        "company_code": company,
        "machine_name": f"Machine {mid}",
        "location": "",
        "supervisor_id": supervisor_id,
        "last_activity_at": last_activity_at,
        "has_open_tickets": False,
    }

def _make_ticket(mid, status="Open", reported_at=None, closed_at=None):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return {
        "ticket_id": f"T-{mid}",
        "machine_id": mid,
        "company_code": "TEST",
        "machine_name": f"Machine {mid}",
        "reported_at": reported_at or now_str,
        "status": status,
        "urgency": "Medium",
        "closed_at": closed_at or "",
        "hours_to_fix": "2" if status == "Closed" else "",
        "description": "test",
        "ai_summary": "",
        "language": "",
        "closed_by": "",
    }


class TestComputeKpis:
    def _run(self, machines, tickets):
        m_repo = MagicMock()
        m_repo.get_company_machines.return_value = machines
        t_repo = MagicMock()
        t_repo.get_company_tickets.return_value = tickets
        return compute_kpis("TEST", "Test Co", t_repo, m_repo)

    def test_machines_down_uses_server_computed_tickets(self):
        """machines_down should be derived from ticket counts, not has_open_tickets column."""
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        machines = [_make_machine("M001", last_activity_at=fresh)]
        tickets = [_make_ticket("M001", status="Open")]
        result = self._run(machines, tickets)
        assert result["kpis"]["machines_down"] == 1

    def test_machines_down_zero_when_no_open_tickets(self):
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        machines = [_make_machine("M001", last_activity_at=fresh)]
        tickets = [_make_ticket("M001", status="Closed")]
        result = self._run(machines, tickets)
        assert result["kpis"]["machines_down"] == 0

    def test_risk_map_present_in_response(self):
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        machines = [_make_machine("M001", last_activity_at=fresh)]
        result = self._run(machines, [])
        assert "machine_risk_map" in result
        assert "M001" in result["machine_risk_map"]

    def test_stale_machine_counted(self):
        """Machine with no last_activity_at → stale_machines incremented."""
        machines = [_make_machine("M001", last_activity_at="")]  # blank = stale
        result = self._run(machines, [])
        assert result["kpis"]["stale_machines"] == 1

    def test_stale_reduces_plant_health(self):
        """Plant health must NOT show 100% when there are stale machines."""
        machines = [_make_machine("M001", last_activity_at="")]
        result = self._run(machines, [])
        assert result["kpis"]["plant_health_pct"] < 100

    def test_high_risk_machine_counted(self):
        fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        machines = [_make_machine("M001", last_activity_at=fresh)]
        tickets = [_make_ticket("M001", status="Open")]
        result = self._run(machines, tickets)
        assert result["kpis"]["high_risk_machines"] == 1

    def test_new_kpi_fields_present(self):
        result = self._run([], [])
        for field in ("stale_machines", "high_risk_machines"):
            assert field in result["kpis"], f"Missing KPI field: {field}"


# ---------------------------------------------------------------------------
# 3. Approval escalation tests
# ---------------------------------------------------------------------------

class TestEscalation:
    def _make_company(self, code, approved="no", hours_ago=25, name=None):
        ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "company_code": code,
            "company_name": name or f"Company {code}",
            "approved": approved,
            "registered_at": ts,
        }

    def test_escalation_fires_for_overdue_company(self, monkeypatch):
        from app import config, email_client
        monkeypatch.setattr(config, "APPROVAL_ESCALATION_HOURS", 24)
        monkeypatch.setattr(config, "PLATFORM_ADMIN_EMAIL", "admin@test.local")

        sent = []
        monkeypatch.setattr(email_client, "send_email", lambda to, subject, body: sent.append(to))

        # Clear in-memory notification set
        from app.services import escalation_service
        escalation_service._already_notified.clear()

        repo = MagicMock()
        repo.list_companies.return_value = [self._make_company("LATE1", hours_ago=26)]

        escalated = run_escalation_check(repo)
        assert "LATE1" in escalated
        assert len(sent) == 1
        assert sent[0] == "admin@test.local"

    def test_escalation_skips_approved_company(self, monkeypatch):
        from app import config, email_client
        monkeypatch.setattr(config, "APPROVAL_ESCALATION_HOURS", 24)

        sent = []
        monkeypatch.setattr(email_client, "send_email", lambda to, subject, body: sent.append(to))

        from app.services import escalation_service
        escalation_service._already_notified.clear()

        repo = MagicMock()
        repo.list_companies.return_value = [self._make_company("GOOD1", approved="yes", hours_ago=26)]

        escalated = run_escalation_check(repo)
        assert escalated == []
        assert sent == []

    def test_escalation_skips_recent_company(self, monkeypatch):
        from app import config, email_client
        monkeypatch.setattr(config, "APPROVAL_ESCALATION_HOURS", 24)

        sent = []
        monkeypatch.setattr(email_client, "send_email", lambda to, subject, body: sent.append(to))

        from app.services import escalation_service
        escalation_service._already_notified.clear()

        repo = MagicMock()
        repo.list_companies.return_value = [self._make_company("NEW1", hours_ago=2)]

        escalated = run_escalation_check(repo)
        assert escalated == []

    def test_escalation_not_sent_twice(self, monkeypatch):
        """Same company should not get a second email in the same server lifecycle."""
        from app import config, email_client
        monkeypatch.setattr(config, "APPROVAL_ESCALATION_HOURS", 24)
        monkeypatch.setattr(config, "PLATFORM_ADMIN_EMAIL", "admin@test.local")

        sent = []
        monkeypatch.setattr(email_client, "send_email", lambda to, subject, body: sent.append(to))

        from app.services import escalation_service
        escalation_service._already_notified.clear()

        repo = MagicMock()
        repo.list_companies.return_value = [self._make_company("DUP1", hours_ago=30)]

        run_escalation_check(repo)  # First run — should send
        run_escalation_check(repo)  # Second run — should NOT send again
        assert len(sent) == 1
