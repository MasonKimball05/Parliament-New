# Changelogs Archive

This directory contains detailed changelogs for each major version of Parliament.

## Structure

- Each major/minor version has its own detailed changelog file
- Format: `vX.Y.Z.md` (e.g., `v2.1.0.md`)
- Main `CHANGELOG.md` in root directory contains version summaries and links

## Available Changelogs

### v2.x Series - Security Focus
- [v2.1.0](./v2.1.0.md) - Security & Authentication Enhancements (Dec 26, 2025)

## What's in a Detailed Changelog?

Each version file includes:
- ✅ Complete feature documentation
- ✅ Technical implementation details
- ✅ Code examples and snippets
- ✅ Files created/modified
- ✅ Database migrations
- ✅ Configuration changes
- ✅ Security metrics
- ✅ Deployment guide
- ✅ Testing documentation
- ✅ Known issues and limitations

## When to Create a New Changelog

Create a new detailed changelog file when:
- Releasing a new minor version (v2.X.0)
- Releasing a major version (vX.0.0)
- Making significant feature additions

Patch versions (v2.1.X) can be appended to the existing minor version file.

## Template

When creating a new changelog, use this structure:

```markdown
# Version X.Y.Z - Brief Description

**Release Date:** Month DD, YYYY
**Status:** Deployment status

---

## New Features

### 1. Feature Name
- What was added
- How it works
- Technical implementation
- Files modified

## Configuration Changes
- Settings updates
- Environment variables

## Database Changes
- Migrations
- Schema updates

## Deployment Guide
- Step-by-step instructions

## Breaking Changes
- List any breaking changes

## Contributors
- Names
```

---

**Last Updated:** 2025-12-26
