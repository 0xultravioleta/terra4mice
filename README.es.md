# terra4mice

> State-Driven Development Framework
>
> "El software no esta listo cuando funciona, esta listo cuando el state converge con el spec"

terra4mice aplica el modelo mental de Terraform al desarrollo de software. Mientras Terraform gestiona infraestructura, terra4mice gestiona **desarrollo vivo**.

## El Problema

En el vivecoding, pasa esto:

1. Se implementa A
2. B rompe A
3. Se hace workaround C
4. Queda TODO para D
5. Alguien dice "ya funciona"
6. Semanas despues: D nunca existio

**El sistema no sabe**:
- Que partes del spec estan completas
- Que partes estan mockeadas
- Que partes existen solo en tu cabeza

## La Solucion

```
SPEC (desired state)  ->  Lo que DEBE existir (YAML declarativo)
STATE (current state) ->  Lo que EXISTE (inferido/marcado)
PLAN (diff)           ->  spec - state = trabajo a hacer
APPLY (execution)     ->  Ciclos hasta convergencia
```

## Quick Start

```bash
# Instalar
pip install terra4mice

# Con analisis AST profundo (opcional, Python >=3.10)
pip install terra4mice[ast]

# Con remote state backend (S3 + DynamoDB locking)
pip install terra4mice[remote]

# Inicializar en tu proyecto
cd my-project
terra4mice init

# Ver que falta
terra4mice plan

# Auto-detectar estado del codebase
terra4mice refresh

# Listar recursos en state
terra4mice state list

# Marcar algo como implementado
terra4mice mark feature.auth_login --files src/auth.py

# Reporte CI (JSON)
terra4mice ci --format json
```

## Comandos

### `terra4mice init`

Crea archivos de spec y state:

```bash
terra4mice init
# Created: terra4mice.spec.yaml
# Created: terra4mice.state.json
```

### `terra4mice plan`

Muestra que falta para converger:

```
$ terra4mice plan

terra4mice will perform the following actions:

  + feature.auth_login
      # Resource declared in spec but not in state
  + feature.auth_refresh
      # Resource declared in spec but not in state
  ~ feature.auth_logout
      # Resource is partially implemented

Plan: 2 to create, 1 to update.
```

### `terra4mice refresh`

Auto-detecta el estado del codebase usando multiples estrategias:

```
$ terra4mice refresh

Scanning /my-project for resources...

Inference Report
============================================================
IMPLEMENTED (5 resources)
  module.models
    Confidence: [##########] 100%
    Files: src/models.py
    Evidence: Explicit files found, AST analysis: 100% match

PARTIAL (1 resources)
  feature.auth
    Confidence: [######----] 60%

MISSING (2 resources)
  feature.payments
  feature.notifications

Summary
  Convergence: 68.8%
```

Estrategias de inferencia (en orden de prioridad):
1. **tree-sitter AST** (con `[ast]`) - verifica funciones, clases, exports contra spec attributes
2. **stdlib ast** - analisis Python basico
3. **Regex** - Solidity, TypeScript/JavaScript patterns
4. **Heuristica** - tamano de archivos config/docs

### `terra4mice state list`

Lista todos los recursos en el state:

```
$ terra4mice state list

feature.auth_login
feature.auth_refresh
module.payment_processor
```

### `terra4mice state show <address>`

Muestra detalles de un recurso:

```
$ terra4mice state show feature.auth_login

# feature.auth_login
type     = "feature"
name     = "auth_login"
status   = "implemented"
files    = ["src/auth.py", "src/routes/login.py"]
tests    = ["tests/test_auth.py"]
```

### `terra4mice mark <address>`

Marca un recurso con un status:

```bash
# Marcar como implementado
terra4mice mark feature.auth_login --files src/auth.py

# Marcar como parcial
terra4mice mark feature.auth_refresh --status partial --reason "Missing token rotation"

# Marcar como roto
terra4mice mark feature.auth_logout --status broken --reason "Tests failing"
```

### `terra4mice apply`

Loop interactivo para implementar el plan:

```
$ terra4mice apply

============================================================
Next: + feature.auth_login
      Resource declared in spec but not in state

Attributes: {'endpoints': ['POST /auth/login']}

Action: [i]mplement, [p]artial, [s]kip, [q]uit? i
Files that implement this: src/auth.py, src/routes/login.py
Marked as implemented: feature.auth_login
```

### `terra4mice state pull / push`

Sincronizar state entre backends local y remoto:

```bash
# Descargar state remoto a archivo local
terra4mice state pull -o local_backup.json

# Subir state local al backend remoto
terra4mice state push -i local_backup.json
```

### `terra4mice force-unlock <lock-id>`

