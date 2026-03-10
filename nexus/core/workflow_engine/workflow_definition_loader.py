import os
from copy import deepcopy
from typing import Any, Callable

import yaml

from nexus.core.models import Agent, WorkflowStep


def parse_require_human_merge_approval(data: dict[str, Any]) -> bool:
    """Parse workflow-level approval setting from workflow definition."""
    monitoring = data.get("monitoring", {})
    require_human_merge_approval = True
    if isinstance(monitoring, dict):
        require_human_merge_approval = monitoring.get("require_human_merge_approval", True)

    # Retained because workflow YAML may still define this top-level key.
    if "require_human_merge_approval" in data:
        require_human_merge_approval = data.get("require_human_merge_approval", True)
    return bool(require_human_merge_approval)


def parse_require_approval_for(data: dict[str, Any]) -> list[str]:
    """Parse list of steps that require strict human approval gates."""
    require_approval_for = data.get("require_approval_for")
    if not require_approval_for:
        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            require_approval_for = metadata.get("require_approval_for")
            
    if isinstance(require_approval_for, list):
        return [str(step_id) for step_id in require_approval_for]
    return []


def _step_reference(step: dict[str, Any], index: int) -> str:
    """Return stable step reference used by on_success/routes wiring."""
    return str(step.get("id") or step.get("name") or f"step_{index}").strip()


def _target_candidates(target_name: str) -> list[str]:
    target = str(target_name or "").strip()
    if not target:
        return []
    if target.endswith((".yaml", ".yml")):
        return [target]
    return [f"{target}.yaml", f"{target}.yml"]


def _namespaced_step_id(namespace: str, step_ref: str) -> str:
    clean_step = str(step_ref or "").strip()
    if not clean_step:
        return namespace
    return f"{namespace}__{clean_step}"


