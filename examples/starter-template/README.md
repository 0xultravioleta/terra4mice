# terra4mice Starter Template

A comprehensive template to get you started with terra4mice quickly.

## Quick Start

```bash
# 1. Copy to your project
cp terra4mice.spec.yaml /path/to/your/project/

# 2. Edit the spec to match your project
#    - Remove example resources you don't need
#    - Add your own features, modules, endpoints, etc.
#    - Update file paths to match your codebase

# 3. Initialize terra4mice
cd /path/to/your/project
t4m init

# 4. Auto-detect implemented resources
t4m refresh

# 5. See what's missing
t4m plan
```

## What's Included

This template includes examples of common resource types:

| Type | Use Case |
|------|----------|
| `feature` | User-facing functionality (auth, payments, etc.) |
| `module` | Internal code structure (packages, services) |
| `endpoint` | REST/GraphQL API routes |
| `migration` | Database schema changes |
| `test` | Test coverage tracking |
| `docs` | Documentation completeness |

## Customization Tips

### 1. Start Small
Don't try to spec everything at once. Start with 3-5 key resources and expand as you go.

### 2. Use Dependencies Wisely
`depends_on` creates an execution order. Only add dependencies that truly block implementation.

### 3. Leverage Inference
Add `files:` to resources so terra4mice can auto-detect implementation status:
```yaml
my_feature:
  files:
    - src/features/my_feature.py
```

### 4. Add Custom Attributes
The `attributes:` block is freeform. Add whatever metadata is useful:
```yaml
my_feature:
  attributes:
    description: "..."
    owner: "@alice"
    priority: P0
    due_date: "2024-03-01"
```

### 5. Custom Resource Types
You're not limited to the examples! Create any type:
```yaml
resources:
  contract:  # For Solidity
  component: # For React
  workflow:  # For CI/CD
  epic:      # For product management
```

## Team Collaboration

For teams, configure S3 backend in the spec:

```yaml
backend:
  type: s3
  bucket: my-team-terra4mice
  key: projects/myproject/state.json
  region: us-east-1
  locking: true
```

## Commands Reference

```bash
t4m init              # Create state file
t4m refresh           # Auto-detect resource status
t4m plan              # Show diff: spec vs state
t4m plan --json       # Machine-readable output

t4m state list        # List all tracked resources
t4m state show <id>   # Show resource details

t4m mark <id> created # Manually mark as complete
t4m mark <id> partial # Mark as in-progress
t4m mark <id> broken  # Mark as broken/blocked

t4m apply             # Interactive: walk through plan
```

## More Info

- [terra4mice README](https://github.com/ultrasev/terra4mice)
- [Full Spec Documentation](../../SPEC.md)