Liberar forzosamente un lock atascado (cuando un proceso crashea a mitad de operacion):

```bash
terra4mice force-unlock a1b2c3d4-5678-9abc-def0-123456789abc
# Lock forcefully released: a1b2c3d4-...
# WARNING: Releasing a lock held by another process may cause state corruption.
```

### `terra4mice init --migrate-state`

Migrar state local a un backend remoto configurado en el spec:

```bash
# 1. Agregar seccion backend: al terra4mice.spec.yaml
# 2. Ejecutar migracion
terra4mice init --migrate-state
# State migrated to s3 backend.
#   Resources: 12
#   Serial: 45
```

### `terra4mice ci`

Output para CI/CD pipelines:

```bash
# JSON (machine-readable)
terra4mice ci --format json

# Markdown (PR comments)
terra4mice ci --format markdown --comment pr-comment.md

# Fail if convergence < threshold
terra4mice ci --fail-under 80
```

## Spec File Format

```yaml
# terra4mice.spec.yaml
version: "1"

resources:
  feature:
    auth_login:
      attributes:
        description: "User login"
        endpoints: [POST /auth/login]
      depends_on: []

    auth_refresh:
      attributes:
        description: "Token refresh"
      depends_on:
        - feature.auth_login

  module:
    state_manager:
      attributes:
        class: StateManager
        functions: [load, save, list, mark_created]
      files:
        - src/state_manager.py

  endpoint:
    api_users:
      attributes:
        method: GET
        path: /api/users
      depends_on:
        - feature.auth_login
```

### Spec Attributes para AST Verification

Con `terra4mice[ast]` instalado, estos atributos se verifican contra el codigo real:

```yaml
attributes:
  class: StateManager              # busca en clases
  functions: [load, save, list]    # busca en funciones definidas
  entities: [Resource, State]      # busca en clases/interfaces/types/enums
  exports: [WorkerRatingModal]     # busca en exports (TS/JS)
  imports: [useState, useEffect]   # busca en imports
  commands: [init, plan, refresh]  # substring match en funciones
  strategies: [explicit_files]     # substring match en funciones+clases
```

Lenguajes soportados: Python, TypeScript/TSX, JavaScript, Solidity.

## State File Format

```json
{
  "version": "1",
  "serial": 3,
  "last_updated": "2026-01-27T15:30:00",
  "resources": [
    {
      "type": "feature",
      "name": "auth_login",
      "status": "implemented",
      "files": ["src/auth.py"],
      "tests": ["tests/test_auth.py"],
      "created_at": "2026-01-27T14:00:00"
    }
  ]
}
```

## Remote State Backend

Almacena el state en S3 con locking opcional via DynamoDB para colaboracion en equipo. Agrega una seccion `backend:` al spec:

```yaml
# terra4mice.spec.yaml
version: "1"

backend:
  type: s3
  config:
    bucket: my-terra4mice-state
    key: projects/myapp/terra4mice.state.json
    region: us-east-1
    lock_table: terra4mice-locks    # tabla DynamoDB (opcional)
    profile: my-aws-profile         # perfil AWS (opcional)
    encrypt: true                   # S3 SSE (opcional)

resources:
  # ... tu spec sin cambios ...
```

Sin `backend:` o con `type: local`, el comportamiento no cambia (archivo local).

### Setup de la tabla DynamoDB para locking

```bash
aws dynamodb create-table \
  --table-name terra4mice-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

### Como funciona el locking

Cuando se configura un `backend` con `lock_table`, los comandos que escriben state (`refresh`, `mark`, `lock`, `unlock`, `state rm`, `state push`) adquieren automaticamente un lock en DynamoDB antes de escribir. Si otro proceso tiene el lock, el comando falla con un error descriptivo mostrando quien lo tiene y cuando fue adquirido.

## CI/CD Integration

```yaml
# .github/workflows/terra4mice.yml
name: Check Convergence

on: [push, pull_request]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install terra4mice[ast]
      - run: terra4mice plan --detailed-exitcode
        # Returns 2 if there are pending changes
```

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 1 - MVP CLI | DONE | init, plan, refresh, state, mark, apply, ci |
| 2 - tree-sitter AST | DONE | Multi-language deep analysis, spec attribute verification |
| 3 - Multi-AI Contexts | DONE | Track which AI (Claude, Codex, Kimi) has context on what |
| 4 - CI/CD Integration | DONE | GitHub Action, PR comments, convergence badges |
| 4.5 - Remote State | DONE | S3 backend, DynamoDB locking, state pull/push, migrate-state |
| 5 - Apply Runner | PLANNED | Interactive apply loop, agent integration |
| 6 - Ecosystem Rollout | PLANNED | Deploy across Ultravioleta DAO projects |

## Seguimiento de Contexto Multi-Agente

Cuando multiples AIs trabajan en el mismo proyecto, cada una lleva su propio contexto aislado. El grupo de comandos `contexts` provee un **registro de contextos** para saber que AI tiene contexto de que recursos.

### `terra4mice contexts list`

Muestra todos los agentes y sus contextos de recursos:

```
$ terra4mice contexts list

