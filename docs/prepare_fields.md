# Declarative Prepare Fields

This document describes the declarative prepare field contract for playbook and skill assets. When a playbook declares `fields` or `fields_ref`, interactive `cafe prepare` renders prompts from those definitions. Playbooks without declarative fields keep the legacy `commands.prepare` interactive path.

## PrepareField schema

Each prepare question is declared as one field object:

| Property | Required | Description |
| --- | --- | --- |
| `id` | yes | Stable identifier (for example `input_method`, `quick_rigor`) |
| `type` | yes | `enum`, `boolean`, `template`, `text`, or `setup_mode` |
| `label` | yes | User-facing label |
| `write` | yes* | Issue config target (`spec.rigor`, `plan.template`, `pr.auto_create`, …) |
| `help` | no | Optional help text |
| `default` | no | Default value when applicable |
| `choices` | yes for `enum` / `setup_mode` | `{ value, label, description? }` entries |
| `show_when` | no | Simple visibility conditions |
| `group` | no | Logical grouping (`input`, `setup`, `quick_defaults`, `custom`) |

Allowed `write` targets:

- `spec.rigor`, `spec.template`, `spec.sync_github`, `spec.input_method`, `spec.issue_id`
- `plan.template`, `plan.sync_github`
- `pr.auto_create`, `pr.post_todo_list`

`setup_mode` fields describe the setup-mode chooser and must not declare `write`.

Optional document metadata:

```yaml
meta:
  prompt_for_spec_plan_config: true
fields:
  - id: setup_mode
    type: setup_mode
    ...
```

## Declaring fields on a playbook

Inline fields:

```yaml
commands:
  prepare:
    fields:
      - id: rigor
        type: enum
        label: Rigor
        write: spec.rigor
        ...
```

External static asset via `fields_ref`:

```yaml
commands:
  prepare:
    fields_ref: skill://spec/assets/prepare/default_prepare_fields.yaml
```

`fields` and `fields_ref` are mutually exclusive.

When a playbook keeps legacy `commands.prepare` blocks **and** declares `fields` / `fields_ref`, `cafe playbook validate` checks semantic parity between the legacy metadata and the declarative fields.

## `fields_ref` sources

### Skill asset URI (preferred)

```
skill://<skill-name>/assets/<relative-path>
```

Example:

```
skill://spec/assets/prepare/default_prepare_fields.yaml
```

Resolution uses `SkillLoader.get_skill_dir()` and only allows files under that skill's `assets/` directory.

### Playbook-relative path

```
assets/local_prepare_fields.yaml
```

Resolved relative to the playbook YAML file directory. The ref must not contain `://`.

## Security rules

Common rules for both formats:

- Load with `yaml.safe_load` / `json.load` only
- No script execution, no imports
- Allowed extensions: `.yaml`, `.yml`, `.json`
- Relative paths only; `..` is rejected
- Resolved path must stay inside the allowed sandbox directory
- Target must exist and be a regular file

Skill URI additional rules:

- URI must match `skill://<skill>/assets/<path>`
- Unknown skills are rejected
- Path segments must not include `scripts`
- References like `skill://spec/references/...` or `skill://spec/scripts/...` are rejected

Playbook-relative additional rules:

- Ref must not contain `://`
- Resolved path must stay under the playbook directory

## Canonical example

The built-in default playbook references:

`src/cafe/data/skills/spec/assets/prepare/default_prepare_fields.yaml`

That asset fully describes the current default prepare flow, including setup mode selection, quick/custom branches, GitHub-only prompts, and sync/PR defaults. Legacy `commands.prepare` remains on `src/cafe/data/playbooks/default.yaml` for runtime behavior and parity validation.

## Validation entry points

- `cafe playbook validate <name>`
- `load_playbook_file()` / `PlaybookLoader.load_model()`
- `PrepareProfile.resolved_prepare_fields()` (read-only contract access; does not drive UI)

Omitting both `fields` and `fields_ref` preserves backward-compatible behavior.
