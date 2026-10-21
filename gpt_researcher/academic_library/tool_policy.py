"""One local non-thinking decision; execution is authorized separately.

The service supplies bounded context assembled from trusted session state. This
module never reads evidence, dispatches academic tools, retries, or falls back to
another model. No training package or retained artifact is imported at runtime.
"""

from __future__ import annotations

import json
import math
import re
from urllib.parse import urlsplit

import requests

from .tool_policy_contract import (
    ContractError, PROVIDERS, SYSTEM_V3, ToolPolicyError, canonical,
    parse_response, tools_schema,
)


MODEL_ALIAS = "academic-tools-sft-v1"
DEFAULT_ENDPOINT = "http://127.0.0.1:8091"
MAX_INPUT_BYTES = 128 * 1024
MAX_INPUT_TOKENS = 7808
MAX_OUTPUT_TOKENS = 384
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_GENERATION_TIMEOUT = 600.0
METADATA_TIMEOUT = 5.0
CONTEXT_KEYS = {"mode", "allowed_providers", "allowed_document_ids", "candidates", "existing_results"}
PAPER_FIELDS = {"paper_id": 64, "title": 512, "provider": 64, "doi": 512, "url": 1024}


def _context_list(value, maximum):
    if type(value) is not list or len(value) > maximum:
        raise ToolPolicyError("invalid_context")


def validate_context(context):
    """Validate and copy the small, explicitly supported protocol fields."""
    if (type(context) is not dict or not CONTEXT_KEYS <= set(context)
            or not set(context) <= CONTEXT_KEYS | {"documents"}):
        raise ToolPolicyError("invalid_context")
    if type(context["mode"]) is not str or context["mode"] not in ("local", "web", "hybrid"):
        raise ToolPolicyError("invalid_context")
    for name, maximum in (("allowed_providers", 10), ("allowed_document_ids", 64)):
        values = context[name]
        _context_list(values, maximum)
        if any(type(value) is not str for value in values) or len(set(values)) != len(values):
            raise ToolPolicyError("invalid_context")
        if name == "allowed_providers" and any(value not in PROVIDERS for value in values):
            raise ToolPolicyError("invalid_context")
        if name == "allowed_document_ids" and any(re.fullmatch(r"[0-9a-f]{32}", value) is None for value in values):
            raise ToolPolicyError("invalid_context")
    copied = {key: list(context[key]) for key in ("allowed_providers", "allowed_document_ids")}
    copied["mode"] = context["mode"]
    # Later retained SFT prompts include public document titles for title-based
    # subset selection. Preserve presence, order and text exactly; never expose
    # paths, document bodies, or metadata outside the selected document grants.
    if "documents" in context:
        documents = context["documents"]
        _context_list(documents, 64)
        copied["documents"] = []
        seen_documents = set()
        for document in documents:
            if type(document) is not dict or set(document) != {"document_id", "title"}:
                raise ToolPolicyError("invalid_context")
            document_id, title = document["document_id"], document["title"]
            if (type(document_id) is not str or re.fullmatch(r"[0-9a-f]{32}", document_id) is None
                    or document_id not in context["allowed_document_ids"] or document_id in seen_documents
                    or type(title) is not str or not title.strip() or len(title) > 512):
                raise ToolPolicyError("invalid_context")
            seen_documents.add(document_id)
            copied["documents"].append(dict(document))
    for name, maximum in (("candidates", 16), ("existing_results", 8)):
        records = context[name]
        _context_list(records, maximum)
        fields = dict(PAPER_FIELDS)
        extra_fields = {"year"}
        if name == "existing_results":
            fields.update(status=64, text=512)
            # Both occur in retained SFT data; neither grants extra authority.
            extra_fields.update(("untrusted", "queries"))
        else:
            # Retained validation includes untrusted candidate snippets.
            fields["snippet"] = 512
        seen = set()
        copied[name] = []
        for record in records:
            if type(record) is not dict or not record or not set(record) <= set(fields) | extra_fields:
                raise ToolPolicyError("invalid_context")
            for key, value in record.items():
                if key == "year":
                    if value is not None and (type(value) is not int or not 1000 <= value <= 2200):
                        raise ToolPolicyError("invalid_context")
                elif key == "untrusted":
                    if type(value) is not bool:
                        raise ToolPolicyError("invalid_context")
                elif key == "queries":
                    _context_list(value, 8)
                    if any(type(query) is not str or not query.strip() or len(query) > 4096 for query in value):
                        raise ToolPolicyError("invalid_context")
                elif type(value) is not str or len(value) > fields[key]:
                    raise ToolPolicyError("invalid_context")
            if name == "candidates":
                paper_id = record.get("paper_id")
                if not paper_id or not paper_id.strip() or paper_id in seen:
                    raise ToolPolicyError("invalid_context")
                seen.add(paper_id)
            copied[name].append({key: list(value) if key == "queries" else value
                                 for key, value in record.items()})
    return copied


