# AdminFlow Action Plan Design

Status: design baseline, 2026-08-16

## Purpose

AdminFlow should become a complete connector-neutral administrative workflow engine before production input and output connectors are added.

The Action Plan is the deterministic contract between human review and downstream execution. It answers one question clearly:

> What exactly will AdminFlow do if the reviewer approves this item?

The Action Plan must be understandable to a first-time office user, deterministic in application logic, auditable, expandable, and independent of any specific email, fax, EHR, CRM, scheduling, task-management, or other external connector.

This document defines the product and architecture contract. It is not yet an implementation prompt.

## Core architecture rule

AdminFlow keeps the existing boundary:

> AI handles fuzzy interpretation. Deterministic application code owns workflow state, validation, routing, timers, permissions, actions, and execution policy.

AI may classify, extract, summarize, identify possible uncertainty, and draft human-readable content. AI must not decide which office action is authorized, transition workflow state directly, or invoke a connector.

## Connector-neutral engine

The target engine is:

```text
Incoming source
      ↓
Normalized IntakeEvent / IntakeArtifact
      ↓
Native extraction / selective OCR
      ↓
AI classification + structured extraction
      ↓
Deterministic validation
      ↓
Decision Packet
      +
Proposed Action Plan
      ↓
Human correction / review
      ↓
Approved facts + approved Action Plan
      ↓
Deterministic Action Engine
      ↓
Action Execution result
      ↓
Output adapter / connector when one exists
```

Input and output connectors are replaceable edges around this engine. They must not become the engine itself.

## Three separate concepts

AdminFlow should keep these concepts distinct.

### Decision Packet

The human-facing explanation of the WorkItem:

- title
- document type
- confidence
- plain-language summary
- key information
- needs-attention items
- original source document
- next action
- human review controls

The Decision Packet answers:

> What came in, what does AdminFlow understand, what needs my attention, and what am I being asked to authorize?

### Action Plan

The deterministic, structured description of the action AdminFlow intends to perform after authorization.

The Action Plan answers:

> What exactly will happen if I approve?

### Action Execution

The record of what actually happened when AdminFlow attempted the authorized Action Plan.

The Action Execution answers:

> Was the authorized action attempted, did it succeed, and what result came back?

Approval, Action Plan, and Action Execution are separate events. Approval must never be treated as proof that the downstream action succeeded.

## Approval semantics

V1 approval should formally mean:

> I have verified the information shown in this Decision Packet and authorize AdminFlow to execute the displayed deterministic Action Plan using the reviewed data.

Approval authorizes both:

1. the reviewed facts snapshot; and
2. the exact Action Plan shown to the reviewer.

Approval must not mean merely "mark this WorkItem approved."

If the reviewed facts or proposed Action Plan change after review begins, prior approval must not apply to the changed version.

The existing WorkItem state/version protection should remain part of the stale-client defense. The future Action Plan should also have explicit identity/version lineage so authorization always references a specific plan.

## Cognitive human interface requirement

The Action Plan must be represented to the reviewer in ordinary administrative language.

The reviewer should see something like:

```text
WHAT WILL HAPPEN NEXT

When you approve this, AdminFlow will create an Accounts Payable task
for the Office Manager, due August 23, 2026. The original invoice and
the reviewed information above will be attached.

No external message will be sent.

[ Handle Manually ]          [ Approve & Create AP Task ]
```

The reviewer should not need to understand internal terms such as:

- action_type
- workflow_definition_id
- WorkItem version
- connector adapter
- structured extraction
- execution status enum

The interface should explain the consequence before asking for authorization.

The primary approval button should normally describe the action, for example:

- Approve & Create Task
- Approve & Route to Billing
- Approve & Schedule Follow-up
- Approve & Prepare Response
- future: Approve & Send Email
- future: Approve & Send Fax

A generic "Approve" button should be avoided when a specific consequence can be stated.

