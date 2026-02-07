# Minimal terra4mice Example

The simplest possible terra4mice spec to get started.

## Usage

```bash
# Copy to your project
cp terra4mice.spec.yaml /path/to/your/project/

# Initialize and check status
cd /path/to/your/project
t4m init
t4m plan
```

## What's Here

Just two features with a dependency:

```
my_first_feature
       ↓
my_second_feature (depends on first)
```

Edit `terra4mice.spec.yaml` to replace these with your actual resources.

## Next Steps

1. Edit the spec with your real features/modules
2. Run `t4m refresh` to auto-detect what's already implemented  
3. Run `t4m plan` to see what needs to be built
4. See `../starter-template/` for a more comprehensive example