def build_messages(task, context):
    if type(task) is not str or not task.strip() or len(task) > 4096:
        raise ToolPolicyError("invalid_task")
    context = validate_context(context)
    messages = [
        {"role": "system", "content": SYSTEM_V3},
        {"role": "user", "content": canonical({"task": task, "context": context, "tools": tools_schema()})},
    ]
    try:
        size = sum(len(message["content"].encode("utf-8")) for message in messages)
    except UnicodeError:
        raise ToolPolicyError("invalid_context") from None
    if size > MAX_INPUT_BYTES:
        raise ToolPolicyError("input_too_large")
    return messages


def authorize(action, context):
    """Revalidate a decision against current server-owned grants before dispatch."""
    context = validate_context(context)
    try:
        raw = canonical(action)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ContractError("invalid_action") from None
    action = parse_response(raw)
    if action["action"] != "call":
        return action
    tool, arguments = action["tool"], action["arguments"]
    if tool in ("search_papers", "resolve_paper", "read_paper"):
        if context["mode"] not in ("web", "hybrid"):
            raise ContractError("network_not_authorized")
    elif context["mode"] not in ("local", "hybrid"):
        raise ContractError("local_not_authorized")
    if tool == "search_papers" and not set(arguments["providers"]) <= set(context["allowed_providers"]):
        raise ContractError("provider_not_authorized")
    if tool == "resolve_paper" and "crossref" not in context["allowed_providers"]:
        raise ContractError("provider_not_authorized")
    if tool == "search_library" and not set(arguments["document_ids"]) <= set(context["allowed_document_ids"]):
        raise ContractError("document_not_authorized")
    if tool == "read_paper" and arguments["paper_id"] not in {p["paper_id"] for p in context["candidates"]}:
        raise ContractError("paper_not_authorized")
    return action


def _local_endpoint(endpoint):
    if type(endpoint) is not str or len(endpoint) > 128:
        raise ToolPolicyError("invalid_endpoint")
    try:
        parts = urlsplit(endpoint)
        if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1")
                or parts.username is not None or parts.password is not None
                or parts.path not in ("", "/") or parts.query or parts.fragment
                or parts.port is None or not 1 <= parts.port <= 65535):
            raise ValueError
    except ValueError:
        raise ToolPolicyError("invalid_endpoint") from None
    host = "[::1]" if parts.hostname == "::1" else "127.0.0.1"
    return f"http://{host}:{parts.port}"


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ToolPolicyError("invalid_server_response") from None


