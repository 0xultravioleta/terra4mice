# terra4mice - PLAN

> Roadmap de implementación incremental
> Filosofía: Dogfoodear en proyectos reales desde el día 1

## Arquitectura General

```
┌─────────────────────────────────────────────────────────────┐
│                      terra4mice                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────┐│
│  │   SPEC   │    │  STATE   │    │   PLAN   │    │ APPLY  ││
│  │  Parser  │───►│ Inferrer │───►│Generator │───►│ Runner ││
│  └──────────┘    └──────────┘    └──────────┘    └────────┘│
│       │               │               │               │     │
│       ▼               ▼               ▼               ▼     │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                    CLI Interface                      │  │
│  │  terra4mice init | plan | apply | status | diff      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Componente | Tecnología | Razón |
|------------|------------|-------|
| CLI | Python (Click/Typer) | Rápido de prototipar, buenas libs |
| Spec Format | YAML | Familiar, versionable, editable |
| State Storage | JSON/YAML | Simple, portable |
| AST Analysis | tree-sitter | Multi-language |
| Test Detection | pytest/jest plugins | Ecosistema existente |

**Alternativa Rust**: Si performance es crítico después del MVP, reescribir core en Rust.

---

## Phase 1: MVP CLI (Semanas 1-4)

### Objetivo
CLI funcional que pueda hacer `plan` en un proyecto real.

### Entregables

#### 1.1 Spec Parser (Semana 1)
```python
# terra4mice/spec.py
def load_spec(path: str) -> Spec:
    """Load and validate spec.yaml"""

def validate_spec(spec: Spec) -> List[ValidationError]:
    """Ensure spec is well-formed"""
```

**Schema inicial**:
```yaml
# spec.yaml v0.1
version: "0.1"
app:
  <feature>:
    status: required | optional | deprecated
    tests: [unit, integration, e2e]  # optional
    endpoints: [...]  # optional
    files: [...]  # optional, explicit file mapping
```

#### 1.2 State Inferrer (Semanas 2-3)
```python
# terra4mice/state.py
def infer_state(repo_path: str, spec: Spec) -> State:
    """Infer current state from codebase"""
```

**Inferencia inicial (Python)**:
- Buscar archivos que matcheen `files` del spec
- Buscar tests en `tests/` o `*_test.py`
- Analizar coverage si existe `.coverage`
- Detectar imports y dependencias

**Estados inferidos**:
```python
StateStatus = Literal["missing", "partial", "implemented", "broken"]
```

#### 1.3 Plan Generator (Semana 3)
```python
# terra4mice/plan.py
def generate_plan(spec: Spec, state: State) -> Plan:
    """Generate plan: spec - state = actions"""

def format_plan(plan: Plan) -> str:
    """Human-readable plan output"""
```

**Output format**:
```
$ terra4mice plan

Scanning spec.yaml...
Inferring state...

Plan:
  + implement auth.revoke
  ~ complete auth.refresh (partial → implemented)
  + implement payments.split

3 actions required
0 destructive changes

Run 'terra4mice apply' to begin implementation cycle.
```

#### 1.4 CLI (Semana 4)
```python
# terra4mice/cli.py
@app.command()
def init():
    """Initialize terra4mice in current directory"""

@app.command()
def plan():
    """Show what needs to be done"""

@app.command()
def status():
    """Show current convergence %"""
```

### Dogfood Target: Ultratrack

Por qué Ultratrack:
- Tiene spec de 200+ líneas
- CERO líneas de código
- Perfecto para validar que `plan` muestra todo como `missing`

**Resultado esperado**:
```
$ terra4mice plan

Plan:
  + implement twitch_integration
  + implement discord_integration
  + implement telegram_integration
  + implement snapshot_integration
  + implement blockchain_integration
  + implement events_integration
  + implement twitter_integration
  + implement unified_api
  + implement mega_brain_queries

