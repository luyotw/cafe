# Implementation Plan

## Goal
Add CSV export for report downloads.

## Negative space

- **Heavy spreadsheet library:** Not adding — `csv` module is sufficient for flat exports.

## Layering map

| Layer | Location |
| --- | --- |
| Business logic | `src/cafe/services/export_service.py` |
| HTTP / UI | `src/cafe/views/report_views.py` |

## Dependency ADR

| Package | Type | Why | Alternatives | Requirement |
| --- | --- | --- | --- | --- |
| `openpyxl` | runtime | Optional XLSX path for power users | Built-in `csv` only | Feature: Excel export toggle |

**`openpyxl`:** Chosen for maintained XLSX write support; `csv` remains the default path. Serves acceptance criterion for Excel download.

## Tasks

- [ ] Add export service and tests
- [ ] Expose download endpoint
