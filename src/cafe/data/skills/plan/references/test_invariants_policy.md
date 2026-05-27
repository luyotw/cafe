# Test Invariants Policy

Workflow agents should write tests that protect **stable invariants** (business rules and user-journey outcomes), not fragile implementation details.

## What to test

- **Business invariants** — rules that must remain true across refactors.
- **User-journey outcomes** — what the user can do and what they observe as results, without binding to incidental presentation.
- **Contract-level behavior** of public interfaces (inputs → outputs / side effects) when those contracts are intentional and stable.
- **Pure business logic** in shared library modules — cover with **unit tests** using representative inputs, boundaries, and error cases.

## What not to test (unless explicitly required)

| Avoid binding to | Exception |
| --- | --- |
| Exact **UI copy** | Copy listed as an explicit product requirement in the spec (legal/contractual wording, mandated labels) |
| **CSS class names** and styling | — |
| **DOM structure** (nesting, order, wrappers) | — |
| **Internal state shape** | Only when state shape is itself a stable external contract |

## Allowed integration-test UI assertions

When verifying user-visible outcomes, prefer stable contracts over presentation details:

- Accessibility **roles** and **labels** (for example, `button`, `alert`).
- **Test identifiers** designed for testing (`data-testid`, not CSS classes).
- **Exact copy** only when the spec for this change explicitly requires that wording.

Integration tests must **not** use “no UI assertions at all” as a blanket rule; use stable contracts instead of brittle structure.

## Unit vs integration

| Layer | Organize by | Assert on |
| --- | --- | --- |
| **Unit** | Pure function / shared module | Invariants over inputs and boundaries |
| **Integration** | **User journey** + **invariant outcome** | System behavior across boundaries, not per-component internals |

## Good vs Bad (place order journey)

**Invariant:** Order total is computed correctly and a valid order is created.

### Good (invariant-focused)

- **Unit — Total calculation invariant:** Given items and discounts, total equals expected value (empty cart, max discount, rounding).
- **Integration — Place order succeeds:** Given a valid cart, when the user places an order, an order exists and success is visible via a stable contract (alert role, test id, or non-copy outcome signal)—not via CSS classes or DOM nesting.

### Bad (implementation-detail focused)

- Asserting exact copy like `Order placed successfully!` when that string is not a spec-mandated product requirement.
- Asserting CSS classes such as `.btn-primary` or `.success-banner`.
- Asserting DOM position such as “success message is the 2nd child of the header wrapper”.
- Asserting `state.checkout.step === "CONFIRMATION"` when that schema is not a public contract.

## Plan Test List (required in plan output)

Every plan must include:

```markdown
## Test List

### Unit tests (N)
1. **Label** — Invariant: … — Scope: …

### Integration tests (M)
1. **Label** — Journey: … — Invariant outcome: … — Boundary: …
```

When **N** or **M** is zero, add one sentence explaining why.

## Develop and review

- **Develop:** Every new or changed test must map to a plan Test List item; follow this policy for assertions.
- **Review:** Reject added/changed tests that violate the “what not to test” rules; accept allowed stable UI contracts. Existing unrelated tests do not require wholesale rewrites.
