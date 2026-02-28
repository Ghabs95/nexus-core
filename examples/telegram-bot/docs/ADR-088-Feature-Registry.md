# ADR-088: Feature Registry for Ideation Dedup

## Status
Proposed

## Context
The Nexus Telegram Bot provides feature ideation capabilities. To improve user experience and avoid repetitive suggestions, we need a way to track implemented features and filter them out during ideation.

## Decision
We will implement a `FeatureRegistryService` within the `examples/telegram-bot` application to manage a registry of implemented features per project.

### 1. Data Model
Each feature record will contain:
- `project_key`: The project identifier.
- `feature_id`: A unique identifier (e.g., issue number or slug).
- `canonical_title`: The primary name of the feature.
- `aliases`: Alternative names for the feature to improve fuzzy matching.
- `source_issue`: The issue number that triggered the implementation.
- `source_pr`: The pull request URL associated with the implementation.
- `status`: Current status (default: `implemented`).
- `implemented_at`: Timestamp of implementation.
- `updated_at`: Timestamp of the last update.

### 2. Storage Backends
The service will support two backends, selected via `NEXUS_STORAGE_BACKEND`:
- **Filesystem**: A JSON file (`feature_registry.json`) in the bot's state directory.
- **Postgres**: A new table `nexus_feature_registry` managed via SQLAlchemy.

### 3. Ideation Integration
- **Prompt Injection**: Before calling the LLM for ideation, the service will fetch implemented features for the project and inject them as an "Exclude List" in the persona prompt.
- **Post-Filtering**: Model output will be filtered using fuzzy matching (SequenceMatcher) against the registry with a configurable similarity threshold (default `0.86`).

### 4. Completion Ingestion
When a workflow is finalized (issue closed), the system will:
- Parse the structured completion summary.
- Extract the feature title (usually the issue title).
- Upsert the feature into the registry.

### 5. Manual Commands
Operators can manage the registry via Telegram:
- `/feature_done <project> <title>`: Manually mark a feature as implemented.
- `/feature_list <project>`: List implemented features for a project.
- `/feature_forget <project> <feature_id|title>`: Remove a feature from the registry.

## Consequences
- **Pros**: Reduced redundancy in AI suggestions, better tracking of project progress.
- **Cons**: Minor overhead in ideation latency (registry lookup), potential for false positives/negatives in fuzzy matching.