9 actions required
Convergence: 0%
```

---

## Phase 2: State Inference Avanzada -- COMPLETADA

### Objetivo
Inferencia de estado mas inteligente usando AST y tests.

### Implementado (2026-02-06)

#### 2.1 tree-sitter AST Analysis
- `src/terra4mice/analyzers.py` - Modulo con analisis profundo via tree-sitter
- Soporte multi-lenguaje: Python, TypeScript/TSX, JavaScript, Solidity
- `AnalysisResult` dataclass: functions, classes, exports, imports, entities, decorators
- `score_against_spec()`: verifica atributos del spec contra codigo real
- `analyze_file()`: dispatch por extension con cache de parsers
- Dependencia opcional: `pip install terra4mice[ast]`

#### 2.2 Inference Engine Upgrade
- `inference.py` ahora usa tree-sitter como primera opcion con 5 niveles de fallback:
  1. tree-sitter (Python, TS, JS, Solidity) -> `score_against_spec()`
  2. stdlib `ast` (Python)
  3. regex (Solidity)
  4. regex (TypeScript/JavaScript) - nuevo `_score_typescript_fallback()`
  5. size heuristic (config/docs)
- Patterns extendidos para .tsx y .jsx

#### 2.3 Tests
- `tests/test_analyzers.py` - 48 tests
- Tests con `skipif` para funcionar con o sin tree-sitter instalado

### Pendiente
- pytest plugin para reportar a terra4mice
- Coverage mapping

---

## Phase 3: Multi-AI Context Tracking (Nuevo)

### Objetivo
Cuando multiples AIs (Claude Code, Codex, Kimi, etc.) trabajan en el mismo proyecto,
cada una lleva su propio contexto. terra4mice puede servir como **registry centralizado**
que sabe que AI tiene contexto sobre que parte del codebase.

### Problema
- Claude Code sabe que modifico `inference.py` pero Codex no lo sabe
- Kimi trabajo en el frontend pero Claude no tiene ese contexto
- Los archivos de contexto de cada AI son JSON estructurados pero aislados
- No hay forma facil de saber "que AI toco que" o "quien sabe de que"

### Solucion: Context Registry

```
terra4mice contexts list
# Output:
# AGENT          RESOURCE              LAST SEEN    STATUS
# claude-code    module.inference      2min ago     active
# claude-code    module.analyzers      2min ago     active
# codex          feature.auth_login    1hr ago      stale
# kimi-2.5       feature.frontend      30min ago    active
```

### Entregables

#### 3.1 Context Registry (`src/terra4mice/contexts.py`)
```python
@dataclass
class ContextEntry:
    agent: str           # "claude-code", "codex", "kimi-2.5"
    resource: str        # "module.inference"
    timestamp: datetime
    files_touched: List[str]
    confidence: float    # How much context the agent has

@dataclass
class AgentContext:
    agent: str
    entries: Dict[str, ContextEntry]  # resource -> entry

def register_agent(agent: str, resource: str, files: List[str])
def get_agent_context(agent: str) -> AgentContext
def list_contexts() -> List[AgentContext]
def merge_contexts(*contexts: AgentContext) -> AgentContext
```

#### 3.2 CLI Extensions
```bash
# Registrar contexto al hacer mark
terra4mice mark module.auth implemented --agent=codex

# Ver que AI tiene contexto de que
terra4mice contexts list
terra4mice contexts show claude-code

# Sincronizar contextos entre AIs
terra4mice contexts sync --from=claude-code --to=codex
```

#### 3.3 Context Export/Import (`src/terra4mice/context_io.py`)
```python
# Exportar snapshot para compartir
def export_context(agent: str, output: Path)
def import_context(input: Path, agent: str)
def diff_contexts(a: AgentContext, b: AgentContext) -> ContextDiff
```

**Formato de export**: JSON estructurado compatible con context files de cada AI
```json
{
  "agent": "claude-code",
  "project": "terra4mice",
  "timestamp": "2026-02-06T...",
  "resources": {
    "module.inference": {
      "status": "implemented",
      "files": ["src/terra4mice/inference.py"],
      "knowledge": ["tree-sitter integration", "5-level fallback"]
    }
  }
}
```

### Casos de uso
1. **Handoff**: Claude Code termina una sesion, exporta contexto, Codex lo importa y continua
2. **Conflicto detection**: "Codex modifico auth.py pero Claude no sabe" -> warning
3. **Onboarding**: Nueva AI entra al proyecto, importa contexto global y sabe el estado real
4. **Audit trail**: "Quien implemento que" como git blame pero para AIs

### Dogfood Target: terra4mice mismo
- Usar terra4mice para trackear contexto de Claude Code trabajando en terra4mice

---

## Phase 4: CI/CD Integration (previamente Phase 3) -- COMPLETADA

### Objetivo
terra4mice como gate en pipelines.

### Entregables

#### 4.1 GitHub Action
```yaml
# action.yml
name: 'terra4mice'
description: 'State-driven development checks'
inputs:
  fail-on-incomplete:
    description: 'Fail if plan is not empty'
    default: 'true'
