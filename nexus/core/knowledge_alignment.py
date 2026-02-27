"""Repository-native feature alignment evaluation helpers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
        "workflow",
        "feature",
        "issue",
    }
)
_SOURCE_DIRS = ("docs",)
_SOURCE_FILE_PREFIXES = ("readme", "adr-")
_SOURCE_SUFFIXES = (".md", ".mdx")


@dataclass(frozen=True)
class AlignmentArtifact:
    source: str
    title: str
    path_or_url: str
    rationale: str


@dataclass(frozen=True)
class AlignmentResult:
    alignment_score: float
    matched_artifacts: list[AlignmentArtifact]
    gaps: list[str]
    recommended_next_actions: list[str]

    @property
    def alignment_summary(self) -> str:
        if not self.matched_artifacts:
            return "No meaningful alignment found in repository docs/ADRs for this request."
        top = self.matched_artifacts[0]
        percent = round(self.alignment_score * 100)
        return f"{percent}% alignment confidence based on {top.path_or_url} and related repository artifacts."

    @property
    def artifact_paths(self) -> list[str]:
        return [artifact.path_or_url for artifact in self.matched_artifacts]


class KnowledgeAlignmentService:
    """Evaluate task text alignment against local documentation and ADRs."""

    def evaluate(
        self,
        request_text: str,
        workflow_type: str,
        repo_path: str,
        max_hits: int = 3,
    ) -> AlignmentResult:
        query_tokens = _tokenize(f"{request_text or ''} {workflow_type or ''}")
        artifacts = self._index_repo(repo_path)
        if not query_tokens or not artifacts:
            initial_gaps = sorted(query_tokens)[:8]
            return AlignmentResult(
                alignment_score=0.0,
                matched_artifacts=[],
                gaps=initial_gaps,
                recommended_next_actions=_build_recommendations(0.0, query_tokens, initial_gaps),
            )

        scored: list[tuple[float, dict[str, object], set[str]]] = []
        covered: set[str] = set()
        for artifact in artifacts:
            artifact_tokens = artifact["tokens"]
            overlap = query_tokens & artifact_tokens
            if not overlap:
                continue
            coverage = len(overlap) / max(1, len(query_tokens))
            concentration = len(overlap) / max(1, len(artifact_tokens))
            score = round((coverage * 0.8) + (concentration * 0.2), 4)
            scored.append((score, artifact, overlap))

        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[: max(1, max_hits)]
        matched = [
            AlignmentArtifact(
                source="repo",
                title=str(artifact["title"]),
                path_or_url=str(artifact["path"]),
                rationale=_build_rationale(overlap),
            )
            for _, artifact, overlap in top
        ]
        for _, _, overlap in top:
            covered.update(overlap)

        score = round(sum(item[0] for item in top) / len(top), 4) if top else 0.0
        gaps = sorted(query_tokens - covered)[:8]
        return AlignmentResult(
            alignment_score=score,
            matched_artifacts=matched,
            gaps=gaps,
            recommended_next_actions=_build_recommendations(score, query_tokens, gaps),
        )

    def _index_repo(self, repo_path: str) -> list[dict[str, object]]:
        base = os.path.abspath(repo_path or ".")
        paths: list[str] = []
        for root_name in _SOURCE_DIRS:
            root = os.path.join(base, root_name)
            if not os.path.isdir(root):
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for filename in filenames:
                    lower = filename.lower()
                    if not lower.endswith(_SOURCE_SUFFIXES):
                        continue
                    paths.append(os.path.join(dirpath, filename))

        try:
            base_files = os.listdir(base)
        except OSError:
            base_files = []
        for filename in base_files:
            lower = filename.lower()
            if not lower.endswith(_SOURCE_SUFFIXES):
                continue
            if lower.startswith(_SOURCE_FILE_PREFIXES):
                paths.append(os.path.join(base, filename))

        indexed: list[dict[str, object]] = []
        seen: set[str] = set()
        for path in paths:
            abs_path = os.path.abspath(path)
            if abs_path in seen:
                continue
            seen.add(abs_path)
            text = _read_text(abs_path)
            if not text:
                continue
            headings = _extract_headings(text)
            title = headings[0] if headings else os.path.basename(abs_path)
            excerpt = " ".join(text.split())[:500]
            relative = os.path.relpath(abs_path, base)
            tokens = _tokenize(" ".join([title, " ".join(headings), excerpt]))
            if not tokens:
                continue
            indexed.append(
                {
                    "path": relative,
                    "title": title,
                    "tokens": tokens,
                }
            )
        return indexed


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _extract_headings(text: str) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            headings.append(match.group(1).strip())
    return headings


def _tokenize(text: str) -> set[str]:
    tokens = {token.lower() for token in _WORD_RE.findall(text.lower())}
    return {token for token in tokens if token not in _STOP_WORDS and len(token) >= 3}


def _build_rationale(overlap: set[str]) -> str:
    keywords = ", ".join(sorted(overlap)[:5])
    return f"Keyword overlap: {keywords}" if keywords else "No overlapping keywords."


def _build_recommendations(score: float, query_tokens: set[str], gaps: list[str]) -> list[str]:
    actions: list[str] = []
    if score < 0.35:
        actions.append(
            "Low alignment confidence: draft an ADR or extend docs before implementation."
        )
    if gaps:
        actions.append(f"Clarify unresolved terms in the issue: {', '.join(gaps[:5])}.")
    if query_tokens:
        actions.append("Reference matched artifacts explicitly in the design or implementation comment.")
    return actions
