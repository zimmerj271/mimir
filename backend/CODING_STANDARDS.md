# Python Coding Standards — OOP-first, composition-first

**Intent:** readable, maintainable, extensible Python engineering consistent with the user's explicit preference for OOP over procedural modules and loose standalone functions. Apply this to backend production code. Keep tests and scripts idiomatic without allowing exceptions to become loopholes.

## Design rules (MUST)
1. **Organize production behavior into cohesive classes** with explicit responsibilities. Use instance methods for operations that belong to a service, repository, adapter, policy, validator, serializer, or coordinator. Avoid modules made up of standalone business-logic functions.
2. **Composition over inheritance.** Prefer constructor-injected collaborators implementing typed `Protocol` interfaces to deep class hierarchies or base-class frameworks. Inheritance is appropriate only for genuine substitutability with stable invariants, not code reuse alone.
3. **Encapsulate change points** (storage, inference, memory provider, transport, auth, clock, ID generation, token counting, retry policy). Use interfaces where there is an actual boundary or test seam; do **not** create one interface and factory for every trivial class.
4. **Single responsibility and small public APIs.** A class should have one coherent purpose; it should not mix HTTP handling, SQL, workflow coordination, and provider-specific code. Avoid `Manager` or `Utils` classes with unrelated responsibilities.
5. **Explicit dependency injection.** Construct components at the composition root; avoid hidden global service locators, singleton mutable state, runtime monkey-patching and implicit network calls in constructors.
6. **Strong typing.** Annotate public methods and data. Use dataclasses, enums, Pydantic boundary models and structured exceptions as appropriate. Use `Protocol` at module boundaries; avoid pervasive `Any`, unvalidated dictionaries, and untyped provider payloads outside adapters.
7. **Methods should be cohesive and testable.** Extract a collaborating class if one method takes multiple unrelated responsibilities, instead of moving logic into unrelated free functions.
8. **Design patterns only when justified.** Adapter for Mem0/Ollama, repository for persistence, strategy for pluggable policies, factory/composition root for configured providers, state-machine rules for runs/jobs. Document actual benefit; avoid abstract-factory/decorator pipelines or frameworks added speculatively.
9. **Deterministic core, side effects at edges.** Context assembly and policy checks should be pure in behavior even when implemented as instance methods. Inject clocks, ID generators, and token counters to keep tests deterministic.
10. **Error handling is explicit.** Use typed domain exceptions; do not catch broad exceptions except at clear boundaries with logging and normalization. Never mask failures, cross-user unauthorized access, or uncertain external side effects.
11. **Async discipline.** `async` where I/O occurs; never block FastAPI's event loop with synchronous SDK calls. Use bounded threading only behind adapters when needed. Avoid open DB transactions during inference or Mem0 calls.
12. **Database correctness.** SQL migration constraints are authoritative, ORM mappings match SQL. Unit tests cannot replace integration tests for RLS, transactions, locks, branches or leases.

## Standalone functions policy
- **Default: do not introduce module-level standalone production business-logic functions.** Favor focused class methods, static methods only when logically owned by that class, and injected service collaborators.
- **Allowed narrowly where required or clearly more idiomatic:** Python entry points (`main`), framework/lifecycle callbacks, decorators, simple pure formatting helpers closely associated with a module, and test functions/fixtures. Minimize these; name and justify exceptions in code review. Do not wrap simple computations in needless object graphs just to eliminate a function.
- Module-level constants, enums, type aliases, protocols and immutable data classes are welcome.
- Prefer `@staticmethod` **only for operations genuinely owned by a class**, not as a dumping ground for unrelated utilities.

## Example service decomposition
```python
class ConversationRepository(Protocol):
    async def get_owned(self, owner_id: UUID, conversation_id: UUID) -> Conversation: ...

class ConversationService:
    def __init__(self, repository: ConversationRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    async def get_conversation(self, owner_id: UUID, conversation_id: UUID) -> Conversation:
        return await self._repository.get_owned(owner_id, conversation_id)
```
The example illustrates dependency direction; it does not require extra abstraction if no second implementation or test seam is needed.

## Review checklist
- Does every new class have one coherent responsibility and a useful name?
- Is business logic implemented in appropriate methods, not a procedural `utils.py`?
- Are dependencies explicit, typed, replaceable at real boundaries and scoped correctly?
- Could a simpler composition achieve the same behavior without an abstract class hierarchy?
- Is failure behavior documented, with no hidden side effects or silent fallbacks?
- Are authorization/ownership and transactional boundaries tested?
- Are tests deterministic and do they cover meaningful edge cases?
- Is code small enough to review as one task without unrelated refactoring?
- Did the change avoid new architecture and dependencies not required by the task?

## Python quality tools (mandatory; version pin and configuration established in T01)
- Use `uv` for dependency/environment management and all project command execution; commit `uv.lock` and enforce locked sync. `ruff` formatter + linter; **mypy** strict; `pytest` + `pytest-asyncio` + coverage. Configure pinned versions in `pyproject.toml` during T01.
- CI must run the pinned commands. Never invent green results or bypass failing checks.
