"""Approval gate enforcement for workflow steps."""
import logging
from typing import Optional

from nexus.core.models import WorkflowStep, ApprovalGateType

logger = logging.getLogger(__name__)


class ApprovalGateEnforcer:
    """
    Enforces approval gates on workflow steps.
    
    Provides methods to inject approval constraints into agent prompts
    and validate agent operations against approval policies.
    """

    @staticmethod
    def apply_constraints_to_prompt(step: WorkflowStep, base_prompt: str) -> str:
        """
        Apply approval gate constraints to agent prompt.
        
        Args:
            step: Workflow step with approval gates
            base_prompt: Original agent prompt
            
        Returns:
            Modified prompt with approval constraints injected
        """
        if not step.approval_gates:
            return base_prompt
        
        # Get all approval constraint messages
        constraints = step.get_approval_constraints()
        
        if not constraints:
            return base_prompt
        
        # Inject constraints into prompt
        # Place constraints prominently in the prompt (after main instructions)
        modified_prompt = f"{base_prompt}\n\n{constraints}"
        
        logger.info(
            f"Applied {len(step.approval_gates)} approval gate(s) to step {step.step_num}: {step.name}"
        )
        
        return modified_prompt

    @staticmethod
    def validate_operation(step: WorkflowStep, operation: str) -> bool:
        """
        Validate if an operation is allowed based on approval gates.
        
        Args:
            step: Workflow step with approval gates
            operation: Operation being attempted (e.g., "gh pr merge")
            
        Returns:
            True if operation is allowed, False if blocked
        """
        if not step.approval_gates:
            return True
        
        tool_restrictions = step.get_tool_restrictions()
        
        for restriction in tool_restrictions:
            if restriction.lower() in operation.lower():
                logger.warning(
                    f"Operation '{operation}' blocked by approval gate on step {step.step_num}"
                )
                return False
        
        return True

    @staticmethod
    def check_pr_merge_allowed(step: WorkflowStep) -> bool:
        """
        Check if PR merge operations are allowed for this step.
        
        Args:
            step: Workflow step to check
            
        Returns:
            True if PR merge allowed, False otherwise
        """
        return not step.has_approval_gate(ApprovalGateType.PR_MERGE)

    @staticmethod
    def get_gate_summary(step: WorkflowStep) -> Optional[str]:
        """
        Get human-readable summary of approval gates on this step.
        
        Args:
            step: Workflow step
            
        Returns:
            Summary string or None if no gates
        """
        if not step.approval_gates:
            return None
        
        active_gates = [gate for gate in step.approval_gates if gate.required]
        if not active_gates:
            return None
        
        gate_types = [gate.gate_type.value for gate in active_gates]
        return f"Active approval gates: {', '.join(gate_types)}"