runs:
  using: 'composite'
  steps:
    - run: pip install terra4mice
    - run: terra4mice plan --ci
```

#### 4.2 Pre-commit Hook
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/ultravioleta/terra4mice
    hooks:
      - id: terra4mice-check
        name: Check spec convergence
```

#### 4.3 PR Comments
```python
# terra4mice/integrations/github.py
def post_plan_comment(pr_number: int, plan: Plan):
    """Post plan as PR comment"""
```

**Ejemplo de comment**:
```markdown
## terra4mice Plan

| Feature | Status | Action |
|---------|--------|--------|
| auth.login | implemented | - |
| auth.refresh | partial | ~ complete |
| auth.revoke | missing | + implement |

**Convergence**: 67%
**Blocking merge**: Yes (plan not empty)
```

### Dogfood Target: x402-rs

Por qué x402-rs:
- CI ya existe con Terraform
- Alta criticidad (pagos)
- Spec drift es costoso

---

## Phase 5: Apply Runner (previamente Phase 4)

### Objetivo
Ciclo `apply` que guía implementación.

### Entregables

#### 5.1 Interactive Apply
```python
# terra4mice/apply.py
def apply_interactive(plan: Plan):
    """Guide developer through implementing each action"""
    for action in plan.actions:
        print(f"Next: {action}")
        print(f"Suggested files: {action.suggested_files}")
        input("Press Enter when done...")
        new_state = infer_state()
        if action.is_complete(new_state):
            print("✓ Action complete")
```

#### 5.2 Agent Integration (Council)
```python
# terra4mice/integrations/council.py
def provide_plan_to_agent(agent_id: str, plan: Plan):
    """Send plan to Claude Code agent via Council"""
```

**Integración con Council**:
- Council spawn → recibe plan de terra4mice
- Agente implementa → terra4mice re-infiere state
- Loop hasta convergencia

---

## Phase 6: Ecosystem Rollout (previamente Phase 5)

### Proyectos ordenados por prioridad

| Prioridad | Proyecto | Razón |
|-----------|----------|-------|
| P0 | Ultratrack | Spec-only, máximo impacto de visibility |
| P0 | x402-rs | Crítico, spec drift costoso |
| P1 | Council | Puede usar terra4mice como convergence gate |
| P1 | Karmacadabra | 48 agentes, unclear status → visibility |
| P1 | Colmena | Diseñado state-driven |
| P2 | Abracadabra | Bloqueado, pero visibility ayuda |
| P2 | 402milly | Scaling 1M→402M |
| P3 | SDKs | API contract management |
| P3 | Faro | Monitoring from specs |

---

## Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| State inference incorrecta | Alta | Alto | Validación manual inicial, feedback loop |
| Spec format too rigid | Media | Medio | Schema extensible, versioning |
| Overhead de mantener specs | Media | Alto | Auto-generation desde código existente |
| Adopción lenta | Media | Medio | Dogfooding primero, valor visible |

---

## Métricas de Éxito por Phase

| Phase | Métrica | Target | Estado |
|-------|---------|--------|--------|
| 1 | CLI funciona en Ultratrack | Plan muestra 9 missing | DONE |
| 2 | State inference accuracy | >80% en Python | DONE |
| 3 | Multi-AI context tracking | Contexto compartido entre 2+ AIs | PENDIENTE |
| 4 | CI integration functional | Blocks PR en x402-rs | DONE |
| 5 | Apply loop works | Developer completa feature guiado | PENDIENTE |
| 6 | Ecosystem adoption | 5+ proyectos usando | PENDIENTE |

---

## Dependencias Externas

| Dependencia | Versión | Uso |
|-------------|---------|-----|
| tree-sitter | ^0.20 | AST parsing |
| pyyaml | ^6.0 | YAML parsing |
| click/typer | latest | CLI framework |
| pytest | ^7.0 | Test integration |

---

## Comando Final de Graduation

```bash
# Cuando todo funcione:
terra4mice plan --all-ecosistem

# Output esperado:
Scanning 15 projects...

Ecosystem Convergence Report:
  ultratrack:     75% (+75 from baseline)
  x402-rs:        95% (+10 from baseline)
  council:        90% (+5 from baseline)
  karmacadabra:   100% (+15 from baseline)
  ...

Overall: 87% convergence
Recommendation: Ready for public release
```
