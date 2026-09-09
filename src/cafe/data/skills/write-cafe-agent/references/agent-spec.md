# CAFE Agent File Specification

## 1. Purpose and checklist contract

An agent file defines a durable role persona. It is read by the active phase and may also be converted into phase checklist items. It must contain role-specific behavior that remains valid wherever that role is used, not one phase's procedure.

When a phase skill declares `workflow.checklist.include_role_guidance: true`, CAFE reads the selected agent file and converts every line whose trimmed form starts with `- ` into a checkbox under `## Agent Guidelines Checklist`. Indented bullets are also extracted. Paragraphs are not extracted.

Therefore:

- every `- ` line must be an intentional, independently verifiable checklist gate;
- guidelines must use one flat list with no nested `- ` bullets;
- examples, alternatives, and explanatory sublists must remain prose rather than bullets;
- a behavioral requirement must not exist only in prose if it needs checklist enforcement.

## 2. Location and identity

Use `<root>/agents/<role>/<name>.md`, where root is selected by ownership:

- built in: `src/cafe/data/agents/`;
- global personal: `~/.cafe/agents/`;
- project override: `.cafe/agents/`.

The filename stem and frontmatter `name` must match exactly, including case and non-ASCII characters. Use this frontmatter shape:

```yaml
---
name: <filename stem>
description: <concise role specialization and native-language declaration when non-English>
---
```

Use `母語為繁體中文。` for a Traditional Chinese built-in agent. English is the default and does not need an explicit native-language sentence.

## 3. Body shape

After frontmatter, write exactly:

1. one concise sentence that states the role;
2. one lead-in sentence introducing behavioral guidelines;
3. a flat list of one or more actionable guidelines.

English template:

```markdown
You are <role statement>. Your behavioral guidelines are as follows:

- <Actionable role guideline.>
```

Traditional Chinese template:

```markdown
你是<角色敘述>，你的行為準則如下：

- <可獨立檢核的角色準則>
```

Follow the natural punctuation and terminology of the agent's language. Keep each guideline to one checklist concept where practical.

## 4. Ownership boundary

Agent guidelines own personal style and role judgment that should follow the agent across phases. Examples include requirement focus for a PM, test discipline for a developer, or review rigor for a reviewer.

Do not put these concerns in an agent file:

- ordered phase procedure or iteration-specific steps;
- artifact paths, placeholders, or output templates;
- baton, handoff, or playbook routing;
- tool grants or command sequences;
- repository-wide rules that apply regardless of role.

Put phase procedure in `write-cafe-phase`-owned skills and routing in `write-cafe-playbook`-owned playbooks.

## 5. Canonical role patterns

Use the built-in files as the source examples:

- PM: `src/cafe/data/agents/pm/Roger.md` and `范曙燁.md` keep attention on requirements, user perspective, and clear communication.
- Developer: `src/cafe/data/agents/developer/David.md`, `src/cafe/data/agents/developer/Nick.md`, `src/cafe/data/agents/developer/羅博高.md`, and `src/cafe/data/agents/developer/魯柔凡.md` cover coding standards, project language and commit conventions, implementation discipline, and robust tests.
- Reviewer: `src/cafe/data/agents/reviewer/Richard.md` and `林萌芝.md` define review rigor, relevance, and restrained presentation.

When creating another role, copy the structure rather than copying role-specific content.

## 6. Language counterparts

Counterparts in different languages must preserve the same role boundary and checklist behavior. They may use idiomatic phrasing rather than literal translation. Compare the extracted guideline set concept by concept and document any intentional specialization in the description.

## 7. Validation checklist

- filename stem equals frontmatter `name`;
- `description` is concise and states a non-English native language;
- the body uses the agent's declared language consistently;
- one concise role statement precedes the guidelines;
- at least one flat top-level `- ` guideline exists;
- no nested `- ` bullets or decorative bullet lists exist;
- every extracted item is actionable, role-specific, and phase-independent;
- equivalent language variants preserve equivalent behavior;
- checklist extraction contains every intended guideline and nothing else.
