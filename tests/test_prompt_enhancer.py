import json

from whisper_key.prompt_enhancer import PromptEnhancer, is_loopback_url

TEXT = "also bitte äh mach mal den Test für die Datei foo.py"


def reply(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


def recorder(answer):
    calls = []

    def post(url, body, timeout):
        calls.append((url, json.loads(body), timeout))
        if isinstance(answer, Exception):
            raise answer
        return answer
    return post, calls


def test_disabled_by_default_and_never_posts():
    post, calls = recorder(reply("x"))
    assert PromptEnhancer(poster=post).enhance(TEXT) == TEXT
    assert calls == []


def test_enhances_via_chat_completion():
    post, calls = recorder(reply("  Schreibe einen Test für foo.py.\n"))
    enhancer = PromptEnhancer(enabled=True, model="qwen", timeout=5, poster=post)
    assert enhancer.enhance(TEXT) == "Schreibe einen Test für foo.py."
    url, body, timeout = calls[0]
    assert url == "http://127.0.0.1:8080/v1/chat/completions"
    assert timeout == 5.0
    assert body["model"] == "qwen"
    assert body["messages"][1] == {"role": "user", "content": TEXT}


def test_failures_keep_original_text():
    for answer in (OSError("refused"), b"not json", reply(""), reply(None),
                   json.dumps({"choices": []}).encode()):
        post, _ = recorder(answer)
        assert PromptEnhancer(enabled=True, poster=post).enhance(TEXT) == TEXT


def test_short_text_is_left_alone():
    post, calls = recorder(reply("x"))
    assert PromptEnhancer(enabled=True, poster=post).enhance("ja bitte") == "ja bitte"
    assert calls == []


def test_non_loopback_url_disables():
    post, calls = recorder(reply("x"))
    enhancer = PromptEnhancer(enabled=True, url="http://example.com/v1/chat/completions", poster=post)
    assert not enhancer.enabled
    assert enhancer.enhance(TEXT) == TEXT
    assert calls == []


def test_loopback_check():
    assert is_loopback_url("http://localhost:8080/v1/chat/completions")
    assert is_loopback_url("http://[::1]:8080/x")
    assert not is_loopback_url("https://127.0.0.1/x")
    assert not is_loopback_url("http://127.0.0.1.evil.com/x")
    assert not is_loopback_url("file:///etc/passwd")


def test_think_block_is_stripped_and_unclosed_rejected():
    post, _ = recorder(reply("<think>\nhmm, the user wants\n</think>\n\nSchreibe Tests für foo.py."))
    assert PromptEnhancer(enabled=True, poster=post).enhance(TEXT) == "Schreibe Tests für foo.py."
    post, _ = recorder(reply("<think>still thinking"))
    assert PromptEnhancer(enabled=True, poster=post).enhance(TEXT) == TEXT


def test_overlong_result_keeps_original_and_max_tokens_is_bounded():
    post, calls = recorder(reply("word " * 2000))
    assert PromptEnhancer(enabled=True, poster=post).enhance(TEXT) == TEXT
    assert len(TEXT) < calls[0][1]["max_tokens"] <= len(TEXT) + 1024


def test_string_false_stays_disabled():
    assert not PromptEnhancer(enabled="false").enabled
    assert PromptEnhancer(enabled="true").enabled
