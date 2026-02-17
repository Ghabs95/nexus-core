"""
Basic Workflow Example - Demonstrates core Nexus framework usage.

This example shows:
1. Creating a simple 3-step workflow
2. Using file storage for persistence
3. Managing workflow state
4. Audit logging
"""
import asyncio
from pathlib import Path

from nexus.adapters.storage import FileStorage
from nexus.core.models import Agent, Workflow, WorkflowStep
from nexus.core.workflow import WorkflowEngine


async def main():
    """Run a basic workflow example."""
    
    # 1. Setup storage
    storage = FileStorage(base_path="./data")
    print("✓ Initialized file storage at ./data")
    
    # 2. Create workflow engine
    engine = WorkflowEngine(storage=storage)
    print("✓ Created workflow engine")
    
    # 3. Define agents
    triage_agent = Agent(
        name="TriageAgent",
        display_name="Triage Specialist",
        description="Analyzes incoming requests and determines priority",
        timeout=300,
        max_retries=2
    )
    
    design_agent = Agent(
        name="DesignAgent",
        display_name="Technical Designer",
        description="Creates technical design for feature requests",
        timeout=600,
        max_retries=3
    )
    
    impl_agent = Agent(
        name="ImplementationAgent",
        display_name="Implementation Lead",
        description="Implements features according to design",
        timeout=1800,
        max_retries=3
    )
    
    print("✓ Defined 3 agents")
    
    # 4. Create workflow with steps
    workflow = Workflow(
        id="demo-workflow-001",
        name="Feature Development",
        version="1.0",
        description="Simple 3-step feature development workflow",
        steps=[
            WorkflowStep(
                step_num=1,
                name="triage",
                agent=triage_agent,
                prompt_template="Analyze this feature request: {description}",
            ),
            WorkflowStep(
                step_num=2,
                name="design",
                agent=design_agent,
                prompt_template="Create technical design for: {triage.output}",
                condition="triage.complexity == 'high'"  # Only run for complex features
            ),
            WorkflowStep(
                step_num=3,
                name="implement",
                agent=impl_agent,
                prompt_template="Implement: {design.output}",
            ),
        ]
    )
    
    print(f"✓ Created workflow: {workflow.name}")
    
    # 5. Persist workflow
    await engine.create_workflow(workflow)
    print(f"✓ Saved workflow with ID: {workflow.id}")
    
    # 6. Start workflow execution
    workflow = await engine.start_workflow(workflow.id)
    print(f"✓ Started workflow (state: {workflow.state.value})")
    
    # 7. Simulate step execution
    print("\n--- Simulating Workflow Execution ---\n")
    
    # Step 1: Triage
    print("Step 1: Triage Agent analyzing request...")
    await engine.complete_step(
        workflow_id=workflow.id,
        step_num=1,
        outputs={
            "complexity": "high",
            "priority": "P1",
            "estimated_hours": 40
        }
    )
    print("✓ Step 1 completed: Complexity = high, Priority = P1")
    
    # Step 2: Design
    print("\nStep 2: Design Agent creating technical spec...")
    await engine.complete_step(
        workflow_id=workflow.id,
        step_num=2,
        outputs={
            "architecture": "microservices",
            "components": ["api", "database", "frontend"],
            "design_doc_url": "https://example.com/design.md"
        }
    )
    print("✓ Step 2 completed: Design documented")
    
    # Step 3: Implementation
    print("\nStep 3: Implementation Agent writing code...")
    await engine.complete_step(
        workflow_id=workflow.id,
        step_num=3,
        outputs={
            "pr_url": "https://github.com/example/repo/pull/42",
            "files_changed": 15,
            "tests_added": 8
        }
    )
    print("✓ Step 3 completed: Code implemented")
    
    # 8. Check final state
    workflow = await engine.get_workflow(workflow.id)
    print(f"\n✓ Workflow completed (state: {workflow.state.value})")
    print(f"  Duration: {(workflow.completed_at - workflow.created_at).total_seconds():.2f}s")
    
    # 9. View audit log
    audit_log = await engine.get_audit_log(workflow.id)
    print(f"\n--- Audit Log ({len(audit_log)} events) ---")
    for event in audit_log:
        print(f"  [{event.timestamp.strftime('%H:%M:%S')}] {event.event_type}")
        if event.data:
            for key, value in event.data.items():
                print(f"    - {key}: {value}")
    
    # 10. Demonstrate pause/resume
    print("\n--- Testing Pause/Resume ---")
    
    # Create a new workflow
    workflow2 = Workflow(
        id="demo-workflow-002",
        name="Test Pause/Resume",
        version="1.0",
        steps=[
            WorkflowStep(
                step_num=1,
                name="step1",
                agent=triage_agent,
                prompt_template="Do something"
            )
        ]
    )
    
    await engine.create_workflow(workflow2)
    await engine.start_workflow(workflow2.id)
    print("✓ Created and started second workflow")
    
    # Pause it
    workflow2 = await engine.pause_workflow(workflow2.id)
    print(f"✓ Paused workflow (state: {workflow2.state.value})")
    
    # Resume it
    workflow2 = await engine.resume_workflow(workflow2.id)
    print(f"✓ Resumed workflow (state: {workflow2.state.value})")
    
    print("\n✨ Example complete! Check ./data directory for persisted state.")


if __name__ == "__main__":
    print("=" * 60)
    print(" Nexus Core - Basic Workflow Example")
    print("=" * 60)
    print()
    
    asyncio.run(main())
