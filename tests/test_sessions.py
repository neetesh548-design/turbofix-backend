import time

from app.sessions import SessionStore


def test_open_and_get_within_ttl():
    store = SessionStore(ttl_seconds=60)
    store.open("+919900011111", "T123", "TF-ACME3-M001")
    session = store.get("+919900011111")
    assert session is not None
    assert session.ticket_id == "T123"
    assert session.machine_id == "TF-ACME3-M001"


def test_get_returns_none_for_unknown_phone():
    store = SessionStore(ttl_seconds=60)
    assert store.get("+919900000000") is None


def test_session_expires_after_ttl():
    store = SessionStore(ttl_seconds=0.05)
    store.open("+919900011111", "T123", "TF-ACME3-M001")
    time.sleep(0.1)
    assert store.get("+919900011111") is None


def test_opening_again_overwrites_previous_session():
    store = SessionStore(ttl_seconds=60)
    store.open("+919900011111", "T123", "TF-ACME3-M001")
    store.open("+919900011111", "T124", "TF-ACME3-M002")
    session = store.get("+919900011111")
    assert session.ticket_id == "T124"


def test_sweep_expired_unnotified_returns_and_removes_expired_sessions():
    store = SessionStore(ttl_seconds=0.05)
    store.open("+919900011111", "T123", "TF-ACME3-M001")
    time.sleep(0.1)

    expired = store.sweep_expired_unnotified()

    assert [phone for phone, _ in expired] == ["+919900011111"]
    assert expired[0][1].ticket_id == "T123"
    assert store.get("+919900011111") is None


def test_sweep_expired_unnotified_skips_already_notified_sessions():
    store = SessionStore(ttl_seconds=0.05)
    store.open("+919900011111", "T123", "TF-ACME3-M001")
    store.mark_notified("+919900011111")
    time.sleep(0.1)

    expired = store.sweep_expired_unnotified()

    assert expired == []
    assert store.get("+919900011111") is None  # still cleaned up, just not reported


def test_sweep_expired_unnotified_leaves_active_sessions_alone():
    store = SessionStore(ttl_seconds=60)
    store.open("+919900011111", "T123", "TF-ACME3-M001")

    expired = store.sweep_expired_unnotified()

    assert expired == []
    assert store.get("+919900011111") is not None
