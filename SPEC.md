# terra4mice - SPEC

> State-Driven Development Framework
> "El software no está listo cuando funciona, está listo cuando el state converge con el spec"

## 1. Visión

terra4mice es un framework que aplica el modelo mental de Terraform al desarrollo de software. Mientras Terraform gestiona infraestructura, terra4mice gestiona **desarrollo vivo**.

El problema que ataca: En el vivecoding, se va back-and-forth muchas veces. Al final queda una lista de TODOs, pero no hay forma de saber qué del spec está realmente implementado vs lo que el equipo *cree* que existe.

## 2. Problema

### El caos del vivecoding

```
1. Se implementa A
2. B rompe A
3. Se hace workaround C
4. Queda TODO para D
5. Alguien dice "ya funciona"
6. Semanas después: D nunca existió
```

### El sistema actual no sabe:

- Qué partes del spec están **completas**
- Qué partes están **mockeadas**
- Qué partes funcionan solo en **ciertos paths**
- Qué partes existen solo en **tu cabeza**

### TODOs vs State

| TODO | State |
|------|-------|
| No verificable | Binario o progresivo |
| No bloquea | Bloquea convergencia |
| No semántica de sistema | Representa deuda real |
| Se puede ignorar | No se puede mentir |

## 3. Solución: El Modelo Terraform

### 3.1 Development Spec (Desired State)

Archivo declarativo escrito por humanos. Es **contrato ejecutable**, no documentación.

```yaml
# app.spec.yaml
app:
  auth:
    login:
      status: required
      endpoints: [POST /auth/login]
      tests: [unit, integration]
    refresh:
      status: required
    revoke:
      status: required

  payments:
    provider: x402
    modes:
      - usdc
      - split

  audit:
    logging: mandatory
    replayable: mandatory
```

### 3.2 Development State (Current State)

Generado **automáticamente** por terra4mice. Nunca escrito a mano.

```yaml
# app.state.yaml (auto-generated)
app:
  auth:
    login: implemented
    refresh: partial
    revoke: missing

  payments:
    usdc: implemented
    split: missing

  audit:
    logging: implemented
    replayable: missing
```

### Estados permitidos por unidad:

| Estado | Significado |
|--------|-------------|
| `missing` | No existe código |
| `partial` | Existe pero incompleto |
| `implemented` | Funciona y tiene tests |
| `broken` | Existía pero ahora falla |
| `deprecated` | Marcado para remover |

### 3.3 Plan

El diff entre spec y state:

```
$ terra4mice plan

Plan:
  + implement auth.revoke
  ~ complete auth.refresh
  + implement payments.split
  + implement audit.replayable

4 actions required
0 destructive changes
```

**Invariante crítico**: Si el plan no está vacío, el sistema **no está completo**.

### 3.4 Apply

Un ciclo de desarrollo, **no un deploy**:

1. Escribir/modificar código
2. Ejecutar validadores
3. Recalcular state
4. Re-evaluar plan

Si después del apply el plan sigue no vacío, el sistema permanece incompleto.

## 4. Validadores de Estado

El state se infiere usando validadores combinables:

| Validador | Qué detecta |
|-----------|-------------|
| Tests funcionales | Feature funciona end-to-end |
| Coverage semántico | Paths críticos cubiertos |
| Rutas alcanzables | Código no es dead code |
| Contratos explícitos | Interfaces cumplen spec |
| Checks de invariantes | Assertions no fallan |

Los validadores **nunca** marcan algo como `implemented` sin evidencia verificable.

## 5. Integración CI/CD

```yaml
# .github/workflows/terra4mice.yml
- name: Check convergence
  run: |
    terra4mice plan
    if [ $? -ne 0 ]; then
      echo "Plan not empty - build fails"
      exit 1
    fi
```

**Regla base**: Si `terra4mice plan` produce acciones pendientes, el build falla.

Esto elimina:
- Merges incompletos
- "Después lo arreglamos"
- Deuda invisible

## 6. Análisis AQAL

| Cuadrante | Score | Notas |
|-----------|-------|-------|
| I (Experiencia) | 8 | Claridad mental: saber exactamente qué falta |
| IT (Artefactos) | 10 | CLI, specs, plans - output tangible |
| WE (Cultura) | 7 | Cultura de honestidad técnica, no autoengaño |
| ITS (Sistemas) | 9 | Integración CI/CD, gates automáticos |

**Balance**: Bueno
**Recomendación**: Fortalecer WE con guías de adopción y comunidad

## 7. Non-Goals

terra4mice **NO**:

- Gestiona infraestructura (eso es Terraform)
- Reemplaza CI (lo complementa)
- Reemplaza tests (los usa como input)
- Es otro framework de testing

terra4mice **SÍ**:

- Orquesta el progreso real
- Expone deuda real
- Impide autoengaño técnico
- Es source of truth para "¿ya está?"

## 8. Filosofía de Diseño

1. **Estado antes que intención** - Lo que existe, no lo que queremos
2. **Evidencia antes que percepción** - Tests, no "creo que funciona"
3. **Convergencia antes que velocidad** - Mejor lento y correcto
4. **Claridad antes que heroísmo** - Plan visible, no magia

## 9. Definición de Completo

Un proyecto está completo cuando:

```
$ terra4mice plan

No changes required.
State matches spec.
```

Nada más.

## 10. Métricas de Éxito

| Métrica | Baseline | Target |
|---------|----------|--------|
| Bugs post-merge por spec drift | ~3/mes | 0 |
| Tiempo para saber "¿ya está?" | Horas/días | Segundos |
| Deuda técnica invisible | Alta | Cero |
| Confianza en merges | Media | Alta |

## 11. User Stories

### Como developer vivecoding:
- Quiero saber exactamente qué me falta implementar
- Para no tener que revisar manualmente cada feature

### Como tech lead:
- Quiero bloquear merges que no convergen con el spec
- Para evitar "lo arreglamos después"

### Como PM:
- Quiero ver progreso real, no percibido
- Para dar estimados honestos

### Como CI/CD pipeline:
- Quiero un gate objetivo de completitud
- Para no depender de juicio humano