## Proposed Action Plan contract

The exact persistence model is intentionally deferred, but the domain contract should support the following information.

### Identity and lineage

Each Action Plan should retain:

- Action Plan ID
- WorkItem ID
- WorkItem state/version from which the plan was generated
- WorkflowDefinition ID/version
- source IntakeEvent lineage
- reviewed or proposed facts snapshot used to construct the plan
- plan version or equivalent immutable revision identity
- created time
- superseded time/reason when applicable

The original IntakeArtifact must remain directly reachable through WorkItem lineage before and after approval and execution.

### Human presentation

Each Action Plan should have deterministic presentation metadata such as:

- action title
- plain-language action description
- approval button label
- destination/owner description
- due-time description when applicable
- external-effect disclosure

Example:

```text
action_title: Create Accounts Payable task
action_description: Create an Accounts Payable task for the Office Manager,
                    due August 23, with the original invoice attached.
approval_label: Approve & Create AP Task
external_effect: No external message will be sent.
```

These values should be produced by application-owned templates/configuration, not invented by the AI provider.

### Action type

The plan should identify a deterministic action type understood by the Action Engine.

Initial native V1 action types should focus on actions AdminFlow itself can complete without production connectors:

- `create_internal_task`
- `route_internal`
- `schedule_follow_up`
- `manual_step`

Future connector-backed actions can be added behind adapters without changing the workflow/approval architecture, for example:

- `send_email`
- `send_fax`
- `create_external_task`
- `update_external_record`
- `submit_form`
- `schedule_external_event`

The workflow configuration selects the allowed action type. AI does not.

### Destination

The plan should state where responsibility or output goes.

For native V1 actions this may include:

- internal queue
- role
- named internal assignee
- WorkItem destination/state

Future connectors may resolve destinations such as an email address, fax number, external queue, system record, calendar, or API resource through connector-specific adapters.

The core engine should not embed connector-specific destination logic into workflow state transitions.

### Action payload

The Action Plan should contain the exact reviewed information needed to perform the action.

The payload should be application-validated and derived from the reviewed facts snapshot.

Example conceptual payload:

```json
{
  "action_type": "create_internal_task",
  "destination": {
    "queue": "accounts_payable",
    "role": "office_manager"
  },
  "task": {
    "title": "Review Acme Medical Supply invoice #18422",
    "due_at": "2026-08-23T17:00:00-04:00"
  },
  "facts": {
    "vendor_name": "Acme Medical Supply",
    "invoice_number": "18422",
    "amount_due": 1284.50,
    "due_date": "2026-08-30"
  },
  "attachments": [
    "original_source_artifact"
  ]
}
```

This example is conceptual only. It does not prescribe the eventual database/API schema.

### Source attachments

The Action Plan must state which source artifacts travel with the action.

For document-based work, the default should usually include the immutable original document.

A downstream task or connector should receive a reference to the preserved source rather than a newly generated copy that loses lineage.

### Timing

The plan may include deterministic timing such as:

- due date
- follow-up date/time
- delay until a specific time
- business-day offset
- no deadline

Timing rules are application/workflow rules, not AI decisions.

If a rule says "follow up in three business days," deterministic application code computes the actual date/time that appears in the Action Plan.

### Preconditions

The Action Plan should make approval eligibility deterministic.

Examples:

- required facts are present
- required human correction has been completed
- classification has been confirmed when confidence is below a configured threshold
- conflicting values have been resolved
- destination is valid
- action configuration exists

Blocking Needs Attention items should prevent approval until resolved.

Non-blocking warnings may remain visible while still allowing authorization.

### Idempotency

Action execution must be safe against duplicate clicks, retries, page reloads, and process restarts.

The future execution layer should use a deterministic idempotency identity tied to the authorized Action Plan so one authorization cannot accidentally produce duplicate downstream actions.

Retrying a failed execution should retry the same authorized plan unless the user explicitly creates and approves a revised plan.

