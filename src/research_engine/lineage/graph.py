"""Lineage DAG: report element -> chart -> model output -> fact -> document -> source."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from ..errors import LineageError
from ..schemas.document import DocumentRecord
from ..schemas.financial import FinancialFact, Provenance


class NodeKind(str, Enum):
    SOURCE = "source"
    DOCUMENT = "document"
    FACT = "fact"
    ASSUMPTION = "assumption"
    MODEL_OUTPUT = "model_output"
    CHART = "chart"
    REPORT_ELEMENT = "report_element"


# Analyst assumptions are legitimate roots; they are declared, not sourced.
LEGITIMATE_ROOTS = frozenset({NodeKind.SOURCE, NodeKind.ASSUMPTION})


@dataclass(frozen=True)
class LineageNode:
    node_id: str
    kind: NodeKind
    label: str
    attributes: Mapping[str, str] = field(default_factory=dict)


class LineageGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, LineageNode] = {}
        self._parents: dict[str, list[str]] = {}

    def add_node(self, node: LineageNode) -> None:
        existing = self._nodes.get(node.node_id)
        if existing is not None:
            if existing != node:
                raise LineageError(f"conflicting definitions for lineage node {node.node_id!r}")
            return
        self._nodes[node.node_id] = node
        self._parents[node.node_id] = []

    def node(self, node_id: str) -> LineageNode:
        if node_id not in self._nodes:
            raise LineageError(f"unknown lineage node {node_id!r}")
        return self._nodes[node_id]

    def parents(self, node_id: str) -> list[LineageNode]:
        self.node(node_id)
        return [self._nodes[p] for p in self._parents[node_id]]

    def ancestors(self, node_id: str) -> set[str]:
        self.node(node_id)
        seen: set[str] = set()
        stack = list(self._parents[node_id])
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(self._parents[current])
        return seen

    def link(self, child_id: str, parent_id: str) -> None:
        """Record that `child_id` was derived from `parent_id`."""
        self.node(child_id)
        self.node(parent_id)
        if child_id == parent_id or child_id in self.ancestors(parent_id):
            raise LineageError(f"linking {child_id!r} <- {parent_id!r} would create a lineage cycle")
        if parent_id not in self._parents[child_id]:
            self._parents[child_id].append(parent_id)

    def trace(self, node_id: str) -> list[list[LineageNode]]:
        """Every path from the node up to a root, node first."""
        start = self.node(node_id)
        paths: list[list[LineageNode]] = []

        def walk(current: LineageNode, path: list[LineageNode]) -> None:
            parents = self._parents[current.node_id]
            if not parents:
                paths.append(path)
                return
            for p in parents:
                walk(self._nodes[p], path + [self._nodes[p]])

        walk(start, [start])
        return paths

    def broken_nodes(self) -> list[LineageNode]:
        """Nodes whose lineage terminates somewhere other than a source or declared assumption."""
        broken = []
        for node_id, node in self._nodes.items():
            for path in self.trace(node_id):
                if path[-1].kind not in LEGITIMATE_ROOTS:
                    broken.append(node)
                    break
        return broken

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {"id": n.node_id, "kind": n.kind.value, "label": n.label, "attributes": dict(n.attributes)}
                for n in self._nodes.values()
            ],
            "edges": [{"child": c, "parent": p} for c, ps in self._parents.items() for p in ps],
        }

    @classmethod
    def from_records(cls, documents: Iterable[DocumentRecord], facts: Iterable[FinancialFact]) -> LineageGraph:
        graph = cls()
        for doc in documents:
            location = doc.source_url or doc.local_source_path
            source_id = f"src:{location}"
            graph.add_node(LineageNode(source_id, NodeKind.SOURCE, location))
            graph.add_node(LineageNode(doc.document_id, NodeKind.DOCUMENT, doc.document_type.value,
                                       {"file_hash": doc.file_hash or "", "fiscal_period": doc.fiscal_period or ""}))
            graph.link(doc.document_id, source_id)
        facts = list(facts)
        for fact in facts:
            attrs = {"value": str(fact.value), "unit": fact.unit, "period": fact.period.label}
            if fact.source is not None:
                attrs.update({k: str(v) for k, v in fact.source.model_dump(exclude_none=True).items() if k != "document_id"})
            graph.add_node(LineageNode(fact.fact_id, NodeKind.FACT, fact.metric_id, attrs))
        for fact in facts:
            if fact.provenance is Provenance.REPORTED:
                if fact.source.document_id not in graph._nodes:
                    raise LineageError(f"fact {fact.fact_id} cites unregistered document {fact.source.document_id}")
                graph.link(fact.fact_id, fact.source.document_id)
            else:
                for input_id in fact.inputs:
                    if input_id not in graph._nodes:
                        raise LineageError(f"derived fact {fact.fact_id} references unknown input {input_id}")
                    graph.link(fact.fact_id, input_id)
        return graph
