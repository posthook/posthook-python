# Releasing posthook (Python)

## Release repo

https://github.com/posthook/posthook-python

## Registry

https://pypi.org/project/posthook/

## Steps

1. **Update the version** in `src/posthook/_version.py`:

   ```python
   VERSION = "1.1.0"  # bump as needed
   ```

2. **Run tests**:

   ```bash
   python -m pytest
   ```

3. **Build the distribution**:

   ```bash
   python -m build
   ```

4. **Upload to PyPI**:

   ```bash
   twine upload dist/*
   ```

5. **Commit, tag, and push**:

   ```bash
   git add -A
   git commit -m "Release v1.1.0"
   git tag v1.1.0
   git push origin main --tags
   ```

6. **Create GitHub release**:

   ```bash
   gh release create v1.1.0 --title "v1.1.0" --notes "Release notes here"
   ```

## Prerequisites

- PyPI account with access to the `posthook` package
- `build` and `twine` installed: `pip install build twine`
- PyPI API token configured in `~/.pypirc` or via `TWINE_USERNAME`/`TWINE_PASSWORD`

## Versioning

Follow [semver](https://semver.org/):

- **Patch** (1.0.x): Bug fixes, doc updates
- **Minor** (1.x.0): New features, backward-compatible changes
- **Major** (x.0.0): Breaking API changes
