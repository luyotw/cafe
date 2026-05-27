# Implementation Plan

## Goal
Two-tab settings UI without client-side routing.

## Negative space

- **Client router (e.g. react-router):** Not adding — only two static tabs; local component state is enough.
- **CSS framework:** Not adding — plain CSS covers the layout.
- **PWA / offline stack:** Not adding — not requested in the spec.

## Layering map

| Layer | Location |
| --- | --- |
| UI | `src/components/SettingsTabs.tsx` |
| State | `src/hooks/useSettingsTab.ts` |
| Persistence | `src/lib/settingsStorage.ts` |

## Dependency ADR

No new runtime or dev dependencies expected for this feature.

## Tasks

- [ ] Implement tab UI and local state
- [ ] Wire persistence layer
