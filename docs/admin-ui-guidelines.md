# Admin UI Guidelines

## Layout
- Use `minmax(0, 1fr)` in dashboard grids to avoid overflow.
- Set `min-width: 0` on card containers inside CSS grids.
- Prefer compact table mode for 2-column KPI tables.

## Data Semantics
- Show business labels instead of internal codes where possible.
- Keep registry and dashboard naming consistent.
- Support backward-compatible payload aliases only in API, not in UI labels.

## Responsive
- Dashboard must stay inside card boundaries on desktop/tablet/mobile.
- Long values should wrap (`overflow-wrap`, `word-break`) before forcing horizontal scroll.
- Use horizontal scroll only for truly wide data tables.

## UX in Sales Context
- Keep “captured facts” and “next action” visible to operator.
- Make stage/state clear without exposing technical internals.
