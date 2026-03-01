# Obsidian Vault Integration for terra4mice

> Phase 6 Feature: Treat Obsidian vaults as both spec source and state backend.
> Status: DESIGN COMPLETE, IMPLEMENTATION PLANNED
> Author: Ultravioleta DAO
> Date: 2026-03-01

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Why Obsidian Vaults](#2-why-obsidian-vaults)
3. [Obsidian Vault Architecture (Reference)](#3-obsidian-vault-architecture-reference)
4. [Integration Overview](#4-integration-overview)
5. [Vault Layout for terra4mice](#5-vault-layout-for-terra4mice)
6. [A. ObsidianBackend (State Storage)](#6-a-obsidianbackend-state-storage)
7. [B. ObsidianSpecLoader (Spec from Vault)](#7-b-obsidianspecloader-spec-from-vault)
8. [C. Plan/State Export to Obsidian](#8-c-planstate-export-to-obsidian)
9. [D. Obsidian as Inference Source (5th Strategy)](#9-d-obsidian-as-inference-source-5th-strategy)
10. [E. CLI Commands](#10-e-cli-commands)
11. [F. Canvas Generation (Dependency Graph)](#11-f-canvas-generation-dependency-graph)
12. [G. Bases / Dataview Integration](#12-g-bases--dataview-integration)
13. [Data Format Specification](#13-data-format-specification)
14. [Dependencies and Installation](#14-dependencies-and-installation)
15. [Files to Modify](#15-files-to-modify)
16. [Test Plan](#16-test-plan)
17. [Implementation Phases](#17-implementation-phases)
18. [Risks and Mitigations](#18-risks-and-mitigations)
19. [Ecosystem Comparison](#19-ecosystem-comparison)
20. [Python Libraries Reference](#20-python-libraries-reference)
21. [Future Possibilities](#21-future-possibilities)

---

## 1. Executive Summary

This document describes the integration of Obsidian vaults as a first-class backend for terra4mice. The integration allows developers to:

- **Define specs as Obsidian notes** instead of (or alongside) YAML files
- **Store state in vault frontmatter** instead of JSON files or S3
- **Visualize dependency graphs** via Obsidian's Graph View (zero code)
- **Query convergence** via Dataview/Bases plugins (zero code)
- **Edit resource status** in a rich markdown editor with bidirectional sync
- **Publish dashboards** via Obsidian Publish or Digital Garden

The integration requires **zero new dependencies** (uses stdlib + pyyaml, already in the project). `python-frontmatter` is added as an optional dependency for ergonomics.

**No existing tool bridges the Terraform mental model with Obsidian.** terra4mice would be the first.

---

## 2. Why Obsidian Vaults

### 2.1 Why This Integration Makes Sense

| terra4mice Concept | Obsidian Natural Mapping |
|---|---|
| `Resource` (atomic unit) | Individual `.md` note |
| `type.name` address | `type/name.md` file path |
| `depends_on` references | `[[wikilinks]]` between notes |
| `State` (collection of resources) | Folder of notes + `_index.md` |
| `Spec` (desired state) | Notes tagged `terra4mice_spec: true` |
| `Plan` (diff output) | `_dashboard.md` with status table |
| `ResourceStatus` enum | `status:` field in YAML frontmatter |
| `confidence` score | `confidence:` field in YAML frontmatter |
| Dependency graph | Graph View (built-in, automatic) |
| Convergence queries | Dataview TABLE queries on frontmatter |
| `serial` counter | `serial:` in `_index.md` frontmatter |
| `locked` flag | `locked: true` in frontmatter |
| Human context/notes | Markdown body below `---` fence (always preserved) |

### 2.2 Key Advantages

1. **Zero runtime dependency**: Vault files are plain `.md` on disk. terra4mice reads/writes them without Obsidian running.
2. **Graph View for free**: Wikilinks between resource notes automatically create a visual dependency graph.
3. **Dataview/Bases for free**: Frontmatter fields are queryable without any terra4mice-specific code.
4. **Git-native**: Vaults are just folders. Git tracks changes, enables CI/CD, supports team collaboration.
5. **Human-friendly editing**: Developers edit specs in a rich markdown editor, not raw YAML.
6. **Bidirectional**: terra4mice can read from AND write to the vault.
7. **Adoption path**: Obsidian users can adopt terra4mice incrementally by adding frontmatter to existing notes.
8. **No vendor lock-in**: Files are standard Markdown with YAML frontmatter. Works without Obsidian too.

### 2.3 What We Decided NOT To Do (and Why)

| Approach | Reason to Skip |
|----------|---------------|
| Native Obsidian plugin (TypeScript) | Second codebase, separate maintenance, limited to Obsidian users |
| Depend on Local REST API | Requires Obsidian running, adds HTTP complexity |
| Complex CRDT-based bidirectional sync | Over-engineering for v1. Simple last-write-wins is sufficient |
| Real-time file watching | Out of scope. terra4mice is a CLI tool, not a daemon |

---

## 3. Obsidian Vault Architecture (Reference)

### 3.1 What is an Obsidian Vault

An Obsidian vault is a **folder on the local filesystem** containing Markdown files, attachments, and a `.obsidian/` configuration directory. There is no proprietary database or binary format.

```
my-vault/
  .obsidian/                    # Config (JSON files)
    app.json                    # Core settings
    appearance.json             # Theme settings
    community-plugins.json      # Installed plugins list
    core-plugins.json           # Core plugin states
    types.json                  # Property type definitions (vault-wide)
    plugins/                    # Per-plugin data
      dataview/data.json
      obsidian-git/data.json
    themes/                     # CSS themes
    snippets/                   # CSS snippets
  notes/                        # User folders (arbitrary structure)
  attachments/                  # Images, PDFs, etc.
  templates/                    # Template notes
  *.md                          # Markdown notes
```

### 3.2 YAML Frontmatter and Properties

Since v1.4 (August 2023), Obsidian has first-class **Properties** -- a typed UI for YAML frontmatter:

```yaml
---
title: Authentication Module
status: partial
confidence: 0.7
type: feature
due: 2026-04-01
tags:
  - terra4mice/resource
  - sprint/3
aliases:
  - auth
---
```

**Supported property types**: Text, List, Number, Checkbox (boolean), Date, Date & Time.

Property types are **vault-wide**: once `status` is defined as "text", it is "text" in every note. Stored in `.obsidian/types.json`.

**Search integration**: `[property:value]` syntax (e.g., `[status:partial]`).

### 3.3 Wikilinks and Graph View

| Pattern | Syntax | Example |
|---------|--------|---------|
| File link | `[[Note Name]]` | `[[state_manager]]` |
| Path link | `[[folder/note]]` | `[[module/models]]` |
| Display text | `[[target\|display]]` | `[[module/models\|module.models]]` |
| Heading link | `[[Note#Heading]]` | `[[models#Resource]]` |
| Block link | `[[Note#^blockid]]` | `[[models#^resource-def]]` |
| Embed | `![[Note]]` | `![[dashboard]]` |

**Graph View** renders notes as nodes, wikilinks as edges. Features: force-directed layout, filtering by tag/folder/search, color-coding by group, orphan detection, local graph per note.

### 3.4 Dataview Plugin

Dataview (10M+ downloads) indexes all frontmatter and provides SQL-like queries:

```dataview
TABLE status, confidence, file.mtime AS "Updated"
FROM "terra4mice"
WHERE status != "implemented"
SORT confidence ASC
```

Query types: `TABLE`, `LIST`, `TASK`, `CALENDAR`. Supports `FROM`, `WHERE`, `SORT`, `GROUP BY`, `FLATTEN`, `LIMIT`.

**DataviewJS** for full JavaScript:
```dataviewjs
const pages = dv.pages('"terra4mice"')
  .where(p => p.status === "partial")
  .sort(p => p.confidence, 'desc');
dv.table(["Resource", "Status", "Confidence"],
  pages.map(p => [p.file.link, p.status, p.confidence]));
```

**Implicit fields**: Every note has `file.name`, `file.path`, `file.folder`, `file.size`, `file.ctime`, `file.mtime`, `file.tags`, `file.links`, `file.outlinks`, `file.inlinks`, `file.tasks`.

### 3.5 Bases Plugin (Core, v1.9+)

Native database views over vault notes (Notion-style). Uses `.base` files (YAML):

```yaml
filters:
  - 'file.folder == "terra4mice"'
formulas:
  progress: 'if(status == "implemented", 100, if(status == "partial", confidence * 100, 0))'
views:
  - type: table
    name: "Convergence"
    order:
      - status: asc
```

Supports: table, list, cards, map views. Formulas with arithmetic, date math, string ops, list ops.

### 3.6 Canvas (JSON Canvas)

Open specification for spatial canvases. `.canvas` files are JSON:

```json
{
  "nodes": [
    {"id": "1", "type": "text", "text": "module.models", "x": 0, "y": 0, "width": 200, "height": 100, "color": "4"}
  ],
  "edges": [
    {"id": "e1", "fromNode": "1", "toNode": "2", "fromSide": "bottom", "toSide": "top"}
  ]
}
```

Node types: `text`, `file` (vault reference), `link` (URL), `group` (container). Colors: hex or presets `"1"`-`"6"` (red, orange, yellow, green, cyan, purple).

### 3.7 Obsidian Git Plugin

Full Git integration (9.9k stars). Features: auto-commit on interval, source control view, diff view, branch management, remote push/pull. Enables team collaboration via Git workflows.

### 3.8 Obsidian URI Scheme

Deep linking: `obsidian://open?vault=MyVault&file=path/to/note`. terra4mice CLI output can include clickable links to open relevant notes.

### 3.9 Templates and Templater

**Templater plugin** supports dynamic templates with JavaScript:

```markdown
---
type: <% tp.system.prompt("Resource type") %>
status: missing
confidence: 0.0
created: <% tp.date.now("YYYY-MM-DD") %>
tags:
  - terra4mice/resource
---
# <% tp.file.title %>
```

Can auto-apply templates when creating notes in specific folders.

### 3.10 Local REST API Plugin

HTTPS server inside Obsidian for external tool access:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/vault/{path}` | Read note content |
| PUT | `/vault/{path}` | Create/overwrite note |
| PATCH | `/vault/{path}` | Insert into section / update frontmatter |
| DELETE | `/vault/{path}` | Delete note |
| POST | `/search/` | Search vault (Dataview DQL, text, JsonLogic) |
| GET | `/commands/` | List commands |
| POST | `/commands/{id}` | Execute command |

Auth: Bearer token. Ports: 27124 (HTTPS), 27123 (HTTP). Content types: `text/markdown`, `application/json`, `application/vnd.olrapi.note+json`.

### 3.11 MCP Servers

Multiple Model Context Protocol servers for AI agent access:
- `obsidian-mcp-server`: Full vault CRUD, search, frontmatter, tags
- `obsidian-mcp` (PyPI): Direct filesystem access, no plugin required
- `ObsidianPilot` (PyPI): Token-efficient editing

---

## 4. Integration Overview

### 4.1 Architecture Diagram

```
                    +-----------------+
                    |  Obsidian App   |
                    |  (optional)     |
                    |  Graph View     |
                    |  Dataview       |
                    |  Bases          |
                    |  Canvas         |
                    +--------+--------+
                             |
                    watches filesystem
                             |
+-------------------+--------v--------+-------------------+
|                  OBSIDIAN VAULT (filesystem)             |
|                                                          |
|  terra4mice/                                             |
|    _index.md          (state serial, version)            |
|    _dashboard.md      (convergence stats)                |
|    _plan.canvas       (dependency graph)                 |
|    _convergence.base  (Bases database view)              |
|    module/                                               |
|      models.md        (resource note)                    |
|      spec_parser.md   (resource note)                    |
|    feature/                                              |
|      auth_login.md    (resource note)                    |
+-------------------+--------+--------+-------------------+
                             |
                    read/write .md files
                             |
              +--------------+--------------+
              |                             |
     +--------v--------+          +--------v--------+
     | ObsidianBackend |          | ObsidianSpec    |
     | (StateBackend)  |          | Loader          |
     |                 |          |                 |
     | read() -> bytes |          | vault -> Spec   |
     | write(bytes)    |          | frontmatter ->  |
     | exists() -> bool|          |   parse_spec()  |
     +--------+--------+          +--------+--------+
              |                             |
     +--------v-----------------------------v--------+
     |              terra4mice core                   |
     |                                                |
     |  StateManager  Planner  InferenceEngine  CI   |
     +------------------------------------------------+
```

### 4.2 Data Flow

**Spec from vault (read)**:
```
Vault notes with terra4mice_spec: true
  -> parse YAML frontmatter per note
  -> extract wikilink dependencies from body
  -> assemble dict matching spec schema
  -> pass to parse_spec() (unchanged)
  -> Spec object
```

**State to vault (write)**:
```
StateManager.save()
  -> _serialize_state() -> JSON bytes
  -> ObsidianBackend.write(bytes)
  -> parse JSON into resource list
  -> for each resource: write/update type/name.md
     -> replace ONLY frontmatter (preserve human body)
  -> update _index.md with serial/version
```

**State from vault (read)**:
```
ObsidianBackend.read()
  -> read _index.md frontmatter (version, serial)
  -> walk type/ subdirectories
  -> for each .md: parse frontmatter -> resource dict
  -> assemble JSON blob
  -> return as bytes
-> StateManager._parse_state() (unchanged)
-> State object
```

---

## 5. Vault Layout for terra4mice

### 5.1 Directory Structure

```
vault-root/
  terra4mice/                      # Configurable subfolder (default: "terra4mice")
    _index.md                      # State metadata (serial, version, resource list)
    _dashboard.md                  # Convergence dashboard (auto-generated)
    _plan.canvas                   # Dependency graph visualization (auto-generated)
    _convergence.base              # Bases database view definition (auto-generated)
    module/                        # Resource type "module"
      models.md                    #   module.models
      spec_parser.md               #   module.spec_parser
      state_manager.md             #   module.state_manager
      planner.md                   #   module.planner
      inference.md                 #   module.inference
      analyzers.md                 #   module.analyzers
      cli.md                       #   module.cli
      backends.md                  #   module.backends
    feature/                       # Resource type "feature"
      context_registry.md          #   feature.context_registry
      context_cli.md               #   feature.context_cli
      context_export.md            #   feature.context_export
    example/                       # Resource type "example"
      demo_project.md
      ultratrack_demo.md
    docs/                          # Resource type "docs"
      readme.md
      spec_doc.md
    config/                        # Resource type "config"
      pyproject.md
    dogfood/                       # Resource type "dogfood"
      seal_registry.md
```

### 5.2 Naming Conventions

| Concept | Convention | Example |
|---------|-----------|---------|
| Resource type | Subfolder name | `module/`, `feature/`, `endpoint/` |
| Resource name | Filename (stem) | `state_manager.md` |
| Full address | `type.name` | `module.state_manager` |
| Path in vault | `subfolder/type/name.md` | `terra4mice/module/state_manager.md` |
| System files | Prefixed with `_` | `_index.md`, `_dashboard.md` |
| Wikilink format | `[[type/name\|type.name]]` | `[[module/models\|module.models]]` |

### 5.3 Files Managed by terra4mice

| File | Purpose | Auto-generated | Body preserved |
|------|---------|---------------|----------------|
| `_index.md` | State serial, version, resource listing | Yes (on write) | Yes |
| `_dashboard.md` | Convergence stats, status table | Yes (on export) | No (regenerated) |
| `_plan.canvas` | Dependency graph visualization | Yes (on export) | No (regenerated) |
| `_convergence.base` | Bases database view config | Yes (on init) | No (regenerated) |
| `type/name.md` | Per-resource state/spec note | Yes (on write) | **Yes (always)** |

---

## 6. A. ObsidianBackend (State Storage)

### 6.1 Overview

Extends the existing `StateBackend` ABC in `backends.py`. Stores state as individual markdown notes with YAML frontmatter per resource, plus an `_index.md` note for metadata.

The critical design constraint: `read()` returns `Optional[bytes]` (a JSON blob) and `write()` accepts `bytes`. The backend must reassemble/decompose JSON from/to scattered markdown notes.

### 6.2 Class Definition

```python
class ObsidianBackend(StateBackend):
    """
    Obsidian vault backend for terra4mice state storage.

    State is stored as individual markdown notes with YAML frontmatter
    per resource, organized in type/ subdirectories. An _index.md note
    holds metadata (version, serial, last_updated).

    Key behaviors:
    - read() reassembles JSON blob from individual note frontmatters
    - write() diffs incoming state, updates only changed notes
    - Human-written body content is ALWAYS preserved on write
    - Notes with terra4mice: true are managed; others are untouched
    - No locking (vaults are typically single-user)
    """

    FRONTMATTER_FENCE = "---"
    INDEX_FILENAME = "_index.md"
    DASHBOARD_FILENAME = "_dashboard.md"

    def __init__(self, vault_path: Path, subfolder: str = "terra4mice"):
        self.vault_path = Path(vault_path)
        self.base_path = self.vault_path / subfolder
        self._subfolder = subfolder

    def read(self) -> Optional[bytes]: ...
    def write(self, data: bytes) -> None: ...
    def exists(self) -> bool: ...

    @property
    def backend_type(self) -> str:
        return "obsidian"

    @property
    def supports_locking(self) -> bool:
        return False
```

### 6.3 `read()` Algorithm

1. Check if `_index.md` exists. If not, return `None` (no state).
2. Parse `_index.md` frontmatter for `version`, `serial`, `last_updated`.
3. Walk subdirectories of `base_path` (skip `_`-prefixed dirs/files).
4. For each `.md` file in type subdirectories:
   a. Parse YAML frontmatter.
   b. Extract resource fields: `type`, `name`, `status`, `locked`, `source`, `attributes`, `depends_on`, `files`, `tests`, `symbols`, timestamps.
   c. Append to resources list.
5. Assemble JSON dict: `{"version", "serial", "last_updated", "resources": [...]}`.
6. Serialize to JSON bytes and return.

### 6.4 `write()` Algorithm

1. Deserialize incoming JSON bytes to dict.
2. Ensure `base_path` directory exists.
3. Write/update `_index.md`:
   a. Set frontmatter: `version`, `serial`, `last_updated`, `terra4mice: true`, `type: index`.
   b. Generate default body: resource count, status breakdown, resource list with wikilinks.
   c. **Preserve existing body** if `_index.md` already exists.
4. Track written paths (for deletion detection).
5. For each resource in state:
   a. Create `type/` subdirectory if needed.
   b. Build frontmatter dict with all resource fields.
   c. Generate default body with wikilinks for dependencies, file listings, notes section.
   d. Call `_write_note()` which **only replaces frontmatter, preserves body**.
   e. Add path to written set.
6. For `.md` files in vault that are NOT in written set:
   a. Check if they have `terra4mice: true` in frontmatter.
   b. If yes, delete (resource was removed from state).
   c. If no, leave untouched (human-created note, not managed by terra4mice).
7. Clean up empty type directories.

### 6.5 Human Content Preservation (Critical)

The `_write_note()` method is the most important piece:

```python
def _write_note(self, path: Path, frontmatter: dict, default_body: str = "") -> None:
    if path.exists():
        existing_body = self._read_body(path)  # Everything below closing ---
    else:
        existing_body = default_body

    fm_text = yaml.dump(frontmatter, default_flow_style=False,
                        allow_unicode=True, sort_keys=False).rstrip()

    content = f"---\n{fm_text}\n---\n\n{existing_body}"
    path.write_text(content, encoding="utf-8")
```

The body is defined as everything after the closing `---` frontmatter fence. On update, only the frontmatter block is replaced. The body -- including human-written notes, design decisions, images, embeds, and wikilinks -- is **never modified**.

### 6.6 Factory Registration

In `create_backend()`:

```python
if backend_type == "obsidian":
    config = backend_config.get("config", {})
    vault_path = config.get("vault_path")
    if not vault_path:
        raise ValueError("Obsidian backend requires 'vault_path' in config")
    subfolder = config.get("subfolder", "terra4mice")
    return ObsidianBackend(vault_path=Path(vault_path), subfolder=subfolder)
```

### 6.7 Spec YAML Configuration

```yaml
backend:
  type: obsidian
  config:
    vault_path: "C:/Users/lxhxr/Documents/MyVault"
    subfolder: "terra4mice"    # default: "terra4mice"
```

Backend resolution priority (unchanged): `--state` CLI flag > spec `backend:` config > default local file.

---

## 7. B. ObsidianSpecLoader (Spec from Vault)

### 7.1 Overview

A new function in `spec_parser.py` that parses Obsidian vault notes as a spec. Each note with `terra4mice_spec: true` (or a configured tag) becomes a spec resource.

### 7.2 Function Signature

```python
def load_spec_from_obsidian(
    vault_path: Union[str, Path],
    subfolder: str = "terra4mice",
    tag: str = "terra4mice/spec",
) -> Spec:
```

### 7.3 Resource Type Resolution

Resource type is determined by (in priority order):
1. Explicit `type:` field in frontmatter
2. Parent folder name relative to subfolder (e.g., `terra4mice/module/` -> type `module`)
3. Default: `"feature"`

### 7.4 Dependency Extraction

Dependencies come from two sources (deduplicated):

**1. Explicit `depends_on` list in frontmatter:**
```yaml
depends_on:
  - module.models
  - module.state_manager
```

**2. Wikilinks in note body:**
```markdown
This module depends on [[module/models|module.models]] for data structures
and [[module/state_manager]] for persistence.
```

The wikilink `[[module/models]]` is converted to address `module.models` via path-to-address conversion (`/` -> `.`). Only wikilinks that match known resource addresses are included.

### 7.5 Spec Note Example

```markdown
---
terra4mice_spec: true
type: module
name: state_manager
attributes:
  description: "JSON state persistence with CRUD operations"
  class: StateManager
  functions:
    - load
    - save
    - list
    - show
    - mark_created
    - mark_partial
    - mark_broken
    - remove
files:
  - src/terra4mice/state_manager.py
depends_on:
  - module.models
tags:
  - terra4mice/spec
  - core-module
---

# State Manager

The state manager handles persistence of terra4mice state. It supports
both local JSON files and pluggable backends (S3, Obsidian, etc).

## Design Decisions

- JSON format for state (machine-managed, not human-edited)
- Serial counter increments on every mutation for conflict detection
- Context manager pattern for auto-lock/unlock with remote backends

## Dependencies

- [[module/models|module.models]] - Core data models
- [[module/backends|module.backends]] - Storage backends

## Notes

Custom notes, diagrams, research links, etc. go here.
This content is preserved across terra4mice writes.
```

### 7.6 CLI Integration

```bash
# Use vault as spec source
terra4mice plan --spec-source obsidian --vault ~/Documents/MyVault

# With custom subfolder
terra4mice plan --spec-source obsidian --vault ~/Documents/MyVault --vault-subfolder specs

# Import vault spec to YAML
terra4mice obsidian import --vault ~/Documents/MyVault --output terra4mice.spec.yaml
```

New argparse flags on `plan`, `refresh`, `ci` commands:
- `--spec-source {yaml,obsidian}` (default: `yaml`)
- `--vault PATH` (required when `--spec-source=obsidian`)
- `--vault-subfolder NAME` (default: `terra4mice`)

### 7.7 Key Design Decision: Spec Resources Always Start as MISSING

When loading a spec from vault, all resources get `status=ResourceStatus.MISSING`. The spec represents **desired state**, not current state. The planner then diffs spec (desired) against state (current) to produce the plan. This is consistent with YAML spec loading.

---

## 8. C. Plan/State Export to Obsidian

### 8.1 Overview

New function in `ci.py` that exports the current plan and state to an Obsidian vault as markdown notes. Generates per-resource notes, a dashboard, and optionally a canvas.

### 8.2 Function Signature

```python
def export_to_obsidian(
    plan: Plan,
    spec: Spec,
    state: State,
    vault_path: str,
    subfolder: str = "terra4mice",
) -> dict:
    """Returns {"created": int, "updated": int, "unchanged": int}"""
```

### 8.3 Per-Resource Note Format

Each resource note includes:

**Frontmatter**:
- `terra4mice: true` (managed flag)
- `type`, `name`, `address`
- `status` (from state, or "missing" if not in state)
- `action` (from plan: create, update, delete, no-op)
- `files`, `tests`, `depends_on`
- `attributes` (from spec)
- `state_attributes` (from state/inference, kept separate)

**Default body** (only used for new notes):
- `# type.name` heading
- Description from attributes
- Action notice (blockquote if action needed)
- Dependencies section with wikilinks
- Files section with code paths
- Notes section with HTML comment placeholder

### 8.4 Dashboard Note (`_dashboard.md`)

Auto-generated convergence overview:

```markdown
---
terra4mice: true
type: dashboard
convergence: 73
total_resources: 15
implemented: 10
partial: 2
missing: 3
---

# terra4mice Dashboard

**Convergence**: 73% (10/15 implemented, 2 partial, 3 missing)

`[##############------]` 73%

## Resources

| Resource | Status | Action |
|----------|--------|--------|
| [[module/models|module.models]] | implemented | no-op |
| [[module/spec_parser|module.spec_parser]] | implemented | no-op |
| [[feature/auth_login|feature.auth_login]] | partial | update |
| [[feature/payments|feature.payments]] | missing | create |

## Pending Actions

- **Create**: 3 resources
- **Update**: 2 resources
```

The dashboard body is **regenerated on each export** (not preserved), since it is entirely derived from state data.

### 8.5 CLI Integration

```bash
# Export plan to Obsidian vault
terra4mice plan --format obsidian --vault ~/Documents/MyVault

# Export current state (without plan)
terra4mice obsidian export --vault ~/Documents/MyVault
```

---

## 9. D. Obsidian as Inference Source (5th Strategy)

### 9.1 Overview

The `InferenceEngine` gains a 5th strategy: check Obsidian vault notes for evidence of resource implementation. This is complementary to the existing 4 strategies (explicit files, pattern matching, test detection, AST analysis).

### 9.2 Confidence Scoring

```
Note exists:                    +0.1 base
Frontmatter status: implemented +0.5
Frontmatter status: partial     +0.3
Frontmatter status: broken      +0.1
Body richness (word count):     +0.0 to +0.4 (scaled: words/500, capped at 0.4)

Total contribution weight:      * 0.2 (max 0.2 added to final confidence)
```

### 9.3 How It Integrates

```python
# In infer_resource(), after existing strategies 1-4:
if self.config.obsidian_vault:
    obs_confidence = self._infer_from_obsidian(resource, result)
    if obs_confidence > 0:
        result.evidence.append(f"Obsidian note: {obs_confidence:.0%} richness")
        result.confidence += obs_confidence * 0.2
```

### 9.4 Configuration

New fields on `InferenceConfig`:
- `obsidian_vault: Optional[str]` (path to vault root, `None` = disabled)
- `obsidian_subfolder: str` (default: `"terra4mice"`)

CLI flags on `refresh` and `ci`:
- `--obsidian-vault PATH`
- `--obsidian-subfolder NAME`

### 9.5 Evidence Produced

The inference result includes evidence strings like:
- `"Obsidian note: 85% richness"`
- `"Obsidian frontmatter status: implemented"`
- Files listed in note frontmatter are added to `result.files_found`

---

## 10. E. CLI Commands

### 10.1 Command Group: `terra4mice obsidian`

```
terra4mice obsidian <command>

Commands:
  init      Scaffold vault structure from existing spec and state
  export    One-way export: state/plan -> vault notes
  import    One-way import: vault notes -> spec YAML
  sync      Bidirectional sync between state and vault
```

### 10.2 `terra4mice obsidian init`

**Purpose**: Create initial vault structure from existing `terra4mice.spec.yaml` and `terra4mice.state.json`.

```bash
terra4mice obsidian init --vault ~/Documents/MyVault
terra4mice obsidian init --vault ~/Documents/MyVault --subfolder my-project
terra4mice obsidian init --vault ~/Documents/MyVault --spec custom.spec.yaml
```

**Behavior**:
1. Load spec and state.
2. Generate plan.
3. Create vault subfolder.
4. Write per-resource notes with frontmatter (status from state) and default body.
5. Write `_index.md`, `_dashboard.md`.
6. Generate `_convergence.base` (Bases view definition).
7. Print summary.

**Output**:
```
Obsidian vault initialized at: ~/Documents/MyVault/terra4mice/
  Created: 15 notes
  Updated: 0 notes

Open the vault in Obsidian to see the Graph View of your project.
Each resource is a note with wikilinks to its dependencies.
```

### 10.3 `terra4mice obsidian export`

**Purpose**: One-way push from terra4mice state to vault. Overwrites frontmatter, preserves bodies.

```bash
terra4mice obsidian export --vault ~/Documents/MyVault
```

### 10.4 `terra4mice obsidian import`

**Purpose**: One-way pull from vault to spec YAML. Reads notes with `terra4mice_spec: true`, assembles spec, writes YAML.

```bash
terra4mice obsidian import --vault ~/Documents/MyVault
terra4mice obsidian import --vault ~/Documents/MyVault --output my-project.spec.yaml
```

**Output**:
```
Imported spec from vault: ~/Documents/MyVault/terra4mice/
  Resources: 15
  Written to: terra4mice.spec.yaml
```

### 10.5 `terra4mice obsidian sync`

**Purpose**: Bidirectional synchronization with conflict resolution.

```bash
# State wins on conflict (default)
terra4mice obsidian sync --vault ~/Documents/MyVault --prefer state

# Vault wins on conflict (manual edits take precedence)
terra4mice obsidian sync --vault ~/Documents/MyVault --prefer vault

# Dry run (show what would change)
terra4mice obsidian sync --vault ~/Documents/MyVault --dry-run
```

**Sync logic**:
1. Read state from terra4mice.
2. Read vault notes.
3. For resources in both: apply `--prefer` policy on conflict.
4. Resources only in state: create vault notes.
5. Resources only in vault (with spec tag): import to state.
6. Human body content is **always** preserved.

**Dry run output**:
```
  [vault+] feature.payments           # Will create vault note
  [vault~] feature.auth: vault partial -> implemented  # Will update vault
  [state~] module.new_mod: state missing -> partial    # Will update state
  [state+] feature.dashboard          # Will import to state

Would create 1 vault notes
Would update 1 vault notes
Would import 2 to state
```

---

## 11. F. Canvas Generation (Dependency Graph)

### 11.1 Overview

Auto-generate a `.canvas` file that visualizes the dependency graph using Obsidian Canvas (JSON Canvas spec v1.0).

### 11.2 Color Mapping

| Status | Color Preset | Hex | Visual |
|--------|-------------|-----|--------|
| implemented | `"4"` (green) | `#28a745` | Green node |
| partial | `"3"` (yellow) | `#ffc107` | Yellow node |
| missing | `"1"` (red) | `#dc3545` | Red node |
| broken | `"2"` (orange) | `#fd7e14` | Orange node |
| deprecated | `"5"` (cyan) | `#17a2b8` | Cyan node |

### 11.3 Layout Algorithm

Simple grid layout by type, with dependency edges:

```
Row 0: [module.models] [module.spec_parser] [module.state_manager] ...
Row 1: [feature.context_registry] [feature.context_cli] ...
Row 2: [example.demo_project] [example.ultratrack_demo] ...
```

Each row corresponds to a resource type. Nodes are spaced 250px horizontally, 200px vertically. Edges connect `depends_on` references with arrows.

### 11.4 Generated File

`terra4mice/_plan.canvas`:
```json
{
  "nodes": [
    {
      "id": "module.models",
      "type": "file",
      "file": "terra4mice/module/models.md",
      "x": 0, "y": 0,
      "width": 200, "height": 80,
      "color": "4"
    },
    {
      "id": "module.spec_parser",
      "type": "file",
      "file": "terra4mice/module/spec_parser.md",
      "x": 250, "y": 0,
      "width": 200, "height": 80,
      "color": "4"
    }
  ],
  "edges": [
    {
      "id": "e-module.spec_parser-module.models",
      "fromNode": "module.spec_parser",
      "toNode": "module.models",
      "fromSide": "left",
      "toSide": "right",
      "toEnd": "arrow",
      "label": "depends_on"
    }
  ]
}
```

---

## 12. G. Bases / Dataview Integration

### 12.1 Auto-Generated Bases File

`terra4mice/_convergence.base`:
```yaml
filters:
  - and:
    - 'file.inFolder("terra4mice")'
    - 'terra4mice == true'
    - 'type != "index"'
    - 'type != "dashboard"'
formulas:
  progress: 'if(status == "implemented", 100, if(status == "partial", confidence * 100, 0))'
  gap: '100 - formula.progress'
properties:
  address:
    width: 180
  status:
    width: 120
  confidence:
    width: 100
  type:
    width: 100
  formula.progress:
    width: 100
views:
  - type: table
    name: "All Resources"
    order:
      - status: asc
      - confidence: desc
  - type: table
    name: "Missing & Partial"
    filters:
      - 'status != "implemented"'
    order:
      - status: asc
  - type: cards
    name: "Resource Cards"
    filters:
      - 'status != "implemented"'
```

### 12.2 Dataview Query Examples for Users

Users can create their own Dataview queries in any vault note:

**Convergence overview**:
```dataview
TABLE status, confidence, type
FROM "terra4mice"
WHERE terra4mice = true AND type != "index" AND type != "dashboard"
SORT status ASC, confidence ASC
```

**Missing resources only**:
```dataview
LIST
FROM "terra4mice"
WHERE status = "missing"
```

**Progress by type**:
```dataview
TABLE length(rows) AS "Count",
  length(filter(rows, (r) => r.status = "implemented")) AS "Done",
  length(filter(rows, (r) => r.status = "partial")) AS "Partial",
  length(filter(rows, (r) => r.status = "missing")) AS "Missing"
FROM "terra4mice"
WHERE terra4mice = true AND type != "index" AND type != "dashboard"
GROUP BY type
```

**Dependency tree for a resource**:
```dataview
LIST depends_on
FROM "terra4mice"
WHERE contains(depends_on, "module.models")
```

**DataviewJS convergence percentage**:
```dataviewjs
const pages = dv.pages('"terra4mice"')
  .where(p => p.terra4mice && p.type !== "index" && p.type !== "dashboard");
const total = pages.length;
const impl = pages.where(p => p.status === "implemented").length;
const partial = pages.where(p => p.status === "partial").length;
const score = Math.round(((impl + partial * 0.5) / total) * 100);
dv.paragraph(`**Convergence: ${score}%** (${impl}/${total} implemented, ${partial} partial)`);
```

---

## 13. Data Format Specification

### 13.1 Resource Note Frontmatter Schema

```yaml
---
# Required fields (managed by terra4mice)
terra4mice: true                    # boolean, marks note as managed
type: module                        # string, resource type
name: state_manager                 # string, resource name
address: module.state_manager       # string, type.name (derived, for convenience)
status: implemented                 # enum: missing | partial | implemented | broken | deprecated

# Optional fields (managed by terra4mice)
locked: false                       # boolean, prevents inference overwrite
source: auto                        # enum: auto | manual | obsidian
confidence: 0.95                    # float 0.0-1.0, inference confidence
action: no-op                       # enum: create | update | delete | no-op (from last plan)
files:                              # list of strings, implementation files
  - src/terra4mice/state_manager.py
tests: []                           # list of strings, test files
depends_on:                         # list of strings, type.name addresses
  - module.models
created_at: "2026-01-15T10:00:00"   # ISO 8601 timestamp
updated_at: "2026-03-01T15:30:00"   # ISO 8601 timestamp

# Spec attributes (from spec definition)
attributes:
  description: "JSON state persistence with CRUD operations"
  class: StateManager
  functions: [load, save, list]

# Inference data (from last refresh, separate from spec attributes)
state_attributes: {}

# Symbol tracking
symbols:
  StateManager:
    status: found
    type: class
  load:
    status: found
    type: function

# Spec-only flag (for spec loader)
terra4mice_spec: true               # boolean, marks note as spec resource (optional)

# Standard Obsidian fields
tags:                               # Obsidian tags, also used for filtering
  - terra4mice/resource
  - terra4mice/module
aliases: []                         # Alternative names for wikilink resolution
---
```

### 13.2 Index Note Frontmatter Schema

```yaml
---
terra4mice: true
type: index
version: "1"
serial: 142
last_updated: "2026-03-01T15:30:00"
total_resources: 15
---
```

### 13.3 Dashboard Note Frontmatter Schema

```yaml
---
terra4mice: true
type: dashboard
convergence: 73
total_resources: 15
implemented: 10
partial: 2
missing: 3
generated: "2026-03-01T15:30:00"
---
```

---

## 14. Dependencies and Installation

### 14.1 No New Required Dependencies

The entire Obsidian integration uses only:
- `pathlib` (stdlib)
- `re` (stdlib)
- `json` (stdlib)
- `yaml` (pyyaml >= 6.0, already a dependency)

### 14.2 Optional Dependencies

```toml
# pyproject.toml addition
[project.optional-dependencies]
obsidian = [
    "python-frontmatter>=1.0.0",    # Ergonomic frontmatter read/write
]
all = [
    "terra4mice[dev,ast,remote,obsidian]",
]
```

`python-frontmatter` is optional but recommended for users who want to write scripts that interact with the vault independently of terra4mice.

### 14.3 Installation

```bash
# Basic (works without python-frontmatter)
pip install -e "."

# With Obsidian extras
pip install -e ".[obsidian]"

# Everything
pip install -e ".[all]"
```

### 14.4 Obsidian-Side Requirements

For the integration to work, users need:
- An Obsidian vault (any version)
- No plugins required for basic functionality
- **Recommended plugins** for enhanced experience:
  - Dataview (query frontmatter)
  - Bases (database views, core plugin in v1.9+)
  - Obsidian Git (version control)
  - Templater (resource note templates)
  - Graph Link Types (typed dependency edges)

---

## 15. Files to Modify

### 15.1 Changes Summary

| File | Change Type | Description | ~LOC |
|------|------------|-------------|------|
| `src/terra4mice/backends.py` | Add class | `ObsidianBackend` extending `StateBackend` | ~200 |
| `src/terra4mice/backends.py` | Modify function | `create_backend()` factory: add `"obsidian"` type | ~10 |
| `src/terra4mice/spec_parser.py` | Add functions | `load_spec_from_obsidian()`, `_parse_obsidian_frontmatter()`, `_read_obsidian_body()`, `_extract_wikilink_dependencies()` | ~120 |
| `src/terra4mice/inference.py` | Modify dataclass | `InferenceConfig`: add `obsidian_vault`, `obsidian_subfolder` | ~5 |
| `src/terra4mice/inference.py` | Add method | `_infer_from_obsidian()` on `InferenceEngine` | ~50 |
| `src/terra4mice/inference.py` | Modify method | `infer_resource()`: wire 5th strategy | ~5 |
| `src/terra4mice/ci.py` | Add functions | `export_to_obsidian()`, `_write_dashboard()`, `_generate_canvas()`, `_generate_base_file()` | ~180 |
| `src/terra4mice/cli.py` | Add subcommands | `obsidian` group: `init`, `export`, `import`, `sync` | ~250 |
| `src/terra4mice/cli.py` | Modify parsers | Add `--spec-source`, `--vault`, `--vault-subfolder`, `--obsidian-vault` flags | ~30 |
| `src/terra4mice/cli.py` | Add helpers | `_load_spec_for_command()`, `_spec_to_yaml()` | ~40 |
| `tests/test_obsidian.py` | New file | Complete test suite for Obsidian integration | ~300 |
| `pyproject.toml` | Modify | Add `obsidian` optional dependency group | ~5 |
| `terra4mice.spec.yaml` | Modify | Add Obsidian integration resources | ~30 |

**Total**: ~1,225 lines of new/modified code.

### 15.2 Modules NOT Modified

These existing modules require **zero changes**:
- `models.py` - Data models are format-agnostic
- `planner.py` - Works on Spec + State objects regardless of source
- `state_manager.py` - Serialization layer unchanged (JSON bytes in/out)

This is the key architectural win: the existing abstractions (`StateBackend`, `parse_spec(dict)`, `StateManager`) are generic enough that Obsidian support plugs in without touching core logic.

---

## 16. Test Plan

### 16.1 Test File: `tests/test_obsidian.py`

```python
# Test structure outline

class TestObsidianBackendReadWrite:
    """Backend ABC compliance: read/write round-trip."""
    def test_write_creates_directory_structure(self, tmp_path): ...
    def test_write_creates_index_note(self, tmp_path): ...
    def test_write_creates_per_resource_notes(self, tmp_path): ...
    def test_read_reassembles_json_blob(self, tmp_path): ...
    def test_round_trip_preserves_all_fields(self, tmp_path): ...
    def test_exists_false_when_no_index(self, tmp_path): ...
    def test_exists_true_when_index_present(self, tmp_path): ...
    def test_backend_type_is_obsidian(self): ...
    def test_supports_locking_is_false(self): ...

class TestObsidianBackendHumanContent:
    """Human-written body content must survive writes."""
    def test_body_preserved_on_frontmatter_update(self, tmp_path): ...
    def test_default_body_used_for_new_notes(self, tmp_path): ...
    def test_empty_body_handled(self, tmp_path): ...
    def test_body_with_wikilinks_preserved(self, tmp_path): ...
    def test_body_with_images_preserved(self, tmp_path): ...

class TestObsidianBackendDeletion:
    """Resource removal from state should delete managed notes."""
    def test_removed_resource_deletes_managed_note(self, tmp_path): ...
    def test_unmanaged_notes_not_deleted(self, tmp_path): ...
    def test_empty_type_dirs_cleaned_up(self, tmp_path): ...
    def test_system_files_not_deleted(self, tmp_path): ...

class TestFrontmatterParsing:
    """Edge cases in YAML frontmatter parsing."""
    def test_valid_frontmatter(self, tmp_path): ...
    def test_no_frontmatter(self, tmp_path): ...
    def test_broken_yaml(self, tmp_path): ...
    def test_empty_frontmatter(self, tmp_path): ...
    def test_unicode_content(self, tmp_path): ...
    def test_multiline_strings(self, tmp_path): ...
    def test_nested_attributes(self, tmp_path): ...

class TestObsidianSpecLoader:
    """Load spec from vault notes."""
    def test_load_from_folder_structure(self, tmp_path): ...
    def test_type_from_explicit_field(self, tmp_path): ...
    def test_type_from_parent_folder(self, tmp_path): ...
    def test_type_defaults_to_feature(self, tmp_path): ...
    def test_depends_on_from_frontmatter(self, tmp_path): ...
    def test_depends_on_from_wikilinks(self, tmp_path): ...
    def test_depends_on_deduplicated(self, tmp_path): ...
    def test_non_spec_notes_ignored(self, tmp_path): ...
    def test_system_files_ignored(self, tmp_path): ...
    def test_empty_vault(self, tmp_path): ...
    def test_nonexistent_vault(self, tmp_path): ...

class TestWikilinkExtraction:
    """Wikilink to dependency address conversion."""
    def test_simple_wikilink(self): ...
    def test_wikilink_with_display_text(self): ...
    def test_only_known_addresses_included(self): ...
    def test_duplicate_links_deduplicated(self): ...
    def test_no_wikilinks(self): ...
    def test_non_terra4mice_wikilinks_ignored(self): ...

class TestExportToObsidian:
    """Plan/state export to vault."""
    def test_creates_per_resource_notes(self, tmp_path): ...
    def test_creates_dashboard(self, tmp_path): ...
    def test_dashboard_has_convergence_stats(self, tmp_path): ...
    def test_preserves_existing_body(self, tmp_path): ...
    def test_wikilinks_in_dependencies(self, tmp_path): ...
    def test_return_counts(self, tmp_path): ...

class TestObsidianInference:
    """5th inference strategy: vault notes."""
    def test_no_note_returns_zero(self, tmp_path): ...
    def test_note_exists_base_confidence(self, tmp_path): ...
    def test_implemented_status_adds_confidence(self, tmp_path): ...
    def test_partial_status_adds_confidence(self, tmp_path): ...
    def test_body_richness_scoring(self, tmp_path): ...
    def test_files_extracted_from_frontmatter(self, tmp_path): ...
    def test_disabled_when_no_vault_configured(self): ...

class TestCreateBackendFactory:
    """Factory function recognizes obsidian type."""
    def test_create_obsidian_backend(self, tmp_path): ...
    def test_missing_vault_path_raises(self): ...
    def test_custom_subfolder(self, tmp_path): ...

class TestObsidianCLI:
    """CLI subcommand integration."""
    def test_obsidian_init(self, tmp_path): ...
    def test_obsidian_export(self, tmp_path): ...
    def test_obsidian_import(self, tmp_path): ...
    def test_obsidian_sync_prefer_state(self, tmp_path): ...
    def test_obsidian_sync_prefer_vault(self, tmp_path): ...
    def test_obsidian_sync_dry_run(self, tmp_path): ...
    def test_plan_with_obsidian_spec_source(self, tmp_path): ...
```

### 16.2 Test Execution

```bash
# Run Obsidian tests only
pytest tests/test_obsidian.py -p no:pytest_ethereum -v

# Run all tests (including existing)
pytest tests/ -p no:pytest_ethereum

# With coverage
pytest tests/ -p no:pytest_ethereum --cov=terra4mice --cov-report=term-missing
```

All tests use `tmp_path` fixtures (temp directories as mock vaults). No actual Obsidian installation needed. No network calls.

---

## 17. Implementation Phases

### 17.1 Phase A: Core Backend (Priority 1)

**Goal**: `ObsidianBackend` that passes all `StateBackend` contract tests.

1. Implement `ObsidianBackend` class in `backends.py`
2. Implement `_read_frontmatter()`, `_read_body()`, `_write_note()` helpers
3. Register in `create_backend()` factory
4. Write `TestObsidianBackendReadWrite` and `TestObsidianBackendHumanContent`
5. Write `TestObsidianBackendDeletion` and `TestFrontmatterParsing`
6. Dogfood: configure terra4mice's own spec to use Obsidian backend

**Estimated scope**: ~200 LOC production, ~200 LOC tests.

### 17.2 Phase B: Spec Loader (Priority 1)

**Goal**: Load specs from Obsidian vault notes.

1. Implement `load_spec_from_obsidian()` in `spec_parser.py`
2. Implement `_extract_wikilink_dependencies()` helper
3. Add `--spec-source` and `--vault` flags to CLI
4. Write `TestObsidianSpecLoader` and `TestWikilinkExtraction`
5. Dogfood: create vault version of `terra4mice.spec.yaml`

**Estimated scope**: ~120 LOC production, ~100 LOC tests.

### 17.3 Phase C: Export + CLI (Priority 2)

**Goal**: CLI commands for vault interaction.

1. Implement `export_to_obsidian()` in `ci.py`
2. Implement `_write_dashboard()` helper
3. Implement CLI subcommands: `obsidian init`, `obsidian export`, `obsidian import`
4. Write `TestExportToObsidian` and `TestObsidianCLI`

**Estimated scope**: ~300 LOC production, ~150 LOC tests.

### 17.4 Phase D: Inference + Sync (Priority 3)

**Goal**: Bidirectional sync and vault-based inference.

1. Implement `_infer_from_obsidian()` in `inference.py`
2. Wire as 5th strategy in `infer_resource()`
3. Implement `cmd_obsidian_sync()` with conflict resolution
4. Write `TestObsidianInference` and sync tests

**Estimated scope**: ~200 LOC production, ~100 LOC tests.

### 17.5 Phase E: Canvas + Bases (Priority 4)

**Goal**: Auto-generated visualizations.

1. Implement `_generate_canvas()` for `.canvas` files
2. Implement `_generate_base_file()` for `.base` files
3. Include in `obsidian init` output

**Estimated scope**: ~150 LOC production, ~50 LOC tests.

---

## 18. Risks and Mitigations

### 18.1 Risk: YAML Frontmatter Corruption

**Scenario**: Malformed YAML in a note breaks parsing.
**Mitigation**: `_read_frontmatter()` catches `yaml.YAMLError` and returns `None`. The resource is skipped with a warning. Other resources continue to work.

### 18.2 Risk: Concurrent Writes (Obsidian + terra4mice)

**Scenario**: User edits a note in Obsidian while terra4mice writes to it.
**Mitigation**: terra4mice only modifies frontmatter, never the body. Obsidian users typically edit the body. Conflict window is narrow. For safety, `obsidian sync --dry-run` shows changes before applying.

### 18.3 Risk: Large Vaults (Performance)

**Scenario**: Vault with thousands of notes slows down `read()`.
**Mitigation**: terra4mice notes are in a subfolder. `read()` only walks that subfolder, not the entire vault. For a project with 100 resources, this means reading 100 small files -- negligible.

### 18.4 Risk: Encoding Issues on Windows

**Scenario**: cp1252 vs UTF-8 issues (known Windows problem).
**Mitigation**: All read/write operations explicitly use `encoding="utf-8"`. Notes are always written as UTF-8.

### 18.5 Risk: Wikilink Resolution Across Renames

**Scenario**: User renames a note in Obsidian, breaking wikilinks.
**Mitigation**: Obsidian auto-updates wikilinks on rename (if configured). terra4mice uses `type/name` paths which are reconstructed from directory structure, not from wikilink resolution.

### 18.6 Risk: Obsidian Properties Type Conflicts

**Scenario**: User's vault has `status` defined as "date" type, conflicting with terra4mice's "text" usage.
**Mitigation**: Document the expected property types. Use `terra4mice_status` prefix if conflicts arise (configurable).

---

## 19. Ecosystem Comparison

### 19.1 Why Obsidian Over Alternatives

| Feature | Obsidian | Notion | Logseq |
|---------|----------|--------|--------|
| Local files | Plain `.md` on disk | Cloud-only, proprietary | Local `.md` (block-based) |
| Offline access | Full | Limited | Full |
| Git integration | Native plugin (9.9k stars) | None | Via plugin |
| Python libraries | obsidiantools, python-frontmatter, py-obsidianmd | notion-sdk-py | logseq-python-library |
| Programmatic access | Direct file read/write | REST API (cloud) | REST API, file access |
| Query engine | Dataview (10M+ downloads), Bases (core) | Native databases | Datascript (Datalog) |
| Graph visualization | Built-in Graph View | None | Built-in |
| Plugin ecosystem | 2,736+ community plugins | Limited | Growing |
| Vendor lock-in | None (standard Markdown) | High | Low |
| Team collaboration | Via Git or Sync | Native | Via Git |
| Self-hosting | N/A (local-first) | No | Yes |

### 19.2 Decision

Obsidian wins for terra4mice because:
1. **Plain files** = zero runtime dependency, just read/write `.md`
2. **python-frontmatter** = 1 optional dependency for full metadata access
3. **obsidiantools** = NetworkX dependency graph for free
4. **Git-native** = CI/CD integration is trivial
5. **No Obsidian required** = files work with any markdown editor

---

## 20. Python Libraries Reference

### 20.1 python-frontmatter

```bash
pip install python-frontmatter
```

```python
import frontmatter

# Read
post = frontmatter.load("note.md")
print(post["status"])        # "partial"
print(post.content)          # body below frontmatter
print(post.metadata)         # full frontmatter dict

# Write
post["status"] = "implemented"
post["confidence"] = 0.95
frontmatter.dump(post, "note.md")

# Parse from string
post = frontmatter.loads("---\nstatus: done\n---\nBody")
metadata, content = frontmatter.parse(text)
```

### 20.2 obsidiantools

```bash
pip install obsidiantools
```

```python
import obsidiantools.api as otools

vault = otools.Vault("/path/to/vault").connect().gather()

# Vault-wide data
vault.graph                        # NetworkX DiGraph
vault.front_matter_index           # All frontmatter
vault.tags_index                   # All tags
vault.backlinks_index              # All backlinks
vault.nonexistent_notes            # Broken links
vault.isolated_notes               # Orphan notes

# Per-note data
vault.get_front_matter("note")     # dict
vault.get_tags("note")             # list
vault.get_backlinks("note")        # list
vault.get_source_text("note")      # raw markdown

# Pandas DataFrames
vault.get_note_metadata()          # All note stats
```

### 20.3 py-obsidianmd

```bash
pip install py-obsidianmd
```

```python
from pathlib import Path
from pyomd import Notes
from pyomd.metadata import MetadataType

notes = Notes(Path("/path/to/folder"))
notes.metadata.add(k="status", l="partial", meta_type=MetadataType.FRONTMATTER)
notes.filter(has_meta=[("type", "feature", MetadataType.FRONTMATTER)])
notes.write()
```

---

## 21. Future Possibilities

### 21.1 MCP Server for terra4mice

Expose terra4mice as an MCP tool, allowing AI agents inside Obsidian to run `plan`, `refresh`, `mark` operations. The Obsidian MCP ecosystem is growing -- terra4mice could be a provider.

### 21.2 Obsidian Plugin (TypeScript)

A native plugin that:
- Shows convergence in the status bar
- Adds a "terra4mice" sidebar with plan output
- Provides a command palette action for `refresh`
- Auto-creates resource notes from spec
- Color-codes notes in file explorer by status

### 21.3 Obsidian Publish Dashboard

Publish the `_dashboard.md` note via Obsidian Publish or Digital Garden plugin. Creates a public convergence dashboard for open-source projects.

### 21.4 Real-Time File Watching

A daemon mode (`terra4mice watch --vault`) that watches vault files and auto-runs inference when notes change. Provides near-real-time convergence tracking.

### 21.5 Multi-Project Vaults

A single vault tracking multiple projects:
```
vault/
  project-a/
    terra4mice/
      _index.md
      module/...
  project-b/
    terra4mice/
      _index.md
      feature/...
```

### 21.6 Graph Analysis

Use `obsidiantools` to analyze the dependency graph:
- Find circular dependencies
- Identify critical path (longest dependency chain)
- Detect orphan resources
- Calculate modularity/cohesion metrics
- Generate architecture diagrams

---

## References

### Obsidian Documentation
- [How Obsidian stores data](https://help.obsidian.md/data-storage)
- [Configuration folder](https://help.obsidian.md/configuration-folder)
- [Properties](https://help.obsidian.md/properties)
- [Internal links](https://help.obsidian.md/links)
- [Tags](https://help.obsidian.md/tags)
- [Bases introduction](https://help.obsidian.md/bases)
- [Canvas](https://help.obsidian.md/plugins/canvas)
- [Obsidian URI](https://help.obsidian.md/Extending+Obsidian/Obsidian+URI)

### Plugin Repositories
- [Dataview](https://github.com/blacksmithgu/obsidian-dataview) - 10M+ downloads
- [Obsidian Git](https://github.com/Vinzent03/obsidian-git) - 9.9k stars
- [Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api)
- [Templater](https://github.com/SilentVoid13/Templater)
- [obsidian-mcp-server](https://github.com/cyanheads/obsidian-mcp-server)

### Python Libraries
- [python-frontmatter](https://github.com/eyeseast/python-frontmatter)
- [obsidiantools](https://github.com/mfarragher/obsidiantools) - v0.11.0
- [py-obsidianmd](https://github.com/selimrbd/py-obsidianmd)

### Specifications
- [JSON Canvas v1.0](https://jsoncanvas.org/)
- [YAML 1.2](https://yaml.org/spec/1.2.2/)

### Related Projects
- [Claude Vault](https://github.com/MarioPadilla/claude-vault) - Sync Claude conversations to Obsidian
- [Obsidian Plugin Stats](https://www.obsidianstats.com/)
