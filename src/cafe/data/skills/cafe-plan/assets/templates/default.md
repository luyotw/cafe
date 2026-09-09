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

### 📋 Development Task Breakdown

#### Task 1: Create service layer foundation
- [ ] **Dev 1.1**: Create `services/auth_service.py`, define `AuthService` class
- [ ] **Dev 1.2**: Create `services/models.py`, define `ServiceResult` dataclass
- [ ] **Dev 1.3**: Create `services/exceptions.py`, define custom exception classes
- [ ] **commit**: Write commit message strictly following existing commit message style
- [ ] **Update**: Check completed items, update md file if necessary

#### Task 2: Implement authentication business logic
- [ ] **Dev 2.1**: Implement `AuthService.authenticate()` method
- [ ] **Dev 2.2**: Move password validation logic to service layer
- [ ] **Dev 2.3**: Move JWT token generation logic to service layer
- [ ] **commit**: Write commit message strictly following existing commit message style
- [ ] **Update**: Check completed items, update md file if necessary

#### Task 3: Implement registration business logic
- [ ] **Dev 3.1**: Implement `AuthService.register_user()` method
- [ ] **Dev 3.2**: Move email and password validation logic
- [ ] **Dev 3.3**: Move user creation logic
- [ ] **commit**: Write commit message strictly following existing commit message style
- [ ] **Update**: Check completed items, update md file if necessary

#### Task 4: Refactor View layer
- [ ] **Dev 4.1**: Modify `login_view()` to call service layer methods
- [ ] **Dev 4.2**: Modify `register_view()` to call service layer methods
- [ ] **Dev 4.3**: Simplify view layer logic, keep only HTTP handling
- [ ] **commit**: Write commit message strictly following existing commit message style
- [ ] **Update**: Check completed items, update md file if necessary

#### Task 5: Write comprehensive tests
- [ ] **Dev 5.1**: Create `tests/unit/test_auth_service.py`
- [ ] **Dev 5.2**: Test `authenticate()` method (success/failure scenarios)
- [ ] **Dev 5.3**: Test `register_user()` method (various validation scenarios)
- [ ] **Dev 5.4**: Test edge cases and exception handling
- [ ] **Dev 5.5**: Run integration tests to ensure API behavior unchanged
- [ ] **commit**: Write commit message strictly following existing commit message style
- [ ] **Update**: Check completed items, update md file if necessary

#### Task 6: Definition of Done (DoD)
Copy the wording of the DoD items from the spec's Acceptance Criteria section (lines marked with `✅ **DoD:**`) and verify each one.
- [ ] **Dev 6.1**: Login and registration work correctly through the new service layer
- [ ] **Dev 6.2**: All error handling returns proper error codes and messages
- [ ] **Dev 6.3**: API response format is unchanged from before refactoring