## Action Plan revision behavior

The Action Plan should not be silently mutated after the reviewer has seen it.

Preferred behavior:

1. AdminFlow produces proposed facts and a proposed Action Plan.
2. The reviewer opens the Decision Packet.
3. The reviewer corrects a fact if needed.
4. Deterministic validation reruns.
5. If the correction changes the proposed action, destination, timing, payload, or human-facing consequence, AdminFlow produces a revised Action Plan.
6. The reviewer sees the revised "What Will Happen Next" section.
7. Approval references the exact revised plan and reviewed facts snapshot.
8. The approved plan becomes immutable for execution/audit purposes.

This prevents a human from approving one action while the application later executes another.

## Action Plan and Needs Attention

Needs Attention is part of the Decision Packet but directly affects Action Plan authorization.

Attention items may come from:

- AI observations
- deterministic required-field validation
- deterministic conflict checks
- deterministic business rules
- low classification confidence
- unavailable/invalid destination
- action configuration problems

Each attention item should conceptually be either:

- blocking; or
- non-blocking.

Example blocking item:

```text
Amount due could not be identified.
This value is required before an Accounts Payable task can be created.
```

Example non-blocking item:

```text
Purchase order number was not found.
You may still route this invoice for manual AP review.
```

The human interface should explain the issue and the consequence rather than expose internal validation codes.

## Native connectorless V1 actions

The engine should prove the full lifecycle without relying on an external integration.

### Create internal task

Example:

```text
Create an Accounts Payable task
Assigned queue: Accounts Payable
Owner role: Office Manager
Due: August 23, 2026
Attach: original invoice
Carry forward: vendor, invoice number, amount, due date
```

This should create a first-class AdminFlow administrative task that can later be completed, reassigned, or surfaced in an internal queue.

### Route internally

Example:

```text
Route this item to the Billing queue for review.
```

This is an internal deterministic responsibility change, not an external connector call.

### Schedule follow-up

Example:

```text
Bring this item back to the Front Desk queue in three business days.
```

The engine computes and stores the deterministic follow-up time.

### Manual step

Example:

```text
Call the sender to obtain the missing reference number.
When complete, record the result and continue this WorkItem.
```

This lets AdminFlow represent office work it cannot automate yet while still preserving state, responsibility, due dates, source context, and audit history.

## Future connector boundary

When output connectors are added, they should implement a narrow adapter boundary.

The core Action Engine should provide an already-authorized, validated action request. The connector should perform only the external-system translation/execution needed for that target.

A connector must not:

- decide which workflow applies
- decide whether human approval is required
- choose a different action than the approved Action Plan
- reinterpret AI output to change workflow state
- mutate WorkItem state directly outside the Action Engine
- discard source lineage

A connector should return a normalized result such as:

- success/failure
- external identifier when available
- external timestamp when available
- sanitized error information
- retryability information when applicable

The Action Engine then deterministically records the result and changes workflow state.

## Workflow lifecycle after approval

Approval should no longer imply immediate completion.

Conceptually the workflow becomes:

```text
needs_review
      ↓
review approved
      ↓
approved_for_action
      ↓
action execution
   ┌──────────────┴──────────────┐
 success                       failure
   ↓                              ↓
awaiting_task_completion   action_needs_attention
   ↓
internal task completed
   ↓
completed
```

Action execution success and business-work completion are separate facts. For
`create_internal_task`, a successful Action Execution means the authorized task
was created and handed to its deterministic queue and responsible role. The
originating WorkItem remains open until a human explicitly completes that task.

The V1 handoff model is Queue + Responsible Role. A future named assignee may be
added separately; task creation does not falsely imply that a particular person
was assigned.

Manual handling should be a separate deterministic path, for example:

```text
needs_review
      ↓
Handle Manually
      ↓
manual_handling
```

The exact state names can be decided during implementation design, but the semantic distinction must remain:

