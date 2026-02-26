"""Workflow routing and tier selection logic."""

import logging

logger = logging.getLogger(__name__)


class WorkflowRouter:
    """Logic for detecting and suggesting workflow tiers."""

    @staticmethod
    def detect_tier_from_labels(labels: list[str]) -> str:
        """
        Detect workflow tier from a list of labels.

        Priority:
        1. workflow:* labels (explicit)
        2. priority:critical/urgent -> fast-track
        3. bug/fix -> shortened
        4. feature/enhancement -> full

        Returns:
            Tier name ("full", "shortened", "fast-track")
        """
        # Explicit labels
        for label in labels:
            if label == "workflow:full":
                return "full"
            elif label == "workflow:shortened":
                return "shortened"
            elif label == "workflow:fast-track":
                return "fast-track"

        # Content-based auto-detection
        labels_lower = [l.lower() for l in labels]

        if any(w in l for l in labels_lower for w in ("critical", "hotfix", "urgent")):
            return "fast-track"
        elif any(w in l for l in labels_lower for w in ("bug", "fix")):
            return "shortened"

        # Default for everything else
        return "full"

    @staticmethod
    def suggest_tier_from_content(title: str, body: str) -> str | None:
        """
        Suggest a workflow tier based on the content of an issue.

        Args:
            title: Issue title.
            body: Issue description.

        Returns:
            Suggested tier name or None.
        """
        content = f"{title} {body}".lower()

        if any(word in content for word in ["critical", "urgent", "hotfix", "asap"]):
            return "fast-track"
        elif any(word in content for word in ["bug", "fix", "problem"]):
            return "shortened"
        elif any(
            word in content for word in ["feature", "add", "enhancement", "improvement", "new"]
        ):
            return "full"

        return None

    @staticmethod
    def suggest_tier_label(title: str, body: str) -> str | None:
        """Suggest a ``workflow:<tier>`` label based on issue content.

        Convenience wrapper around :meth:`suggest_tier_from_content` that
        returns a label string (e.g. ``"workflow:full"``) or ``None``.
        """
        tier = WorkflowRouter.suggest_tier_from_content(title, body)
        return f"workflow:{tier}" if tier else None
