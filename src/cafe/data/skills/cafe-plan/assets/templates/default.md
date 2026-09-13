## Implementation Analysis

## Confirmed Implementation Approach

- **Recommended direction:** [The user-confirmed implementation direction]
- **Will do:** [The required scope this Plan covers]
- **Will not do:** [Related work intentionally excluded]
- **Key trade-offs:** [Material scope, cost, reliability, or maintenance trade-offs; write "None" if none apply]

## Issue Decomposition Assessment

- Decision: `keep` or `split`
- Rationale: [Repository evidence for keeping or splitting delivery]
- Current issue scope: [Independently acceptable outcome to deliver now]
- Trigger: [none, product scope, or implementation scope]

### Proposed follow-up issues

| Title | Goal | Depends on | Scope boundary | Non-goals | Definition of Done |
| --- | --- | --- | --- | --- | --- |

### Negative space

> What we are **not** introducing, and why. If nothing obvious applies, state explicitly (e.g. "No additional runtime deps; template-only change.").

| Declined | Reason |
| --- | --- |
| Client router (e.g. react-router) | Only two static tabs; local state suffices |
| CSS framework | Plain CSS covers the design |
| PWA / offline stack | Not requested in the spec |

> If `.cafe/strategic_context.yaml` has `documents.principles.path` with `status: exists`, tie declines to principles red lines / out-of-scope items. Otherwise leave principles cross-refs blank.

### Layering map

> Where business logic, persistence, and UI live — use **concrete file or module paths**.

| Layer | Location |
| --- | --- |
| Business logic | `services/auth_service.py` |
| Persistence / data | `models/user.py`, ORM queries in service layer |
| UI / HTTP | `views/auth_views.py`, `templates/auth/` |

### Dependency ADR

> Every **runtime and dev** dependency this issue expects to add. If none: write **"No new dependencies expected."**

| Package | Type | Why | Alternatives considered | Requirement served |
| --- | --- | --- | --- | --- |
| *(example)* `pyjwt` | runtime | JWT signing for login tokens | Session cookies only | Auth token feature |

> For a **new major** version, note release age. If the major shipped within the last **30 days**, justify the risk here or pick a stable alternative.

* Code style reference examples
    * Service layer pattern: `services/user_service.py`
    * View layer style: `views/user_views.py`
    * Model usage: How to use `User.objects.filter()`
    * Exception handling: Using custom Exception classes
* Implementation details
    * Create `services/auth_service.py` containing `AuthService` class
    * Implement `authenticate()` and `register_user()` methods
    * Content to move includes:
        * Password validation logic
        * JWT token generation logic
        * User registration validation (email format, password strength, etc.)
        * Database queries and user creation
    * Service layer returns `ServiceResult` object (using dataclass)
    * View layer retains:
        * HTTP request parameter retrieval and basic validation
        * Calling service layer methods
        * Returning HTTP responses based on service layer results (200/400/401/500)
    * No database migration needed, no API format changes needed
* Data format examples
    ```python
    # Service layer returned object structure
    @dataclass
    class ServiceResult:
        success: bool
        data: Optional[Dict[str, Any]] = None
        error: Optional[str] = None
        error_code: Optional[str] = None

    # Login success example
    ServiceResult(
        success=True,
        data={
            'user_id': 123,
            'username': 'john_doe',
            'token': 'eyJhbGciOiJIUzI1NiIs...',
            'expires_at': 1728012345
        }
    )

    # Login failure example
    ServiceResult(
        success=False,
        error='Invalid credentials',
        error_code='AUTH_INVALID_CREDENTIALS'
    )

    # View layer JSON response (maintain existing format)
    {
        "success": true,
        "data": {
            "user_id": 123,
            "username": "john_doe",
            "token": "eyJhbGciOiJIUzI1NiIs...",
            "expires_at": 1728012345
        }
    }
    ```

## Test List

### Unit tests (N)
1. **Label** — Invariant: … — Scope: pure business logic / shared library module

### Integration tests (M)
1. **Label** — Journey: … — Invariant outcome: … — Boundary: system-level behavior (not per-component)

_(If N or M is 0, one sentence explains why.)_

## Todo List

- [ ] `PLAN-001` — Source: `plan` — Work: Create the service-layer foundation — Closure: production callers use the new boundary — Evidence: targeted unit and integration tests
- [ ] `PLAN-002` — Source: `plan` — Work: Implement authentication and registration behavior — Closure: specified success and failure journeys pass — Evidence: targeted business-logic and caller-path tests

### Todo authoring notes

Use one stable unique `PLAN-NNN` row per bounded implementation unit. Keep Work, Closure, and Evidence non-empty. DoD verification belongs in Closure/Evidence rather than unrelated checkboxes.