AGENT          RESOURCE              LAST SEEN    STATUS
claude-code    module.inference      2min ago     active
claude-code    module.analyzers      2min ago     active
codex          feature.auth_login    1hr ago      stale
kimi-2.5       feature.frontend      30min ago    active

Agents: 3 | Active contexts: 4 | Stale: 1
```

### `terra4mice contexts show <agent>`

Muestra contexto detallado para un agente especifico:

```
$ terra4mice contexts show claude-code

# claude-code
Last active: 2min ago
Status: active

Resources in context:
  module.inference      implemented    2min ago
  module.analyzers      implemented    2min ago
  feature.ci            partial        15min ago

Files touched:
  src/terra4mice/inference.py
  src/terra4mice/analyzers.py
```

### `terra4mice contexts sync`

Sincronizar contexto entre agentes:

```bash
# Sincronizar todo el contexto de un agente a otro
terra4mice contexts sync --from=claude-code --to=codex

# Sincronizar solo recursos especificos
terra4mice contexts sync --from=claude-code --to=codex --resources=module.inference,module.analyzers

# Dry run para ver que se sincronizaria
terra4mice contexts sync --from=claude-code --to=codex --dry-run
```

### `terra4mice contexts export / import`

Exportar e importar contextos de agentes para backup o transferencia:

```bash
# Exportar contexto de un agente a archivo
terra4mice contexts export claude-code -o claude-context.json

# Importar contexto desde archivo
terra4mice contexts import codex -i claude-context.json

# Exportar todos los agentes
terra4mice contexts export --all -o all-contexts.json
```

### `terra4mice mark --agent`

Marcar recursos con atribucion de agente:

```bash
# Marcar como implementado por un agente especifico
terra4mice mark module.auth --status implemented --agent=codex --files src/auth.py

# Marcar como parcial con contexto de agente
terra4mice mark feature.payments --status partial --agent=claude-code --reason "Missing refund logic"
```

Esto automaticamente actualiza el registro de contextos para que otros agentes sepan quien trabajo en que.

## Ejemplos de Workflows Multi-Agente

### Ejemplo 1: Handoff Entre Agentes

Cuando un agente completa trabajo y otro toma el control:

```bash
# Claude termina de trabajar en inference
terra4mice mark module.inference --status implemented --agent=claude-code --files src/inference.py

# Antes de que Codex empiece, sincronizar contexto
terra4mice contexts sync --from=claude-code --to=codex --resources=module.inference

# Codex ahora puede ver lo que hizo Claude
terra4mice contexts show codex
```

### Ejemplo 2: Desarrollo en Paralelo

Multiples agentes trabajando en diferentes features:

```bash
# Ver quien trabaja en que
terra4mice contexts list

# Cada agente marca su propio trabajo
terra4mice mark feature.auth --agent=claude-code --status implemented
terra4mice mark feature.payments --agent=kimi-2.5 --status partial

# Verificar conflictos (mismo recurso, diferentes agentes)
terra4mice plan --check-conflicts
```

### Ejemplo 3: Recuperacion de Contexto

Cuando un agente pierde contexto (nueva sesion):

```bash
# Exportar contexto antes de que termine la sesion
terra4mice contexts export claude-code -o session-backup.json

# En nueva sesion, restaurar contexto
terra4mice contexts import claude-code -i session-backup.json

# O sincronizar desde otro agente que tenga contexto actual
terra4mice contexts sync --from=codex --to=claude-code
```

### Ejemplo 4: Integracion CI con Multi-Agente

```yaml
# .github/workflows/terra4mice.yml
- name: Check convergence and contexts
  run: |
    terra4mice plan --detailed-exitcode
    terra4mice contexts list --format json > contexts.json
    # Fail si algun contexto esta stale > 24h
    terra4mice contexts list --stale-threshold 24h --fail-if-stale
```

## Filosofia

1. **Estado antes que intencion** - Lo que existe, no lo que queremos
2. **Evidencia antes que percepcion** - Tests, no "creo que funciona"
3. **Convergencia antes que velocidad** - Mejor lento y correcto
4. **Claridad antes que heroismo** - Plan visible, no magia

## Definicion de Completo

Un proyecto esta completo cuando:

```
$ terra4mice plan

No changes. State matches spec.
```

Nada mas.

## License

MIT - Public good for the developer community.

## Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
