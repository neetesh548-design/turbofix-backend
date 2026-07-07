from app.parser import parse_message


def test_parses_standard_prefilled_message():
    result = parse_message("Issue with TF-ACME3-M042: spindle making loud noise")
    assert result is not None
    assert result.machine_id == "TF-ACME3-M042"
    assert result.company_code == "ACME3"
    assert result.machine_code == "M042"
    assert result.description == "spindle making loud noise"


def test_parses_lowercase_id():
    result = parse_message("issue with tf-acme3-m042: no colon spacing")
    assert result is not None
    assert result.machine_id == "TF-ACME3-M042"


def test_parses_id_without_colon_or_description():
    result = parse_message("TF-BETA1-M001")
    assert result is not None
    assert result.machine_id == "TF-BETA1-M001"
    assert result.description == ""


def test_parses_id_embedded_mid_sentence():
    result = parse_message("hi machine TF-BETA1-M002 is broken again")
    assert result is not None
    assert result.machine_id == "TF-BETA1-M002"
    assert result.description == "is broken again"


def test_returns_none_when_no_machine_id_present():
    assert parse_message("hello is anyone there") is None


def test_returns_none_for_empty_text():
    assert parse_message("") is None
    assert parse_message(None) is None
