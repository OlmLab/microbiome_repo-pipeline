"""llm_backend.py — one entry point for every model call in the pipeline (R2026.30 large update, monthly runs).

    from catalog.llm_backend import LLM
    llm = LLM(role="rubric", cache_dir="~/catalog/cache/llm/<pass>", ledger="~/catalog/cache/llm/ledger.csv",
              budget_usd=250, backend="auto")
    results = llm(requests)          # same list-in / list-out shape as host.llm

Why: the 2026-10 passes lost paid results on kernel resets, hit the 2.0 M-token per-frame ceiling of host.llm, and ran
as dozens of sub-agents whose orchestration cost more than the model calls. This module
  * caches every response on disk under a content hash of the request (a re-run never pays twice);
  * appends one ledger row per paid call (role, model, tokens, estimated $) and refuses to start new calls once the
    pass budget is spent (BudgetExceeded);
  * records refusals (stop_reason 'refusal' / content-filter errors) as status 'refused' and NEVER retries them;
  * retries an empty answer (stop_reason max_tokens with no text/tool output) once with doubled max_tokens;
  * runs on either backend with the same requests:
      backend="host"       host.llm inside a Claude Science kernel (default when a host object is present)
      backend="anthropic"  the Anthropic Messages API with ANTHROPIC_API_KEY; batch=True uses the Message Batches API
                           (asynchronous, priced at half the standard rate), for the large update and monthly runs.
Model ids are never literals here: they come from catalog.models.resolve_model(role) (CATALOG_MODEL_<ROLE> outside a
kernel). Prices for the ledger estimate live in config/prices.yaml (role family -> $ per million tokens).

Request dicts follow host.llm: {"prompt": str} or {"messages": [...]}, optional "system", "tools", "tool_choice",
"max_tokens", "thinking". Each result is a dict with text, tool_use (first tool input or None), stop_reason, usage,
model, status in {"ok", "cached", "refused", "error", "empty"}.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
from typing import Any

from catalog import models as M

PRICE_DEFAULT = {"haiku": (1.0, 5.0, 0.10), "sonnet": (2.0, 10.0, 0.20), "opus": (4.0, 20.0, 0.20)}


class BudgetExceeded(RuntimeError):
    pass


def _load_prices(path: str | None = None) -> dict:
    path = path or os.path.join(os.path.dirname(__file__), "..", "..", "config", "prices.yaml")
    out = dict(PRICE_DEFAULT)
    if os.path.exists(path):
        for line in open(path):
            m = re.match(r"\s*(\w+)\s*:\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\]", line)
            if m:
                out[m.group(1)] = tuple(float(x) for x in m.groups()[1:])
    return out


def request_key(req: dict, model: str) -> str:
    body = {k: req.get(k) for k in ("system", "prompt", "messages", "tools", "tool_choice", "max_tokens", "thinking")}
    return hashlib.sha256(json.dumps([model, body], sort_keys=True, default=str).encode()).hexdigest()[:40]


def _first_tool(content) -> Any:
    for b in content or []:
        if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "tool_use":
            return b.get("input") if isinstance(b, dict) else getattr(b, "input", None)
    return None


def _normalise(raw: dict, model: str) -> dict:
    content = raw.get("content") or []
    text = raw.get("text")
    if text is None:
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    tool = raw.get("tool_use")
    if isinstance(tool, dict) and "input" in tool:
        tool = tool["input"]
    if tool is None:
        tool = _first_tool(content)
    stop = raw.get("stop_reason")
    err = raw.get("error")
    if stop == "refusal" or (err and re.search(r"refus|content (safety|filter)", str(err), re.I)):
        status = "refused"
    elif err:
        status = "error"
    elif not (text or "").strip() and tool is None:
        status = "empty"
    else:
        status = "ok"
    got = raw.get("model") or model
    if status == "ok" and _family(got) != _family(model):
        # answered by a different model tier than requested (e.g. a provider fallback for sensitive content): never committed,
        # cached like a refusal so it is not silently re-sent, and listed in the pass report
        status = "model_substituted"
    return {"text": text or "", "tool_use": tool, "stop_reason": stop, "usage": raw.get("usage") or {},
            "model": got, "model_requested": model, "status": status, "error": str(err) if err else None}


def _family(model: str) -> str:
    m = (model or "").lower()
    return next((f for f in ("haiku", "sonnet", "opus") if f in m), m)


class LLM:
    def __init__(self, role: str, cache_dir: str, ledger: str | None = None, budget_usd: float | None = None,
                 backend: str = "auto", batch: bool = False, host: Any = None, client: Any = None,
                 pass_name: str | None = None, max_concurrency: int = 8):
        self.role, self.batch, self.max_concurrency = role, batch, max_concurrency
        self.cache_dir = os.path.expanduser(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)
        self.ledger = os.path.expanduser(ledger) if ledger else os.path.join(self.cache_dir, "ledger.csv")
        self.budget = budget_usd
        self.pass_name = pass_name or os.path.basename(self.cache_dir.rstrip("/"))
        self.host = host if host is not None else M._host()
        if backend == "auto":
            backend = "host" if self.host is not None else "anthropic"
        self.backend = backend
        self.client = client
        if backend == "anthropic" and client is None:
            import anthropic  # noqa: PLC0415 — optional dependency, only for the API backend
            self.client = anthropic.Anthropic()
        self.model = M.resolve_model(role, host=self.host if backend == "host" else None) if backend == "host" \
            else os.environ.get(f"CATALOG_MODEL_{role.upper()}") or M.resolve_model(role)
        fam = next((f for f in ("haiku", "sonnet", "opus") if f in self.model.lower()), "sonnet")
        self.price = _load_prices()[fam]
        self.spent = self._spent_so_far()

    # ------------------------------------------------------------------ ledger / budget
    def _spent_so_far(self) -> float:
        if not os.path.exists(self.ledger):
            return 0.0
        tot = 0.0
        for r in csv.DictReader(open(self.ledger)):
            if r.get("pass") == self.pass_name:
                tot += float(r.get("usd") or 0)
        return tot

    def cost(self, usage: dict) -> float:
        pin, pout, pcache = self.price
        inp = float(usage.get("input_tokens", 0) or 0)
        cr = float(usage.get("cache_read_input_tokens", 0) or 0)
        cw = float(usage.get("cache_creation_input_tokens", 0) or 0)
        out = float(usage.get("output_tokens", 0) or 0)
        usd = (inp * pin + cw * pin * 1.25 + cr * pcache + out * pout) / 1e6
        return usd * (0.5 if (self.backend == "anthropic" and self.batch) else 1.0)

    def _log(self, key: str, res: dict):
        new = not os.path.exists(self.ledger)
        usd = self.cost(res["usage"])
        self.spent += usd
        with open(self.ledger, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["time", "pass", "role", "backend", "model", "key", "status", "input_tokens", "output_tokens",
                            "cache_read", "cache_write", "usd"])
            u = res["usage"]
            w.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"), self.pass_name, self.role, self.backend, res["model"], key,
                        res["status"], u.get("input_tokens", 0), u.get("output_tokens", 0),
                        u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0), round(usd, 6)])

    # ------------------------------------------------------------------ cache
    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, key[:2], key + ".json")

    def _get(self, key: str):
        p = self._path(key)
        if os.path.exists(p):
            r = json.load(open(p))
            # only a good answer reads back as 'cached'; a cached refusal / substitution / empty keeps its own status,
            # so callers that accept ("ok", "cached") can never take it as an answer
            r["status_original"] = r.get("status")
            r["status"] = "cached" if r["status_original"] == "ok" else r["status_original"]
            return r
        return None

    def _put(self, key: str, res: dict):
        p = self._path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        json.dump(res, open(tmp, "w"))
        os.replace(tmp, p)

    # ------------------------------------------------------------------ backends
    def _host_call(self, reqs: list[dict]) -> list[dict]:
        clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in reqs]
        out = self.host.llm([dict(r, model=self.model) for r in clean], max_concurrency=self.max_concurrency)
        return [o if isinstance(o, dict) else {"error": repr(o)} for o in out]

    def _api_params(self, r: dict) -> dict:
        p = {"model": self.model, "max_tokens": int(r.get("max_tokens") or 4096),
             "messages": r.get("messages") or [{"role": "user", "content": r["prompt"]}]}
        for k in ("system", "tools", "tool_choice", "thinking"):
            if r.get(k) is not None:
                p[k] = r[k]
        return p

    def _api_call(self, reqs: list[dict]) -> list[dict]:
        def one(r):
            try:
                m = self.client.messages.create(**self._api_params(r))
                d = m.model_dump() if hasattr(m, "model_dump") else dict(m)
                return d
            except Exception as e:  # noqa: BLE001 — recorded, not raised
                return {"error": repr(e)}
        if not self.batch:
            from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
            with ThreadPoolExecutor(self.max_concurrency) as ex:
                return list(ex.map(one, reqs))
        ids = [f"r{i}" for i in range(len(reqs))]
        b = self.client.messages.batches.create(requests=[{"custom_id": i, "params": self._api_params(r)} for i, r in zip(ids, reqs)])
        while True:
            b = self.client.messages.batches.retrieve(b.id)
            if getattr(b, "processing_status", None) == "ended":
                break
            time.sleep(30)
        got = {}
        for item in self.client.messages.batches.results(b.id):
            res = item.result
            if getattr(res, "type", None) == "succeeded":
                msg = res.message
                got[item.custom_id] = msg.model_dump() if hasattr(msg, "model_dump") else dict(msg)
            else:
                got[item.custom_id] = {"error": f"{getattr(res, 'type', 'error')}: {getattr(res, 'error', '')}"}
        return [got.get(i, {"error": "missing from batch results"}) for i in ids]

    # ------------------------------------------------------------------ main entry
    def __call__(self, requests: list[dict], chunk: int | None = None) -> list[dict]:
        keys = [request_key(r, self.model) for r in requests]
        results: list[dict | None] = [self._get(k) for k in keys]
        todo = [i for i, r in enumerate(results) if r is None]
        chunk = chunk or (10000 if self.batch else self.max_concurrency * 4)
        call = self._host_call if self.backend == "host" else self._api_call
        for s in range(0, len(todo), chunk):
            if self.budget is not None and self.spent >= self.budget:
                raise BudgetExceeded(f"pass {self.pass_name}: ${self.spent:.2f} spent of ${self.budget:.2f}; "
                                     f"{len(todo) - s} requests not sent (cached results are kept)")
            idx = todo[s:s + chunk]
            raws = call([requests[i] for i in idx])
            retry = []
            for i, raw in zip(idx, raws):
                res = _normalise(raw, self.model)
                self._log(keys[i], res)
                if res["status"] == "empty" and not requests[i].get("_retried"):
                    retry.append(i)
                    continue
                if res["status"] != "error":          # transient errors are not cached, so a re-run tries again
                    self._put(keys[i], res)
                results[i] = res
            if retry:
                rr = [dict(requests[i], max_tokens=2 * int(requests[i].get("max_tokens") or 4096), _retried=True) for i in retry]
                for i, raw in zip(retry, call(rr)):
                    res = _normalise(raw, self.model)
                    self._log(keys[i], res)
                    if res["status"] != "error":
                        self._put(keys[i], res)
                    results[i] = res
        return results  # type: ignore[return-value]

    def summary(self) -> dict:
        return {"pass": self.pass_name, "backend": self.backend, "model": self.model, "spent_usd": round(self.spent, 2),
                "budget_usd": self.budget}
