from nexus.core.prompt_budget import apply_prompt_budget, prompt_prefix_fingerprint, summarize_text


def test_prompt_prefix_fingerprint_is_stable():
    text = "Hello world\n" * 20
    assert prompt_prefix_fingerprint(text) == prompt_prefix_fingerprint(text)


def test_apply_prompt_budget_summarizes_when_over_limit():
    text = "\n".join([f"line {idx}" for idx in range(200)])
    budget = apply_prompt_budget(text, max_chars=200, summary_max_chars=120)
    assert budget["final_chars"] <= 200
    assert budget["summarized"] is True


def test_summarize_text_returns_bullets():
    out = summarize_text("alpha\nbeta\ngamma", max_chars=80)
    assert out.startswith("Summary:")
    assert "- alpha" in out
