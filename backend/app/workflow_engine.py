from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models import WorkItem, WorkflowDefinition
from app.schemas import (
    WorkflowStateDefinition,
    WorkflowTransitionDefinition,
)


class WorkflowDefinitionError(ValueError):
    pass


class WorkflowTransitionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class TransitionResult:
    version: int
    from_state: str
    to_state: str


def validate_workflow_graph(
    states: list[WorkflowStateDefinition],
    initial_state: str,
    transitions: list[WorkflowTransitionDefinition],
) -> None:
    state_names = [state.name for state in states]
    if len(state_names) != len(set(state_names)):
        raise WorkflowDefinitionError("state names must be unique")
    state_set = set(state_names)
    if initial_state not in state_set:
        raise WorkflowDefinitionError("initial_state must reference a defined state")

    pairs = [(edge.from_state, edge.to_state) for edge in transitions]
    if len(pairs) != len(set(pairs)):
        raise WorkflowDefinitionError("workflow transitions must be unique")
    for from_state, to_state in pairs:
        if from_state not in state_set or to_state not in state_set:
            raise WorkflowDefinitionError(
                "workflow transitions must reference defined states"
            )
        if from_state == to_state:
            raise WorkflowDefinitionError("workflow self-transitions are not allowed")

    terminal_states = {state.name for state in states if state.terminal}
    if not terminal_states:
        raise WorkflowDefinitionError("workflow must contain a terminal state")
    if any(from_state in terminal_states for from_state, _ in pairs):
        raise WorkflowDefinitionError("terminal states cannot have outgoing transitions")

    state_map = {state.name: state for state in states}
    if any(state.terminal and state.review_required for state in states):
        raise WorkflowDefinitionError("terminal states cannot require review")
    decisions_by_state: dict[str, list[str]] = defaultdict(list)
    for edge in transitions:
        source_requires_review = state_map[edge.from_state].review_required
        if source_requires_review and edge.review_decision is None:
            raise WorkflowDefinitionError(
                "transitions leaving a review state require review_decision"
            )
        if not source_requires_review and edge.review_decision is not None:
            raise WorkflowDefinitionError(
                "transitions leaving a normal state cannot have review_decision"
            )
        if edge.review_decision is not None:
            decisions_by_state[edge.from_state].append(edge.review_decision)
    for state in states:
        if not state.review_required:
            continue
        decisions = decisions_by_state[state.name]
        if decisions.count("reject") > 1 or len(decisions) != len(set(decisions)):
            raise WorkflowDefinitionError(
                "review decisions from a state must be unique"
            )
        if decisions.count("approve") != 1:
            raise WorkflowDefinitionError(
                "review states require exactly one approve transition"
            )

    adjacency: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    for from_state, to_state in pairs:
        adjacency[from_state].add(to_state)
        reverse[to_state].add(from_state)

    reachable = _walk(initial_state, adjacency)
    if reachable != state_set:
        raise WorkflowDefinitionError("every state must be reachable from initial_state")

    can_reach_terminal: set[str] = set()
    queue = deque(terminal_states)
    while queue:
        state = queue.popleft()
        if state in can_reach_terminal:
            continue
        can_reach_terminal.add(state)
        queue.extend(reverse[state])
    nonterminal_states = state_set - terminal_states
    if not nonterminal_states <= can_reach_terminal:
        raise WorkflowDefinitionError(
            "every nonterminal state must have a path to a terminal state"
        )


def _walk(start: str, adjacency: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    queue = deque([start])
    while queue:
        state = queue.popleft()
        if state in visited:
            continue
        visited.add(state)
        queue.extend(adjacency[state])
    return visited


def apply_transition(
    work_item: WorkItem,
    workflow: WorkflowDefinition,
    *,
    expected_state: str,
    expected_version: int,
    to_state: str,
    allow_review: bool = False,
) -> TransitionResult:
    if expected_state != work_item.current_state:
        raise WorkflowTransitionConflict("WorkItem state has changed")
    if expected_version != work_item.version:
        raise WorkflowTransitionConflict("WorkItem version has changed")

    state_map = {state["name"]: state for state in workflow.states}
    current_definition = state_map[work_item.current_state]
    if current_definition.get("review_required", False) and not allow_review:
        raise WorkflowTransitionConflict(
            "Human review is required for the current state"
        )
    if current_definition.get("terminal", False):
        raise WorkflowTransitionConflict("Terminal WorkItems cannot transition")
    if to_state not in state_map:
        raise WorkflowTransitionConflict("Target state is not defined by the workflow")
    allowed_pairs = {
        (edge["from_state"], edge["to_state"]) for edge in workflow.transitions
    }
    if (work_item.current_state, to_state) not in allowed_pairs:
        raise WorkflowTransitionConflict("Workflow transition is not allowed")

    from_state = work_item.current_state
    work_item.current_state = to_state
    work_item.version += 1
    work_item.updated_at = datetime.now(timezone.utc)
    return TransitionResult(
        version=work_item.version,
        from_state=from_state,
        to_state=to_state,
    )
