from whisper_key.voice_address import Addressed, compose, parse_address


def fields(text):
    a = parse_address(text)
    return a.target, a.body, a.send, a.explicit


def test_prefixes_in_german_and_english():
    assert fields("Pane 2: fix the login test") == ("2", "fix the login test", False, True)
    assert fields("pane zwei, bitte die Tests laufen lassen.") == ("zwei", "bitte die Tests laufen lassen.", False, True)
    assert fields("An Backend: Tests laufen lassen") == ("Backend", "Tests laufen lassen", False, False)
    assert fields("To Müller Tests - check the build") == ("Müller Tests", "check the build", False, False)


def test_trailing_send_phrase_after_punctuation():
    assert fields("Pane 3: Tests laufen lassen, senden.") == ("3", "Tests laufen lassen", True, True)
    assert fields("An Backend: run the tests. Send.") == ("Backend", "run the tests", True, False)
    assert fields("Pane 3: Tests laufen lassen, und senden") == ("3", "Tests laufen lassen", True, True)


def test_verb_at_the_end_is_part_of_the_sentence():
    assert fields("Pane 2: die Funktion soll die Mail senden") == ("2", "die Funktion soll die Mail senden", False, True)
    assert fields("Pane 2: add a method to send") == ("2", "add a method to send", False, True)


def test_send_alone_is_not_a_body():
    assert fields("Pane 3: senden") == ("3", "senden", False, True)


def test_plain_dictation_is_untouched():
    for text in ("Bitte die Tests laufen lassen", "An dieser Stelle fehlt ein Test", "Pane", "Pane 2:"):
        assert parse_address(text) == Addressed(None, text)


def test_overlong_target_is_not_an_address():
    text = "An " + "x" * 60 + ": hallo"
    assert parse_address(text).target is None


def test_compose_replaces_only_the_body():
    for text in ("Pane 2: fix it", "Pane Backend: run it, senden.", "To be honest, das passt so", "An der Stelle, fehlt ein Test"):
        assert compose(parse_address(text)) == text
    addressed = parse_address("To be honest, das passt so")
    addressed.body = "Das passt."
    assert compose(addressed) == "To be honest, Das passt."
    assert compose(Addressed(None, "plain")) == "plain"