class ToolPolicy:
    """Synchronous llama-server client with one generation and no retries.

    Injected transports implement requests.Session.request for offline tests.
    Server identity checks and tokenization never perform model inference.
    """

    authorize = staticmethod(authorize)

    def __init__(self, endpoint=DEFAULT_ENDPOINT, *, transport=None, timeout=DEFAULT_GENERATION_TIMEOUT):
        self.endpoint = _local_endpoint(endpoint)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 600:
            raise ToolPolicyError("invalid_timeout")
        self.timeout = float(timeout)
        self._transport = transport

    def _request(self, method, route, payload=None):
        session = self._transport if self._transport is not None else requests.Session()
        if self._transport is None:
            session.trust_env = False
        response = None
        try:
            read_timeout = self.timeout if route == "/completion" else METADATA_TIMEOUT
            response = session.request(method, self.endpoint + route, json=payload,
                                       timeout=(3.05, read_timeout), allow_redirects=False, stream=True)
            if response.status_code != 200:
                raise ToolPolicyError("policy_unavailable")
            chunks = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                chunks.extend(chunk)
                if len(chunks) > MAX_RESPONSE_BYTES:
                    raise ToolPolicyError("invalid_server_response")
            return _strict_json(bytes(chunks))
        except requests.RequestException:
            raise ToolPolicyError("policy_unavailable") from None
        finally:
            if response is not None:
                response.close()
            if self._transport is None:
                session.close()

    def _check_identity(self):
        models = self._request("GET", "/v1/models")
        entries = models.get("data") if type(models) is dict else None
        if (type(entries) is not list or len(entries) != 1 or type(entries[0]) is not dict
                or entries[0].get("id") != MODEL_ALIAS):
            raise ToolPolicyError("model_mismatch")
        adapters = self._request("GET", "/lora-adapters")
        if type(adapters) is not list or len(adapters) != 1 or type(adapters[0]) is not dict:
            raise ToolPolicyError("adapter_mismatch")
        adapter = adapters[0]
        if (type(adapter.get("id")) is not int or adapter["id"] != 0
                or type(adapter.get("scale")) not in (int, float) or adapter["scale"] != 1
                or type(adapter.get("path")) is not str
                or adapter["path"].replace("\\", "/").rsplit("/", 1)[-1] != "sft-v1.gguf"):
            raise ToolPolicyError("adapter_mismatch")

    def get_status(self):
        """Report only fixed public fields; omit paths, prompts and HTTP failures."""
        try:
            self._check_identity()
        except ToolPolicyError as error:
            return {"available": False, "status": "unavailable", "model": MODEL_ALIAS, "code": str(error)}
        return {"available": True, "status": "ready", "model": MODEL_ALIAS, "code": "ready"}

    def decide(self, task, context):
        messages = build_messages(task, context)
        self._check_identity()
        template_request = {"messages": messages, "chat_template_kwargs": {"enable_thinking": False}}
        rendered = self._request("POST", "/apply-template", template_request)
        prompt = rendered.get("prompt") if type(rendered) is dict else None
        try:
            valid_prompt = type(prompt) is str and bool(prompt) and len(prompt.encode("utf-8")) <= MAX_INPUT_BYTES
        except UnicodeError:
            valid_prompt = False
        if not valid_prompt:
            raise ToolPolicyError("invalid_server_response")
        tokenized = self._request("POST", "/tokenize", {
            "content": prompt, "add_special": True, "parse_special": True, "with_pieces": False,
        })
        tokens = tokenized.get("tokens") if type(tokenized) is dict else None
        if type(tokens) is not list or not tokens or any(type(token) is not int or token < 0 for token in tokens):
            raise ToolPolicyError("invalid_server_response")
        if len(tokens) > MAX_INPUT_TOKENS:
            raise ToolPolicyError("input_too_large")
        payload = {
            # Integer prompts bypass retokenization and chat output parsing.
            # The one inference consumes exactly the sequence checked above.
            "prompt": tokens, "model": MODEL_ALIAS, "temperature": 0,
            "n_predict": MAX_OUTPUT_TOKENS, "stream": False, "cache_prompt": True,
            "repeat_penalty": 1.0, "presence_penalty": 0.0, "frequency_penalty": 0.0,
            "lora": [{"id": 0, "scale": 1.0}],
            "response_fields": ["content", "stop", "stop_type", "truncated", "model",
                                "tokens_evaluated", "tokens_predicted", "timings"],
        }
        result = self._request("POST", "/completion", payload)
        if type(result) is not dict:
            raise ToolPolicyError("invalid_server_response")
        if (result.get("stop") is not True or result.get("truncated") is not False
                or result.get("stop_type") not in ("eos", "word")):
            raise ToolPolicyError("incomplete_decision")
        if type(result.get("tokens_evaluated")) is not int or result["tokens_evaluated"] != len(tokens):
            raise ToolPolicyError("input_token_mismatch")
        return parse_response(result.get("content"))
