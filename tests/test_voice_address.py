from whisper_key.voice_address import Addressed, compose, parse_address


def test_prefixes_in_german_and_english():
    assert parse_address("Pane 2: fix the login test") == Addressed("2", "fix the login test", explicit=True)
    assert parse_address("pane zwei, bitte die Tests laufen lassen.") == Addressed("zwei", "bitte die Tests laufen lassen.", explicit=True)
    assert parse_address("An Backend: Tests laufen lassen") == Addressed("Backend", "Tests laufen lassen")
    assert parse_address("To Müller Tests - check the build") == Addressed("Müller Tests", "check the build")


def test_trailing_send_word():
    assert parse_address("Pane 3: Tests laufen lassen, senden.") == Addressed("3", "Tests laufen lassen", True, True)
    assert parse_address("An Backend: run the tests. Send.") == Addressed("Backend", "run the tests", True)


def test_send_alone_is_not_a_body():
    assert parse_address("Pane 3: senden") == Addressed("3", "senden", explicit=True)


def test_plain_dictation_is_untouched():
    for text in ("Bitte die Tests laufen lassen", "An dieser Stelle fehlt ein Test", "Pane", "Pane 2:"):
        assert parse_address(text) == Addressed(None, text)


def test_overlong_target_is_not_an_address():
    text = "An " + "x" * 60 + ": hallo"
    assert parse_address(text).target is None


def test_compose_roundtrip():
    for text in ("Pane 2: fix it", "Pane Backend: run it senden", "An Backend: run it"):
        assert parse_address(compose(parse_address(text))) == parse_address(text)
    assert compose(Addressed(None, "plain")) == "plain"
