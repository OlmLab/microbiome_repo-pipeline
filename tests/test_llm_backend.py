import csv
import pytest
from catalog import llm_backend as LB


class FakeHost:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0
    def list_models(self): return ["claude-sonnet-x", "claude-haiku-x"]
    def reasoning_model(self): return "claude-sonnet-x"
    def current_model(self): return "claude-sonnet-x"
    def llm(self, reqs, max_concurrency=8):
        out = []
        for r in reqs:
            self.calls += 1
            assert not any(k.startswith("_") for k in r)
            out.append(self.answers.pop(0))
        return out


def ok(text="yes"):
    return {"text": text, "stop_reason": "end_turn", "usage": {"input_tokens": 1000, "output_tokens": 100}}


def test_cache_ledger_and_no_second_payment(tmp_path):
    h = FakeHost([ok("a"), ok("b")])
    llm = LB.LLM("rubric", cache_dir=str(tmp_path / "c"), host=h, backend="host", pass_name="t")
    r = llm([{"prompt": "x"}, {"prompt": "y"}])
    assert [x["text"] for x in r] == ["a", "b"] and h.calls == 2
    r2 = llm([{"prompt": "x"}, {"prompt": "y"}])
    assert h.calls == 2 and all(x["status"] == "cached" for x in r2)
    rows = list(csv.DictReader(open(tmp_path / "c" / "ledger.csv")))
    assert len(rows) == 2 and abs(sum(float(x["usd"]) for x in rows) - 2 * (1000 * 2 + 100 * 10) / 1e6) < 1e-9


def test_refusal_recorded_not_retried(tmp_path):
    h = FakeHost([{"stop_reason": "refusal", "text": "", "usage": {}}])
    llm = LB.LLM("rubric", cache_dir=str(tmp_path / "c"), host=h, backend="host")
    r = llm([{"prompt": "x"}])
    assert r[0]["status"] == "refused" and h.calls == 1
    assert llm([{"prompt": "x"}])[0]["status_original"] == "refused" and h.calls == 1


def test_empty_answer_retried_once_with_more_tokens(tmp_path):
    h = FakeHost([{"text": "", "stop_reason": "max_tokens", "usage": {}}, ok("late")])
    llm = LB.LLM("rubric", cache_dir=str(tmp_path / "c"), host=h, backend="host")
    r = llm([{"prompt": "x", "max_tokens": 100}])
    assert r[0]["text"] == "late" and h.calls == 2


def test_budget_stops_new_calls(tmp_path):
    h = FakeHost([ok()] * 10)
    llm = LB.LLM("rubric", cache_dir=str(tmp_path / "c"), host=h, backend="host", budget_usd=0.001, max_concurrency=1)
    with pytest.raises(LB.BudgetExceeded):
        llm([{"prompt": str(i)} for i in range(10)], chunk=1)
    assert h.calls == 1


def test_transient_error_not_cached(tmp_path):
    h = FakeHost([{"error": "HTTP 529 overloaded"}, ok("fine")])
    llm = LB.LLM("rubric", cache_dir=str(tmp_path / "c"), host=h, backend="host")
    assert llm([{"prompt": "x"}])[0]["status"] == "error"
    assert llm([{"prompt": "x"}])[0]["text"] == "fine"


class _Obj:
    def __init__(self, **k): self.__dict__.update(k)
    def model_dump(self): return {k: v for k, v in self.__dict__.items()}


class FakeClient:
    """Mimics anthropic.Anthropic().messages(.batches) closely enough for the backend."""
    def __init__(self):
        self.created = []
        outer = self
        class Batches:
            def create(self, requests):
                outer.created.append(requests); return _Obj(id="b1", processing_status="in_progress")
            def retrieve(self, bid): return _Obj(id=bid, processing_status="ended")
            def results(self, bid):
                for r in outer.created[-1]:
                    msg = _Obj(content=[{"type": "tool_use", "input": {"echo": r["params"]["messages"][0]["content"]}}],
                               stop_reason="tool_use", usage={"input_tokens": 2000, "output_tokens": 0}, model=r["params"]["model"])
                    yield _Obj(custom_id=r["custom_id"], result=_Obj(type="succeeded", message=msg))
        class Messages:
            batches = Batches()
            def create(self, **p): raise AssertionError("batch mode must not call messages.create")
        self.messages = Messages()


def test_anthropic_batch_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOG_MODEL_RUBRIC", "claude-sonnet-test")
    c = FakeClient()
    llm = LB.LLM("rubric", cache_dir=str(tmp_path / "c"), backend="anthropic", batch=True, client=c, host=None)
    r = llm([{"prompt": "p1"}, {"prompt": "p2"}])
    assert [x["tool_use"]["echo"] for x in r] == ["p1", "p2"]
    assert all(q["params"]["model"] == "claude-sonnet-test" for q in c.created[0])
    rows = list(csv.DictReader(open(tmp_path / "c" / "ledger.csv")))
    assert abs(float(rows[0]["usd"]) - 0.5 * 2000 * 2 / 1e6) < 1e-9      # batch = half price


def test_substituted_model_not_committed_and_not_resent(tmp_path):
    sub = dict(ok("answer"), model="claude-haiku-x")          # requested rubric role resolves to sonnet
    h = FakeHost([sub])
    llm = LB.LLM("rubric", cache_dir=str(tmp_path / "c"), host=h, backend="host", pass_name="t")
    r = llm([{"prompt": "x"}])
    assert r[0]["status"] == "model_substituted" and r[0]["model_requested"] != r[0]["model"]
    r2 = llm([{"prompt": "x"}])                                  # cached, read back with its own status, not 'cached'
    assert r2[0]["status"] == "model_substituted" and h.calls == 1