def _namespace_steps(
    steps: list[dict[str, Any]],
    *,
    namespace: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Prefix a workflow branch step graph with a namespace to avoid ID collisions."""
    id_map: dict[str, str] = {}
    ordered_refs: list[str] = []

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        step_ref = _step_reference(step, idx)
        if not step_ref or step_ref in id_map:
            continue
        ordered_refs.append(step_ref)
        id_map[step_ref] = _namespaced_step_id(namespace, step_ref)

    if not ordered_refs:
        return [], None

    namespaced_steps: list[dict[str, Any]] = []
    for idx, original in enumerate(steps, start=1):
        if not isinstance(original, dict):
            continue
        copied: dict[str, Any] = deepcopy(original)
        original_ref = _step_reference(original, idx)
        copied["id"] = id_map.get(original_ref, _namespaced_step_id(namespace, original_ref))

        on_success = copied.get("on_success")
        if isinstance(on_success, str) and on_success in id_map:
            copied["on_success"] = id_map[on_success]

        parallel = copied.get("parallel")
        if isinstance(parallel, list):
            copied["parallel"] = [
                id_map.get(str(step_id), str(step_id))
                for step_id in parallel
            ]

        routes = copied.get("routes")
        if isinstance(routes, list):
            for route in routes:
                if not isinstance(route, dict):
                    continue
                for field in ("then", "goto", "default"):
                    target = route.get(field)
                    if isinstance(target, str) and target in id_map:
                        route[field] = id_map[target]

        namespaced_steps.append(copied)

    return namespaced_steps, id_map.get(ordered_refs[0])


def _load_target_workflow(
    *,
    base_dir: str,
    source_path: str,
    target: str,
) -> tuple[dict[str, Any], str] | None:
    for candidate in _target_candidates(target):
        candidate_path = os.path.join(base_dir, candidate)
        if os.path.abspath(candidate_path) == os.path.abspath(source_path):
            continue
        if not os.path.exists(candidate_path):
            continue
        try:
            with open(candidate_path, encoding="utf-8") as handle:
                routed_data = yaml.safe_load(handle)
        except Exception:
            continue
        if not isinstance(routed_data, dict):
            continue
        routed_data["__yaml_path"] = candidate_path
        return routed_data, candidate_path
    return None


def _expand_external_router_targets(
    *,
    data: dict[str, Any],
    workflow_type: str,
) -> list[dict[str, Any]]:
    """Inline external router-targeted workflows into a single executable step list."""
    source_path = data.get("__yaml_path")
    flat_steps = data.get("steps", [])
    if not isinstance(source_path, str) or not source_path:
        return []
    if not isinstance(flat_steps, list) or not flat_steps:
        return []

    base_dir = os.path.dirname(source_path)
    rewritten_steps: list[dict[str, Any]] = []
    branch_steps: list[dict[str, Any]] = []
    branch_entry_by_target: dict[str, str] = {}
    used_namespaces: set[str] = set()

    for step in flat_steps:
        if not isinstance(step, dict):
            continue
        copied_step: dict[str, Any] = deepcopy(step)
        routes = copied_step.get("routes")

        targets_in_step: list[str] = []
        if isinstance(routes, list):
            for route in routes:
                if not isinstance(route, dict):
                    continue
                for field in ("then", "goto", "default"):
                    raw_target = route.get(field)
                    if not isinstance(raw_target, str):
                        continue
                    target = raw_target.strip()
                    if not target:
                        continue
                    if target not in targets_in_step:
                        targets_in_step.append(target)

        for target in targets_in_step:
            if target in branch_entry_by_target:
                continue
            loaded = _load_target_workflow(
                base_dir=base_dir,
                source_path=source_path,
                target=target,
            )
            if not loaded:
                continue
            routed_data, _ = loaded
            routed_steps = resolve_workflow_steps_list(routed_data, workflow_type)
            if not routed_steps:
                continue

            namespace_base = str(target).replace("-", "_").replace(".", "_").strip("_")
            namespace = namespace_base or "branch"
            if namespace in used_namespaces:
                suffix = 2
                while f"{namespace}_{suffix}" in used_namespaces:
                    suffix += 1
                namespace = f"{namespace}_{suffix}"
            used_namespaces.add(namespace)

            namespaced_steps, entry = _namespace_steps(routed_steps, namespace=namespace)
            if not namespaced_steps or not entry:
                continue

            branch_entry_by_target[target] = entry
            branch_steps.extend(namespaced_steps)

        if isinstance(routes, list):
            for route in routes:
                if not isinstance(route, dict):
                    continue
                for field in ("then", "goto", "default"):
                    raw_target = route.get(field)
                    if not isinstance(raw_target, str):
                        continue
                    rewritten = branch_entry_by_target.get(raw_target.strip())
                    if rewritten:
                        route[field] = rewritten

        rewritten_steps.append(copied_step)

    if not branch_steps:
        return []

    return rewritten_steps + branch_steps


def resolve_workflow_steps_list(
    data: dict[str, Any], workflow_type: str = ""
) -> list[dict[str, Any]]:
    """Resolve workflow steps from flat or tiered workflow definition layouts."""
    if workflow_type:
        workflow_types_mapping = data.get("workflow_types", {})
        mapped_type = workflow_types_mapping.get(workflow_type, workflow_type)

        key_prefix = mapped_type.replace("-", "_")
        keys_to_try = [
            f"{key_prefix}_workflow",
            key_prefix,
            f"{mapped_type}_workflow",
            mapped_type,
        ]
        seen: set[str] = set()
        for key in keys_to_try:
            if key in seen:
                continue
            seen.add(key)
            tier = data.get(key, {})
            if isinstance(tier, dict) and tier.get("steps"):
                steps = tier["steps"]
                return steps if isinstance(steps, list) else []

        # Router definitions can dispatch to tier-specific workflow files
        # (e.g. enterprise_full_workflow.yaml). If present, follow the route.
        source_path = data.get("__yaml_path")
        if isinstance(source_path, str) and source_path:
            mapped_norm = mapped_type.replace("-", "_").lower()
            targets: list[str] = []
            for step in data.get("steps", []):
                if not isinstance(step, dict):
                    continue
                for route in step.get("routes", []):
                    if not isinstance(route, dict):
                        continue
                    for key in ("then", "goto", "default"):
                        target = route.get(key)
                        if not isinstance(target, str):
                            continue
                        target_name = target.strip()
                        if not target_name:
                            continue
                        if mapped_norm not in target_name.replace("-", "_").lower():
                            continue
                        if target_name not in targets:
                            targets.append(target_name)

            base_dir = os.path.dirname(source_path)
            for target in targets:
                candidate_files = (
                    [target]
                    if target.endswith((".yaml", ".yml"))
                    else [f"{target}.yaml", f"{target}.yml"]
                )
                for candidate in candidate_files:
                    candidate_path = os.path.join(base_dir, candidate)
                    if os.path.abspath(candidate_path) == os.path.abspath(source_path):
                        continue
                    if not os.path.exists(candidate_path):
                        continue
                    try:
                        with open(candidate_path, encoding="utf-8") as handle:
                            routed_data = yaml.safe_load(handle)
                    except Exception:
                        continue
                    if not isinstance(routed_data, dict):
                        continue
                    routed_data["__yaml_path"] = candidate_path
                    routed_steps = resolve_workflow_steps_list(routed_data, workflow_type)
                    if routed_steps:
                        return routed_steps

            expanded = _expand_external_router_targets(
                data=data,
                workflow_type=workflow_type,
            )
            if expanded:
                return expanded

        # If no tier mapping is present, permit flat layout as fallback.
        flat = data.get("steps", [])
        if isinstance(flat, list) and flat:
            return flat
        return []

    flat = data.get("steps", [])
    if isinstance(flat, list) and flat:
        return flat

    for key, value in data.items():
        if key.endswith("_workflow") and isinstance(value, dict) and value.get("steps"):
            steps = value["steps"]
            return steps if isinstance(steps, list) else []

    return []


def build_workflow_steps(
    *,
    data: dict[str, Any],
    steps_data: list[dict[str, Any]],
    slugify: Callable[[str], str],
) -> list[WorkflowStep]:
    """Build WorkflowStep models from parsed workflow step dictionaries."""
    steps: list[WorkflowStep] = []
    for idx, step_data in enumerate(steps_data, start=1):
        if not isinstance(step_data, dict):
            raise ValueError(f"Step {idx} must be a dict")

        agent_type = step_data.get("agent_type", "agent")
        step_name = step_data.get("id") or step_data.get("name") or f"step_{idx}"
        step_desc = step_data.get("description", "")
        prompt_template = step_data.get("prompt_template") or step_desc or "Execute step"

        step_retry: int | None = step_data.get("retry")
        retry_policy = step_data.get("retry_policy")
        step_backoff_strategy: str | None = None
        step_initial_delay: float = 0.0
        if isinstance(retry_policy, dict):
            if step_retry is None:
                step_retry = retry_policy.get("max_retries")
            step_backoff_strategy = retry_policy.get("backoff")
            raw_delay = retry_policy.get("initial_delay", 0.0)
            try:
                step_initial_delay = float(raw_delay) if raw_delay else 0.0
            except (TypeError, ValueError):
                step_initial_delay = 0.0
        
        # Enforce workflow-level security limits
        workflow_tools = data.get("allowed_tools")
        
        # Default to fail-closed: if the step specifies no tools, it gets precisely zero tools.
        step_tools = step_data.get("tools", [])
        if not isinstance(step_tools, list):
            step_tools = []

        if isinstance(workflow_tools, list):
            # Step requested specific tools; rigidly filter against the global whitelist boundary
            combined_tools = [t for t in step_tools if t in workflow_tools]
        else:
            # No global whitelist exists; step tools are accepted as-is
            combined_tools = step_tools
            
        # Every step requires the ability to post status comments for observability
        if "vcs:add_comment" not in combined_tools:
            # Copy to avoid mutating original lists from yaml parser
            combined_tools = list(combined_tools) + ["vcs:add_comment"]
            
        agent = Agent(
            name=agent_type,
            display_name=step_data.get("name", agent_type),
            description=step_desc or f"Step {idx}",
            timeout=data.get("timeout_seconds", 600),
            max_retries=2,
            allowed_tools=combined_tools,
        )

        inputs_data = step_data.get("inputs", {})
        if isinstance(inputs_data, list):
            normalized_inputs = {}
            for entry in inputs_data:
                if isinstance(entry, dict):
                    normalized_inputs.update(entry)
            inputs_data = normalized_inputs

        parallel_raw = step_data.get("parallel", [])
        if isinstance(parallel_raw, list):
            parallel_with: list[str] = [slugify(step_id) or step_id for step_id in parallel_raw]
        else:
            parallel_with = []

        step_routes = step_data.get("routes", [])
        steps.append(
            WorkflowStep(
                step_num=idx,
                name=slugify(step_name) or step_name,
                agent=agent,
                prompt_template=prompt_template,
                condition=step_data.get("condition"),
                retry=step_retry,
                backoff_strategy=step_backoff_strategy,
                initial_delay=step_initial_delay,
                inputs=inputs_data,
                routes=step_routes,
                on_success=step_data.get("on_success"),
                final_step=bool(step_data.get("final_step", False)),
                parallel_with=parallel_with,
                require_human_approval=bool(step_data.get("require_human_approval", False)),
            )
        )

    return steps


def build_dry_run_report_fields(
    *,
    data: dict[str, Any],
    workflow_type: str,
    resolve_steps: Callable[[dict[str, Any], str], list[dict[str, Any]]],
) -> tuple[list[str], list[str]]:
    """Validate a workflow definition dict and simulate the predicted step flow."""
    errors: list[str] = []
    predicted_flow: list[str] = []

    if not isinstance(data, dict):
        return ["Workflow definition must be a dict"], []

    metadata_block = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    if (
        not metadata_block.get("name")
        and not metadata_block.get("id")
    ):
        errors.append("Missing required top-level field: 'name' or 'id'")

    steps = resolve_steps(data, workflow_type)
    if not steps:
        errors.append(
            f"No steps found for workflow_type={workflow_type!r}. "
            "Check that the workflow definition contains a non-empty steps list."
        )
    else:
        step_ids = {s["id"] for s in steps if isinstance(s, dict) and "id" in s}

        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                errors.append(f"Step {idx}: must be a dict, got {type(step).__name__}")
                continue

            step_label = step.get("id") or step.get("name") or f"step_{idx}"
            agent_type = step.get("agent_type", "")
            if not agent_type:
                errors.append(f"Step '{step_label}': missing 'agent_type'")

            on_success = step.get("on_success")
            if on_success and step_ids and on_success not in step_ids:
                errors.append(
                    f"Step '{step_label}': 'on_success' references unknown step id '{on_success}'"
                )

            condition = step.get("condition")
            if condition:
                try:
                    compile(condition, "<condition>", "eval")
                except SyntaxError as exc:
                    errors.append(
                        f"Step '{step_label}': malformed condition expression "
                        f"'{condition}' — {exc}"
                    )

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        agent_type = step.get("agent_type", "")
        if agent_type == "router":
            continue

        step_label = step.get("name") or step.get("id") or f"step_{idx}"
        condition = step.get("condition")
        if not condition:
            predicted_flow.append(f"RUN  {step_label} ({agent_type})")
            continue

        try:
            result = eval(condition, {"__builtins__": {}}, {})  # noqa: S307
            status = "RUN " if result else "SKIP"
        except NameError:
            status = "RUN "
        except Exception:
            status = "SKIP"

        predicted_flow.append(f"{status} {step_label} ({agent_type}) [condition: {condition}]")

    return errors, predicted_flow


def build_prompt_context_text(
    *,
    steps: list[dict[str, Any]],
    yaml_basename: str,
    workflow_type: str,
    current_agent_type: str,
    valid_next_agents: list[str],
) -> str:
    """Render workflow steps and next-agent constraints as prompt context text."""
    if not steps:
        return ""
    tier_label = f" [{workflow_type}]" if workflow_type else ""
    lines: list[str] = [f"**Workflow Steps{tier_label} (from {yaml_basename}):**\n"]
    for idx, step_data in enumerate(steps, 1):
        agent_type = step_data.get("agent_type", "unknown")
        name = step_data.get("name", step_data.get("id", f"Step {idx}"))
        desc = step_data.get("description", "")
        if agent_type == "router":
            continue
        lines.append(f"- {idx}. **{name}** — `{agent_type}` : {desc}")

    lines.append(
        "\n**CRITICAL:** Use ONLY the agent_type names listed above. "
        "DO NOT use old agent names or reference other workflow YAML files."
    )

    seen: set[str] = set()
    display_pairs: list[str] = []
    for step_data in steps:
        agent_type = step_data.get("agent_type", "")
        if agent_type and agent_type != "router" and agent_type not in seen:
            seen.add(agent_type)
            display_pairs.append(f"`{agent_type}` → **{agent_type.title()}**")
    if display_pairs:
        lines.append(
            "\n**Display Names (for the 'Ready for @...' line in your comment):**\n"
            + ", ".join(display_pairs)
        )

    if current_agent_type and valid_next_agents:
        names = ", ".join(f"`{a}`" for a in valid_next_agents)
        if len(valid_next_agents) == 1:
            lines.append(
                f"\n**YOUR next_agent MUST be:** {names}\n"
                f"Do NOT skip ahead or pick a different agent."
            )
        else:
            lines.append(
                f"\n**YOUR next_agent MUST be one of:** {names}\n"
                f"Choose based on your classification. "
                f"Do NOT skip ahead or pick a different agent."
            )

    return "\n".join(lines)


def resolve_next_agent_types_from_steps(
    *,
    steps: list[dict[str, Any]],
    current_agent_type: str,
) -> list[str]:
    """Resolve valid next agent_type values from parsed workflow steps."""
    if not steps:
        return []

    by_id: dict[str, dict[str, Any]] = {
        s["id"]: s for s in steps if isinstance(s, dict) and "id" in s
    }
    current_steps = [
        s for s in steps if isinstance(s, dict) and s.get("agent_type") == current_agent_type
    ]
    if not current_steps:
        return []

    result: list[str] = []
    for step in current_steps:
        on_success = step.get("on_success")
        if step.get("final_step") or not on_success:
            result.append("none")
            continue

        target = by_id.get(on_success)
        if not target:
            continue

        if target.get("agent_type") == "router":
            for route in target.get("routes", []):
                if not isinstance(route, dict):
                    continue
                route_target_id = route.get("then") or route.get("default")
                if route_target_id and route_target_id in by_id:
                    result.append(by_id[route_target_id].get("agent_type", "unknown"))
                elif route_target_id:
                    result.append(str(route_target_id))
            default_route = target.get("default")
            if default_route and default_route in by_id:
                result.append(by_id[default_route].get("agent_type", "unknown"))
        else:
            result.append(target.get("agent_type", "unknown"))

    seen: set[str] = set()
    unique: list[str] = []
    for agent in result:
        if agent not in seen:
            seen.add(agent)
            unique.append(agent)
    return unique


def canonicalize_next_agent_from_steps(
    *,
    steps: list[dict[str, Any]],
    candidate: str,
    valid_next_agents: list[str],
) -> str:
    """Map a normalized candidate (step id/name) to a valid next agent_type."""
    candidate_lc = candidate.lower()
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id", "")).strip().lower()
        step_name = str(step.get("name", "")).strip().lower()
        if candidate_lc in (step_id, step_name):
            mapped = str(step.get("agent_type", "")).strip()
            if mapped in valid_next_agents:
                return mapped
    return valid_next_agents[0] if len(valid_next_agents) == 1 else ""