- review decision
- authorization
- action attempt
- follow-up task completion
- action result

are separate facts.

## Human-facing behavior after approval

Approved items should retain the same useful Decision Packet rather than collapse into technical history.

Example:

```text
APPROVED

Acme Medical Supply — Invoice #18422
Invoice · High confidence

SUMMARY
...

KEY INFORMATION
...

ORIGINAL DOCUMENT
[ View Original Document ]

ACTION
Accounts Payable task created successfully
Assigned to: Office Manager
Due: August 23, 2026
Task ID: AP-1048

REVIEW
Approved by Ron
August 16, 2026 at 8:14 AM
Corrections: Amount due changed from $1,248.50 to $1,284.50
```

The permanent record should communicate:

```text
source → understanding → human decision → authorized action → execution result
```

## Handle Manually semantics

The human-facing alternative to approval should usually be `Handle Manually`, not `Reject`, because the employee is generally not rejecting the incoming document itself.

Handle Manually should mean:

> Do not execute the proposed Action Plan. Preserve the item, source document, review context, and reason so a person can complete or reroute the work manually.

The internal review decision can still use deterministic enum/state names, but the interface should use language that matches the office worker's intent.

## One Action Plan versus multiple steps

The design should remain expandable to multi-step administrative work, but V1 should avoid unnecessary orchestration complexity.

Recommended V1 rule:

- one approved Action Plan has one primary executable action
- that action may create additional internal work that continues through normal WorkItems/tasks
- do not build autonomous multi-action chains in the first Action Engine slice

A future version can add ordered action steps while preserving the same authorization and execution principles.

## Engine-complete V1 target, excluding connectors

The connectorless AdminFlow engine should not be considered complete until it can demonstrate the entire lifecycle through manual intake and native actions:

1. Receive and preserve an original document.
2. Extract readable text with selective OCR when needed.
3. Classify the document.
4. Extract structured facts.
5. Produce a plain-language summary.
6. Produce deterministic Needs Attention items.
7. Produce a cognitive Decision Packet.
8. Produce a deterministic Action Plan.
9. Keep the original document directly accessible from the Decision Packet.
10. Allow a human to correct information.
11. Revalidate and revise the Action Plan when corrections materially change it.
12. Explain exactly what approval will cause.
13. Capture human authorization against an exact facts/plan snapshot.
14. Execute at least the native connectorless action types.
15. Record success/failure separately from approval.
16. Support manual handling.
17. Preserve permanent source, review, action, and execution history.

Once this lifecycle works well, production input/output connectors can be added as adapters rather than as missing pieces of the core engine.

## Non-goals for this design slice

This document does not authorize implementation of:

- email connectors
- fax connectors
- EHR connectors
- CRM connectors
- calendar connectors
- autonomous AI action selection
- AI-controlled workflow transitions
- healthcare-specific workflow assumptions in the core engine
- multi-agent orchestration
- background infrastructure beyond what the chosen deterministic execution design actually requires

## Design invariants to preserve

1. Original source artifacts remain immutable and directly accessible.
2. AI-derived records retain lineage and do not control workflow state.
3. Human corrections do not rewrite immutable AI source records; reviewed data is preserved as a separate authoritative snapshot for the action.
4. The exact Action Plan shown to the reviewer is the plan being authorized.
5. Material changes after review require a revised plan and fresh authorization.
6. Approval and execution success are separate events.
7. Action execution is deterministic and idempotent.
8. External systems are reached only through adapters/connectors.
9. Connector code never becomes the workflow engine.
10. The human interface explains what happened, what needs attention, and what happens next without requiring knowledge of AdminFlow internals.

## Next design artifact

After this Action Plan contract is accepted, the next foundational design should define the `Action Execution` contract in the same way: execution lifecycle, status/result model, retry/idempotency behavior, native internal task representation, and how deterministic workflow transitions react to success or failure.
