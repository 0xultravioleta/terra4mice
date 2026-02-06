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

## Phase 2: State Inference Avanzada (Semanas 5-8)

### Objetivo
Inferencia de estado más inteligente usando AST y tests.

### Entregables

#### 2.1 AST Analysis (Semanas 5-6)
```python
# terra4mice/analyzers/python.py
def analyze_python_file(path: str) -> FileAnalysis:
    """Extract functions, classes, imports from Python file"""

def match_spec_to_code(spec_feature: Feature, analysis: FileAnalysis) -> MatchResult:
    """Determine if code implements feature"""
```

**Usar tree-sitter** para:
- Detectar funciones/clases definidas
- Encontrar patrones (decorators, return types)
- Mapear endpoints a handlers

#### 2.2 Test Integration (Semanas 6-7)
```python
# terra4mice/analyzers/tests.py
def find_tests_for_feature(feature: str, test_dir: str) -> List[TestFile]:
    """Find tests that cover a feature"""

def run_tests_and_update_state(feature: str) -> TestResult:
    """Run tests and determine if feature passes"""
```

**Integración**:
- pytest plugin para reportar a terra4mice
- Coverage mapping: ¿qué líneas cubre cada test?

#### 2.3 Multi-Language (Semana 8)
```python
# terra4mice/analyzers/__init__.py
ANALYZERS = {
    "python": PythonAnalyzer,
    "typescript": TypeScriptAnalyzer,
    "rust": RustAnalyzer,
}

def get_analyzer(language: str) -> Analyzer:
    return ANALYZERS[language]()
```

### Dogfood Target: 402milly

Por qué 402milly:
- Frontend TypeScript + Backend Lambdas
- Spec clara de 1M→402M scaling
- Tests existentes para validar

---

## Phase 3: CI/CD Integration (Semanas 9-10)

### Objetivo
terra4mice como gate en pipelines.

### Entregables

#### 3.1 GitHub Action
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

#### 3.2 Pre-commit Hook
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/ultravioleta/terra4mice
    hooks:
      - id: terra4mice-check
        name: Check spec convergence
```

#### 3.3 PR Comments
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

## Phase 4: Apply Runner (Semanas 11-12)

### Objetivo
Ciclo `apply` que guía implementación.

### Entregables

#### 4.1 Interactive Apply
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

#### 4.2 Agent Integration (Council)
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

## Phase 5: Ecosystem Rollout (Mes 4+)

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

| Phase | Métrica | Target |
|-------|---------|--------|
| 1 | CLI funciona en Ultratrack | Plan muestra 9 missing |
| 2 | State inference accuracy | >80% en Python |
| 3 | CI integration functional | Blocks PR en x402-rs |
| 4 | Apply loop works | Developer completa feature guiado |
| 5 | Ecosystem adoption | 5+ proyectos usando |

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
