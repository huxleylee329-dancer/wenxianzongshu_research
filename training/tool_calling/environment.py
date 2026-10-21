"""Closed-world mock tools. No network, file reading, eval, or production dispatch."""

from __future__ import annotations

from contracts import ContractError, normalized_query, parse_response, canonical


class OfflineEnvironment:
    def __init__(self, context, world):
        self.context = context
        self.world = world
        self.calls = []
        self.consumed = False

    def execute(self, action):
        # Revalidate if callers bypass the raw-response entry point.
        action = parse_response(canonical(action))
        if self.consumed:
            raise ContractError("one_step_only")
        self.consumed = True
        if action["action"] != "call":
            return {"ids": [], "status": action["action"]}
        tool, args = action["tool"], action["arguments"]
        ctx = self.context
        if tool in ("search_papers", "resolve_paper", "read_paper"):
            if ctx["mode"] not in ("web", "hybrid"):
                raise ContractError("network_not_authorized")
        elif ctx["mode"] not in ("local", "hybrid"):
            raise ContractError("local_not_authorized")
        if tool == "search_papers":
            if not set(args["providers"]) <= set(ctx["allowed_providers"]):
                raise ContractError("provider_not_authorized")
        if tool == "resolve_paper" and "crossref" not in ctx["allowed_providers"]:
            # Real PaperTools.resolve_paper uses Crossref, not an arbitrary provider.
            raise ContractError("provider_not_authorized")
        if tool == "search_library":
            if not set(args["document_ids"]) <= set(ctx["allowed_document_ids"]):
                raise ContractError("document_not_authorized")
        if tool == "read_paper":
            known = {p["paper_id"] for p in ctx["candidates"]}
            if args["paper_id"] not in known:
                raise ContractError("paper_not_authorized")
        self.calls.append(action)
        if tool == "search_papers":
            ids = []
            for provider in args["providers"]:
                matched = [p["paper_id"] for p in self.world["papers"]
                           if p["provider"] == provider and normalized_query(args["query"]) in
                           {normalized_query(q) for q in p["queries"]}]
                ids.extend(matched[:args["per_source"]])
        elif tool == "resolve_paper":
            ids = [p["paper_id"] for p in self.world["papers"] if p["doi"] == args["identifier"]]
        elif tool == "read_paper":
            ids = [p["paper_id"] for p in self.world["papers"] if p["paper_id"] == args["paper_id"]]
        else:
            ids = [d["document_id"] for d in self.world["documents"]
                   if d["document_id"] in args["document_ids"] and normalized_query(args["query"]) in
                   {normalized_query(q) for q in d["queries"]}][:args["limit"]]
        return {"ids": list(dict.fromkeys(ids)), "status": "ok" if ids else "empty"}
