"""AgentRegistry — resolves the preferred AI provider for a given agent_type.

Loads agent YAML definitions from a directory and maps ``spec.agent_type``
to a provider name (``spec.provider``).  Falls back to ``copilot`` when no
explicit provider is declared.

Usage::

    registry = AgentRegistry(agents_dir=Path("examples/agents"))
    provider = registry.resolve("triage", providers)   # returns an AIProvider
"""
import logging
from pathlib import Path

import yaml

from nexus.adapters.ai.base import AIProvider

logger = logging.getLogger(__name__)

# Default provider name when YAML does not specify one
_DEFAULT_PROVIDER = "copilot"


class AgentRegistry:
    """Loads agent YAML definitions and resolves the preferred provider.

    Args:
        agents_dir: Directory containing ``*.yaml`` agent definition files.
    """

    def __init__(self, agents_dir: Path | None = None):
        self._agents_dir = agents_dir
        # Maps agent_type -> provider name (e.g. "copilot" | "gemini")
        self._provider_map: dict[str, str] = {}
        if agents_dir:
            self._load(agents_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        agent_type: str,
        providers: list[AIProvider],
    ) -> AIProvider | None:
        """Return the preferred provider for *agent_type*.

        Looks up the YAML-defined provider name, then finds a matching
        provider from *providers* by ``provider.name``.  If no match is
        found the first available provider is returned, and if the list is
        empty ``None`` is returned.

        Args:
            agent_type: The abstract agent type string (e.g. ``"triage"``).
            providers: Available provider instances.

        Returns:
            The preferred :class:`~nexus.adapters.ai.base.AIProvider` or
            ``None`` if no providers are available.
        """
        if not providers:
            return None

        preferred_name = self._provider_map.get(agent_type, _DEFAULT_PROVIDER)
        for provider in providers:
            if provider.name == preferred_name:
                return provider

        # Fall back to first provider in list
        logger.debug(
            "No provider named %r found for agent_type %r; falling back to %s",
            preferred_name,
            agent_type,
            providers[0].name,
        )
        return providers[0]

    def get_provider_name(self, agent_type: str) -> str:
        """Return the raw provider name string for *agent_type*.

        Returns ``"copilot"`` if no YAML definition exists for the type.
        """
        return self._provider_map.get(agent_type, _DEFAULT_PROVIDER)

    def registered_types(self) -> list[str]:
        """Return all agent_type values found across loaded YAML files."""
        return list(self._provider_map.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self, agents_dir: Path) -> None:
        """Parse all ``*.yaml`` files under *agents_dir* and build the map."""
        if not agents_dir.is_dir():
            logger.warning("AgentRegistry: agents_dir %s does not exist", agents_dir)
            return

        for yaml_file in sorted(agents_dir.glob("*.yaml")):
            try:
                self._parse_yaml(yaml_file)
            except Exception as exc:
                logger.warning("AgentRegistry: failed to parse %s — %s", yaml_file, exc)

    def _parse_yaml(self, path: Path) -> None:
        """Extract ``spec.agent_type`` and ``spec.provider`` from one file."""
        with path.open() as fh:
            data = yaml.safe_load(fh)

        spec = data.get("spec", {}) if isinstance(data, dict) else {}
        agent_type = spec.get("agent_type")
        if not agent_type:
            return  # Not an agent definition with a type

        provider_name = spec.get("provider", _DEFAULT_PROVIDER)
        self._provider_map[agent_type] = provider_name
        logger.debug(
            "AgentRegistry: registered agent_type=%r provider=%r (source=%s)",
            agent_type,
            provider_name,
            path.name,
        )
