# Declarative Prepare Fields

This document describes the declarative prepare field contract for playbook and skill assets. A playbook that offers interactive preparation declares exactly one of `fields` or `fields_ref`; `cafe prepare` then renders and persists only those definitions. Bundled playbooks must use this contract. Older project/global playbooks without fields temporarily use the legacy adapter and receive a migration warning on every interactive prepare run.

## PrepareField schema

Each prepare question is declared as one field object:

| Property | Required | Description |
| --- | --- | --- |
| `id` | yes | Stable identifier (for example `input_method`, `quick_rigor`) |
| `type` | yes | `enum`, `boolean`, `template`, `text`, or `setup_mode` |
| `label` | yes | User-facing label |
| `write` | yes* | Issue config target (`<step>.<config-key>`) |
| `help` | no | Optional help text |
| `default` | no | Default value when applicable |
| `non_interactive_default` | no | Default used only by `--no-interactive`; otherwise `default` is used |
| `non_interactive` | no | Whether the default participates in non-interactive setup (default `true`) |
| `required` | no | Reject a missing non-interactive value when no default is declared |
| `normalize` | no | Boundary normalizer; currently `github_issue` is valid only for `spec.issue_id` |
| `choices` | yes for `enum` / `setup_mode` | `{ value, label, description? }` entries |
| `show_when` | no | Simple visibility conditions |
| `group` | no | Logical grouping (`input`, `setup`, `quick_defaults`, `custom`) |

Allowed `write` targets:

- `spec.rigor`, `spec.template`, `spec.sync_github`, `spec.input_method`, `spec.issue_id`
- `plan.template`, `plan.sync_github`
- `pr.auto_create`, `pr.post_todo_list`
- `<declared-step>.<config-key>` for any step declared in the same playbook

`setup_mode` fields describe the setup-mode chooser and must not declare `write`.

Template fields targeting a custom step still require that step's selected skill to
declare an output-template catalog. Non-template workflow-owned fields do not need
development-specific `spec`, `plan`, or `pr` configuration.

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

When a migration playbook keeps legacy `commands.prepare` blocks **and** declares `fields` / `fields_ref`, `cafe playbook validate` checks their semantic parity. New field-driven playbooks should keep all prompt copy, choices, conditions, and defaults in the field document instead.

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

`src/cafe/data/skills/cafe-spec/assets/prepare/default_prepare_fields.yaml`

That asset fully describes the current default prepare flow, including setup mode selection, quick/custom branches, GitHub-only prompts, and interactive/non-interactive defaults. `default.yaml` contains only the reference, so there is no parallel prompt definition.

## Validation entry points

- `cafe playbook validate <name>`
- `load_playbook_file()` / `PlaybookLoader.load_model()`
- `PrepareProfile.resolved_prepare_fields()` (read-only contract access; does not drive UI)

Bundled playbooks with interactive prepare are rejected when they omit both `fields`
and `fields_ref`. A bundled playbook with no interactive setup must explicitly set
`commands.prepare.prompt_for_spec_plan_config: false`. Project/global playbooks may
temporarily omit both sources, but interactive use emits a migration warning; migrate
them by adding an inline `fields` document or `fields_ref`.

## Workflow-owned fields

A non-development workflow can own its setup without declaring unrelated development
settings:

```yaml
commands:
  prepare:
    fields:
      - id: audience
        type: enum
        label: Audience
        write: synthesize.audience
        default: internal
        choices:
          - {value: internal, label: Internal}
          - {value: public, label: Public}
```

Both interactive and non-interactive preparation persist only
`synthesize.audience`; no `--input-method`, `spec`, `plan`, or `pr` block is
introduced.

## Custom step template fields

A custom output catalog belongs to its skill. A playbook may expose the selected
template during prepare by writing to that step's own issue-config block:

```yaml
commands:
  prepare:
    fields:
      - id: synthesis_template
        type: template
        label: Synthesis template
        write: synthesis.template
        default: auto
```

`cafe playbook validate` rejects this field unless the `synthesis` step's skill
declares an output-template catalog. The selected value is persisted as
`synthesis.template` in `issue.yaml`; runtime resolves it through that skill's
bundled catalog, with local and global template overrides retaining normal
precedence. `auto` exposes the catalog without forcing a particular file.
