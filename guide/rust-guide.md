# Rust Super Guide

30 October 2026 · @Consultor

Revision 1.3.0: adds M34 (human domain-owner verification of soundness-critical code and safety prose) and Section 15 (human-first governance for agent- and LLM-assisted changes, cross-model adversarial review, deterministic Clippy rules, red-green evidence and snapshot discipline). Revision 1.2.1 added stable MUST rule ids (M01 to M33) and corrected two lint behaviors after testing on Rust 1.97. Revision 1.2 merged technical revision 1.1 with the adoption guidance, MUST matrix and remote-operation rule of the previous edition.

## 1. Introduction

This guide is an engineering standard that a Rust project can adopt and adapt. It combines practical guidance on API design, implementation, security and review, drawing on Google's Rust Style Guide, Microsoft's Pragmatic Rust Guidelines, the Rust API Guidelines and the other references listed below.

The sources have different audiences and constraints. Combining their strictest sentences does not automatically produce a better standard. This revision distinguishes Rust's actual contracts from useful defaults and the policies of a particular organization.

### Scope and baseline

The default setting is an ordinary Cargo project using the standard library and a selected stable toolchain. Complete examples target **Rust 1.85.0 or newer and edition 2024**. Rust 1.85.0 introduced that edition; this is the examples' compatibility baseline, not a recommendation to deploy an old compiler or a claim about the latest release. A project must select and maintain its production toolchain separately. [Rust 2024 release baseline](https://doc.rust-lang.org/edition-guide/rust-2024/index.html)

Record the project's minimum supported Rust version (MSRV), edition, target platforms, feature combinations, panic strategy, and lint/format toolchains. Kernel, embedded, `no_std`, platform-integrated and safety-critical projects need an explicit profile for their environment. A library's API contract matters even when consumers use a different build profile or executor.

### Requirement levels

These levels express this guide's policy **within the selected project profile**. A source link does not mean that every cited organization uses the same requirement level.

| Level | Meaning |
| --- | --- |
| **MUST** | Required after adopting the relevant policy. A policy deviation needs a recorded reason and review. |
| **MUST NOT** | Prohibited by the relevant policy or contract. Any permitted policy exception must be explicit. |
| **SHOULD** | The expected default; choose another approach when its tradeoff is justified. |
| **SHOULD NOT** | Generally discouraged; a concrete benefit may justify an exception. |
| **MAY** | An optional technique or design choice. |

**Language and API safety contracts cannot be waived by review.** For example, a raw pointer must satisfy the full contract of the operation that dereferences it. A reviewer cannot approve undefined behavior as a style exception. [Rust Reference: undefined behavior and soundness](https://doc.rust-lang.org/reference/behavior-considered-undefined.html)

Rules labeled **PROJECT POLICY** apply to the named organization or codebase. Unlabeled recommendations are this guide's synthesis. Direct links explain the underlying behavior or provide the related source guidance; they are not claims of unanimous endorsement.

### Libraries and applications

Public library APIs need deliberate ownership, compatibility and error contracts. Application code benefits from the same reasoning, but should not acquire generic parameters, builders, crates or traits without a concrete need. Apply architecture advice in the context of actual consumers. [Google: applying API guidance in context](https://google.github.io/styleguide/rustguide.html#apply-api-design-rules-in-context)

### Priorities

Preserve correctness and safety first, then clarity and maintainability, then interoperability and project consistency. Measure relevant performance tradeoffs. Prefer a documented decision to a blanket rule whose assumptions do not hold.

### Adopting this guide in a project

This is a language-level reference, not a system's engineering contract. A project that adopts it should do three things:

1. Assign rules to crate profiles (pure domain logic, I/O adapters, FFI layers, binaries). A domain crate and a database adapter rarely need the same API rules.
2. Keep, for every MUST, its scope, how it is checked and how an exception is approved; the matrix after the golden rules is the starting point. A MUST that no tool enforces is a review rule and must be labeled that way, not presented as protected by CI.
3. Keep system-specific contracts (authorization, personal data, idempotency, provider cost limits, job cancellation, recovery) in the project's own architecture documents and ADRs. Where they conflict with this guide, they take precedence, so there is a single source of authority.

## Golden rules

This quick reference summarizes the detailed sections; their scope and exceptions still apply.

1. **MUST** automate formatting, linting and meaningful tests on the selected toolchains and supported configurations.
2. **MUST** explain local lint suppressions; **SHOULD** use `#[expect(..., reason = "...")]` when the diagnostic reliably fires.
3. **SHOULD** keep imports and public re-exports explicit; use limited globs and preludes deliberately.
4. **SHOULD** follow familiar naming conventions and describe the real ownership and cost of conversions.
5. **SHOULD** accept flexible borrowed views when only their contents are needed; take ownership when the operation requires it.
6. **SHOULD** use newtypes and enums when they prevent ambiguity or invalid states.
7. **SHOULD** implement useful standard traits; **MUST NOT** leak secrets through diagnostics.
8. **MUST** preserve a public API's promised invariants and compatibility; use `#[non_exhaustive]` with its construction and matching restrictions in mind.
9. **SHOULD** report expected failures with `Result` and meaningful absence with `Option`; **MUST NOT** use panics as routine error propagation.
10. **SHOULD** prefer matching or `?` to check-then-unwrap; use `expect` to explain an intentional invariant panic.
11. **SHOULD** make reusable error types informative and interoperable; promise `Send`, `Sync` and `'static` where the contract needs them.
12. **SHOULD** choose borrowing, cloning and shared ownership according to actual lifetimes and sharing semantics.
13. **MUST** support performance-motivated `unsafe` with measurements and a complete safety argument.
14. **MUST** document unsafe obligations and uphold them on ordinary, panic and cancellation paths; expose a safe API only when it is sound.
15. **MUST** define FFI validity, ownership and unwind contracts; a null check or `catch_unwind` cannot establish pointer validity.
16. **MUST NOT** rely on mutex poisoning for memory safety or block executor workers with unbounded work; review recovery and scheduling in context.
17. **SHOULD** prefer ordinary abstractions before macros and test exported expansions as consumers use them.
18. **MUST** document public library contracts and applicable `# Errors`, `# Panics` and `# Safety` conditions; execute documentation examples.
19. **MUST** review dependencies and check known advisories; advisory scans and audit records are different forms of evidence.
20. **MUST** define the supported toolchain and feature matrix; a test feature, a passing lint run or a safety guideline alone is not a deployment or soundness guarantee.

## MUST rules: scope, check and exception

Each MUST below has a stable id, names where it applies, what checks it and how an exception is handled. Ids do not change between revisions; a removed rule keeps its id retired. **Review only** means no tool enforces the rule: CI does not protect it, so reviewers check it explicitly. Tools marked as aids catch some violations, not all. Safety contracts have no review exception.

| ID | Rule | Scope | How it is checked | Exception |
| --- | --- | --- | --- | --- |
| M01 | Formatting checked in CI | All crates | `cargo fmt --all -- --check` on the recorded rustfmt version | `#[rustfmt::skip]` with a reason |
| M02 | Clippy clean in every supported configuration | All crates | `cargo clippy --workspace --all-targets --locked -- -D warnings`, one job per supported feature and target set | Narrow `#[expect]` or reasoned `#[allow]` |
| M03 | Local suppressions explained | All crates | `clippy::allow_attributes_without_reason`, which flags `#[allow]` and `#[expect]` without a reason (verified on Clippy 0.1.97; check `#[expect]` in review on older lint toolchains) | None |
| M04 | Toolchain, MSRV and feature matrix defined | Workspace | `rust-version` in each manifest plus an MSRV CI job; Clippy `incompatible_msrv` as an aid | None |
| M05 | Lint names valid for the lint toolchain | Workspace | Unknown names in source attributes fail under `-D warnings`; unknown names in Cargo `[lints]` tables only print warning E0602 and do not fail the build, so CI must fail on that warning explicitly | None |
| M06 | `missing_docs` in libraries | Library crates | rustc `missing_docs` | Narrow, explained `#[allow]` (for example terse enum variants) |
| M07 | Invariants preserved on every safe path | Types with invariants | Review only, plus tests of rejected inputs | None |
| M08 | Equality, hashing and ordering consistent | Types implementing `Eq`, `Hash` or `Ord` | Review; Clippy `derived_hash_with_manual_eq` as an aid | None |
| M09 | SemVer changes reviewed | Libraries promising SemVer | `cargo semver-checks` as an aid, plus review | Major version bump |
| M10 | No panics as routine error propagation | All crates | Review; Clippy `unwrap_used` and `expect_used` where adopted | Tests; documented invariant panics |
| M11 | `# Errors`, `# Panics`, `# Safety` documented | Public items | Clippy `missing_errors_doc`, `missing_panics_doc`, `missing_safety_doc` as aids, plus review | None for `# Safety` |
| M12 | `unsafe` justified; performance claims measured | Crates with `unsafe` | Review only, with the benchmark attached | None |
| M13 | Caller obligations exposed as `unsafe fn` or `unsafe trait` | Crates with `unsafe` | Review only; Miri on executed cases | None: soundness contract |
| M14 | `// SAFETY:` on unsafe blocks and `unsafe impl` | Crates with `unsafe` | Clippy `undocumented_unsafe_blocks`, plus review of each argument | None |
| M15 | `unsafe_op_in_unsafe_fn` denied | Crates with `unsafe` | rustc lint at `deny` | None |
| M16 | Full raw-slice contract | Code building slices from raw pointers | Review only; Miri on executed cases | None: soundness contract |
| M17 | Miri on novel unsafe abstractions | Novel `unsafe` abstractions and optimizations | `cargo +nightly miri test` on a pinned nightly | Unsupported cases recorded with an alternative check |
| M18 | FFI layout, validity, ownership and unwind policy | FFI crates | Review plus integration tests at the boundary | None: soundness contract |
| M19 | `#[unsafe(no_mangle)]` and `unsafe extern` blocks | Edition 2024 FFI code | The compiler rejects the bare forms | None |
| M20 | No reliance on poisoning for memory safety | Code with shared mutexes | Review only | None |
| M21 | No blocking or unbounded work on executor workers | Async code | Review; Clippy `await_holding_lock` catches one pattern | None |
| M22 | Memory safety when a future is dropped | Async code | Review plus tests that drop futures mid-operation | None: soundness contract |
| M23 | Remote operations keep an id and a reconciliation path | Code calling external or paid systems | Review plus integration tests that cancel mid-call | None for side-effecting calls |
| M24 | `$crate::` paths in exported `macro_rules!` | Crates exporting macros | Tests from a separate consumer crate | None |
| M25 | Test helpers out of the production API | All crates | `cfg(test)`; `cargo tree -e features` on release builds | None |
| M26 | Doctests executed | Library crates | `cargo test --workspace --doc --locked` | `no_run` for external environments |
| M27 | Overflow and shift behavior explicit | Correctness or security-relevant arithmetic | Review; overflow checks in dev and test; Clippy `arithmetic_side_effects` where adopted | Documented modular arithmetic |
| M28 | Lockfile committed, builds `--locked` | Deployed applications | CI uses `--locked` | None |
| M29 | New dependencies reviewed; advisories checked | Workspace | Review record for new direct dependencies; `cargo audit` or `cargo deny check` (advisories) in CI | Tracked advisory exception with reason and expiry |
| M30 | Unstable features justified | All crates | Stable toolchain in CI rejects `#![feature]` | The owning repository's policy |
| M31 | Automated fixes reviewed | All changes | Review only | None |
| M32 | Secret copies minimized; erasure reviewed | Code handling secrets | Review only | None |
| M33 | Critical systems: standard, revision and evidence identified | Safety-related projects | Process audit, not tooling | As defined by the adopted process |
| M34 | Soundness-critical code and safety prose verified by a human domain owner | Crates with `unsafe` or soundness-critical invariants | Clippy `undocumented_unsafe_blocks` / `missing_safety_doc`, Miri, CODEOWNERS (`policy.unsafe_owners`) and human domain review | None |

## 2. Code organization

Organize code around cohesive responsibilities and a deliberate public API. The layout and import rules below are project conventions, inspired by Google and Microsoft; Rust also supports other layouts.

### Crates and features

- **SHOULD** split a crate when a component has an independent purpose, dependency set or release boundary. Smaller crates can improve incremental builds and parallelism, but extra boundaries also add maintenance and compilation overhead; measure build-time claims in the actual workspace.
- **MAY** retain one crate when splitting would complicate implementation ownership, trait coherence or the public API.
- **SHOULD** make library features additive: enabling a feature should add functionality without disabling existing functionality. Prefer names such as `serde` and `std` over `use-serde` or `no-std`. Cargo normally unifies requested features, so one dependent cannot reliably turn off a feature enabled by another. [Cargo features](https://doc.rust-lang.org/cargo/reference/features.html#feature-unification)
- **SHOULD** keep unit-test-only helpers under `#[cfg(test)]`. Integration tests and other crates use the library as an ordinary dependency and cannot access those helpers; use a separate test-support crate or an explicit, non-default `test-util` feature when sharing is necessary. **MUST NOT** treat such a feature as a production exclusion or security boundary: users can enable it, including through `--all-features`. Test support must preserve safe API invariants. [Conditional compilation](https://doc.rust-lang.org/reference/conditional-compilation.html#test), [feature activation](https://doc.rust-lang.org/cargo/reference/features.html#command-line-feature-options)
- **SHOULD** review third-party re-exports and dependency types exposed in signatures as part of the public compatibility contract. Deliberate integration APIs, facade crates and proc-macro re-exports are valid uses; feature-gating an API does not remove its compatibility obligations. [Cargo SemVer guidance](https://doc.rust-lang.org/cargo/reference/semver.html)

### Modules and imports

- **SHOULD** name a focused leaf module after its main item (`Parser` in `parser.rs`) and keep routing modules mostly to declarations and re-exports. Group related small items together when that reads better.
- **SHOULD** expose selected items through explicit re-exports and use semantic public modules when they help users navigate the API. Flattening every API into the crate root is optional.
- **SHOULD** use explicit imports and re-exports. Limited globs are reasonable for test modules, local enum matching and documented preludes. A prelude should be small, intentional and stable.
- **SHOULD** qualify ambiguous names and free functions when context helps (`io::Error`, `mem::swap`); import distinctive types directly. Scope trait imports locally when that improves clarity. A consistent one-module-per-`use` style is optional. These choices adapt [Google's organization rules](https://google.github.io/styleguide/rustguide.html#code-organization) rather than defining Rust syntax requirements.

### Visibility

- **SHOULD** keep representation fields private when the type enforces invariants; expose only useful operations. Public fields suit passive data and configuration, provided arbitrary field values remain valid. [API future proofing](https://rust-lang.github.io/api-guidelines/future-proofing.html#structs-have-private-fields-c-struct-private)
- **SHOULD** use private, `pub(super)` or `pub(crate)` visibility for implementation details. A `pub` item inside an inaccessible private module is not automatically a downstream API; re-exports and reachability matter. [Rust visibility](https://doc.rust-lang.org/reference/visibility-and-privacy.html)
- **SHOULD NOT** depend on another crate's undocumented internals, including `#[doc(hidden)]` items, unless its maintainers explicitly support that use. Hiding documentation does not itself change visibility or define a compatibility guarantee.

## 3. Formatting and lints

Automate formatting and routine lint checks; review still decides whether a suggested change preserves meaning and improves the code.

### rustfmt and toolchains

- **MUST**, as this guide's CI policy, run `cargo fmt --all -- --check` with the project's chosen formatter version. Record that version so contributors and CI produce the same output.
- **SHOULD** use stable rustfmt defaults, with explicit settings only where the project needs them. For the edition-2024 baseline, the following is sufficient:

```toml
# rustfmt.toml
edition = "2024"
style_edition = "2024"
max_width = 100
```

- **MAY** use additional formatter options after checking their stability in the selected rustfmt release. Options such as `imports_granularity`, `hex_literal_case` and `format_macro_matchers` have required nightly rustfmt; do not assume that a configuration copied from another project works with stable. A stable build compiler and a separately pinned nightly formatter can coexist. Consult the [versioned rustfmt configuration reference](https://rust-lang.github.io/rustfmt/).
- **MAY** use `#[rustfmt::skip]` narrowly for tables, generated output or syntax that the formatter handles poorly, with a reason. Prefer generating reproducibly formatted output; requiring every consumer to run a generator at build time can impose unnecessary tools and dependencies. [Microsoft's library build guidance](https://microsoft.github.io/rust-guidelines/guidelines/libs/building/index.html)

### Clippy and suppressions

- **MUST**, as this guide's CI policy, run `cargo clippy --workspace --all-targets -- -D warnings` on a controlled lint toolchain. Add jobs for the supported feature and target configurations: `--all-targets` covers Cargo targets, not every architecture or feature combination. Use `--all-features` only when that configuration is supported. The command remains usable with the Rust 1.85 baseline; newer Cargo versions also offer other warning controls. [Clippy usage](https://doc.rust-lang.org/clippy/usage.html)
- **SHOULD** keep shared lint choices in `Cargo.toml`. Workspace settings use `[workspace.lints]`; each member opts in with `[lints]` followed by `workspace = true`. [Cargo workspace inheritance](https://doc.rust-lang.org/cargo/reference/workspaces.html#the-lints-table)
- **SHOULD** prefer a narrow `#[expect(lint, reason = "...")]` when the lint reliably fires. An unfulfilled expectation warns, helping identify stale suppressions. Use a reasoned `#[allow]` when configurations, generated code or macro expansions make an expectation unreliable. Do not replace global configuration choices mechanically with expectations. [Diagnostic attributes](https://doc.rust-lang.org/reference/attributes/diagnostics.html#lint-check-attributes)
- **MUST** explain local suppressions. `clippy::allow_attributes_without_reason` flags `#[allow]` and `#[expect]` attributes that lack a reason on current Clippy (verified on 0.1.97); on older lint toolchains, check `#[expect]` reasons in review.

### Small lint baseline

This is an adopted starting policy, not the complete Microsoft or S-CORE profile. Clippy already enables its default groups; retaining their defaults also retains deny-by-default correctness checks.

```toml
[lints.rust]
unsafe_op_in_unsafe_fn = "deny"

[lints.clippy]
undocumented_unsafe_blocks = "warn"
allow_attributes_without_reason = "warn"
```

For libraries, **MUST** enable `missing_docs = "warn"` under `[lints.rust]`, as required by section 11; consider adding `missing_debug_implementations = "warn"` as well. Review `pedantic`, `cargo`, selected restriction lints such as `unwrap_used`, and individual nursery lints separately. Do not enable the whole `restriction` group; some restrictions conflict. Broad `pedantic` and `nursery` adoption needs project-specific exceptions rather than a universal mandate. [Clippy lint groups](https://doc.rust-lang.org/clippy/lints.html)

**MUST** verify new lint names against the compiler and Clippy versions that will run them. An unknown name in a source attribute triggers `unknown_lints`, which `-D warnings` turns into an error. An unknown name in a Cargo `[lints]` table only prints warning E0602, and the build still succeeds under `-D warnings` (verified on Rust 1.97), so CI must fail on that warning explicitly. A library's minimum build compiler and its lint/format toolchain may differ; record both policies. [Lint reference](https://clippy.rust-lang.org/master/index.html)

## 4. Naming

Follow ecosystem conventions, while preserving established public names when compatibility requires it.

### Casing and clarity

- **SHOULD** use `UpperCamelCase` for types, traits and variants; `snake_case` for modules, functions, methods, variables and macros; and `SCREAMING_SNAKE_CASE` for constants and statics. Treat acronyms as words (`Uuid`, `HttpClient`, `is_xid_start`). [RFC 430](https://rust-lang.github.io/rfcs/0430-finalizing-naming-conventions.html)
- **SHOULD** avoid redundant `rust`/`rs` affixes in crate names and unexplained project abbreviations. Prefer names that identify a responsibility; words such as `Manager` are acceptable when they convey an actual role.

### Conversions and accessors

Use the following conventions for ad hoc conversions; they describe intent, not a complete performance contract:

| Prefix | Typical meaning | Example |
| --- | --- | --- |
| `as_` | Cheap borrowed view | `str::as_bytes` |
| `to_` | Produces a converted value without consuming a non-`Copy` source; may perform work, validate a borrowed view or take a `Copy` value by value | `str::to_lowercase`, `Path::to_str`, `f64::to_radians` |
| `into_` | Consumes the source; cost varies | `String::into_bytes` |

**SHOULD** use `into_inner` to extract a wrapper's underlying value. Ordinary getters usually omit `get_` (`name`, `name_mut`), but lookup methods and established APIs may use it. `wrong_self_convention` checks some receiver patterns; it cannot verify conversion cost. [API naming conventions](https://rust-lang.github.io/api-guidelines/naming.html), [Clippy's receiver checks](https://clippy.rust-lang.org/master/index.html#wrong_self_convention)

- **SHOULD** provide `is_empty` with collection-like `len`, unless emptiness cannot occur or the operation has another meaning.
- **SHOULD** use the familiar `iter`, `iter_mut` and `IntoIterator` families where the collection supports those operations. Do not invent mutable iteration for an immutable collection merely for symmetry.
- **MAY** use `set_foo`, `take_foo` and `replace_foo` for their familiar update semantics. Document whether accessors return references, copied values, guards or computed data.

### Constructors

- **SHOULD** use inherent associated functions such as `new`, `with_capacity` or a descriptive operation name. Implement `Default` alongside a no-argument `new` when both mean the same sensible default. Do not create a meaningless default to satisfy a naming rule. [Common trait conventions](https://rust-lang.github.io/api-guidelines/interoperability.html#types-eagerly-implement-common-traits-c-common-traits)
- **MAY** use `try_new` to distinguish a fallible constructor, especially when paired with an infallible one. Requiring it for every `Result` constructor is a [Google convention](https://google.github.io/styleguide/rustguide.html#naming-initialization-functions), not a Rust-wide rule; standard APIs also use fallible names such as [`CString::new`](https://doc.rust-lang.org/std/ffi/struct.CString.html#method.new).
- **SHOULD** implement `From` for appropriate infallible conversions and `TryFrom` for fallible conversions. Add an explicit conversion constructor only when it improves discoverability or expresses a distinct operation.

### Generics, lifetimes and extension traits

- **SHOULD** use concise generic names (`T`, `R: Read`) and meaningful lifetime names (`'input`, `'de`) when they clarify relationships. Use `'a` for simple relationships and elision or `'_` where that is clearer. There is no useful universal maximum number of lifetime parameters.
- **SHOULD** suffix extension traits with `Ext`. Defining an extension trait in a downstream crate is legitimate when adding operations to a foreign type; ownership by the original type's team is an organizational preference.

## 5. API design

Design for the caller's ownership needs, meaningful types and a public contract you can maintain. Extra abstraction must solve an actual problem in both libraries and applications.

### Types and invariants

- **SHOULD** use a newtype to distinguish units, identifiers or validated data when accidental substitution matters. A type alias improves naming but does not create a distinct type. Prefer named fields when tuple positions obscure meaning. [API type safety](https://rust-lang.github.io/api-guidelines/type-safety.html)
- **MUST** preserve a type's invariants through every safe construction and mutation path. Fallible input validation can use `Result`, `Option` or `TryFrom`; input already guaranteed valid can have an infallible constructor. Keep fields private when arbitrary values would violate the invariant.
- **SHOULD** use an enum for a boolean argument whose meaning is unclear at the call site, and group related parameters when a long argument list becomes hard to use. A fixed parameter-count threshold is a review heuristic.
- **SHOULD** represent independently combinable flags with a flag-set type, often using `bitflags`; a plain enum represents alternatives, not arbitrary combinations. A dependency on that particular crate is optional. [Flag-set guidance](https://rust-lang.github.io/api-guidelines/type-safety.html#types-for-a-set-of-flags-are-bitflags-not-enums-c-bitflag)

The argument names and types make the overwrite decision explicit:

```rust
//! File output with an explicit overwrite policy.

use std::fs::OpenOptions;
use std::io::{self, Write};
use std::path::Path;

/// Whether saving may replace the contents of an existing file.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Overwrite {
    /// Create the file if absent, or truncate it if present.
    Allow,
    /// Create a new file, failing if the path already exists.
    Forbid,
}

/// Writes `data` to `path` according to the selected overwrite policy.
///
/// # Errors
///
/// Returns an I/O error if opening or writing fails, including when the file
/// already exists and `overwrite` is `Overwrite::Forbid`. A write failure may
/// leave a partially written file.
pub fn save(path: &Path, data: &[u8], overwrite: Overwrite) -> io::Result<()> {
    let mut options = OpenOptions::new();
    options.write(true);
    match overwrite {
        Overwrite::Allow => options.create(true).truncate(true),
        Overwrite::Forbid => options.create_new(true),
    };
    options.open(path)?.write_all(data)
}
```

This example demonstrates parameter design and ordinary I/O error propagation; it does not promise atomic file replacement.

### Parameters and return values

- **SHOULD** accept `&str`, `&[T]`, `&T` or `Option<&T>` when only the borrowed contents are needed. References to owning containers are appropriate when the container itself matters, including capacity inspection, pointer identity or matching a trait's required signature. Mutable operations that grow a collection may require `&mut Vec<T>` or `&mut String`.
- **SHOULD** take ownership when storing or consuming a value, rather than borrowing it only to clone it internally. [Caller control](https://rust-lang.github.io/api-guidelines/flexibility.html#caller-decides-where-to-copy-and-place-data-c-caller-control)
- **MAY** use `AsRef<Path>`, `AsRef<str>`, `Into<T>` or `IntoIterator` when accepting multiple input forms materially helps callers. A concrete borrow is often simpler; consider inference, diagnostics, monomorphization and the actual access pattern. Applications may benefit from these traits too.
- **SHOULD** accept `Read`/`Write` implementations by value when flexible ownership is useful: callers can pass an owned stream or a mutable reference. Add only the bounds the operation needs. [Generic inputs](https://rust-lang.github.io/api-guidelines/flexibility.html)
- **SHOULD** return an iterator when incremental traversal is the useful contract, or an owned collection when ownership, indexing or materialization is the useful result. Document iterator lifetimes and capabilities on which callers can rely.
- **SHOULD** return an informative error type when failure needs explanation or differentiated handling. `Result<T, ()>` is legal and occasionally sufficient, but usually underspecifies a reusable public API.

### Standard traits and derive

- **SHOULD** provide useful `Debug` for public types, with conditional implementations for generic types where appropriate. **MUST NOT** expose secrets through derived or manual diagnostics; redact or omit sensitive data. A justified omission is preferable to an unsafe disclosure. Derived output is not a stable serialization format. [`Debug` documentation](https://doc.rust-lang.org/std/fmt/trait.Debug.html)
- **SHOULD** implement applicable common traits, including `Clone`, `Copy`, equality, ordering, `Hash`, `Default` and `Display`, where their semantics are valid. Derive when field-wise behavior matches the contract; manual implementations can avoid unnecessary generic bounds. Implementing a trait is itself an API commitment. [Interoperability](https://rust-lang.github.io/api-guidelines/interoperability.html)
- **MUST** keep equality and hashing consistent: equal values hash equally for the same hasher. Keep ordering consistent with equality when implementing both. [`Hash` contract](https://doc.rust-lang.org/std/hash/trait.Hash.html#hash-and-eq)
- **SHOULD** implement `From`/`TryFrom` rather than `Into`/`TryInto`; blanket implementations supply the latter. `From` must be infallible and should represent an obvious, value-preserving conversion. Use `AsRef`/`AsMut`, `FromStr`, `FromIterator` and `Extend` where the corresponding operation fits. [`From` documentation](https://doc.rust-lang.org/std/convert/trait.From.html)
- **SHOULD** place bounds on the implementations or methods that need them; retain bounds on a type definition when they are required to express or validate that type.

### Traits, generics and `dyn`

- **SHOULD** define a trait for shared behavior, caller-provided implementations, standard interoperability, marker contracts or extension methods. Use inherent methods for operations belonging to one concrete type; a trait need not exist solely because a type has methods.
- **SHOULD** choose concrete types, generics or `dyn Trait` according to the required extensibility and dispatch. Generics support static dispatch and inlining; `dyn` supports runtime selection, heterogeneous collections and reduced monomorphization. Neither is a universal last resort. Keep traits dyn-compatible when trait objects are part of the intended API. [Trait-object rules](https://doc.rust-lang.org/reference/items/traits.html#dyn-compatibility)
- **MAY** provide inherent methods alongside trait methods for discoverability, while keeping semantics consistent.
- **SHOULD** give operators unsurprising semantics. Implement `Deref` only for transparent, pointer-like access where implicit coercion and method lookup are intentional; do not use it to simulate inheritance or bypass a newtype's invariants. [`Deref` guidance](https://doc.rust-lang.org/std/ops/trait.Deref.html#when-to-implement-deref-or-derefmut)

### Builders and options structs

- **SHOULD** use a builder or options struct when configuration combinations make constructors unwieldy. Builder methods can validate individual values immediately or defer validation; `build() -> Result<_, _>` is appropriate for unresolved invalid states and cross-field constraints. An infallible `build() -> T` is appropriate when validity is already guaranteed. [Builder tradeoffs](https://rust-lang.github.io/api-guidelines/type-safety.html#builders-enable-construction-of-complex-values-c-builder)
- **SHOULD** provide sensible defaults for options where possible. For an ordinary public-field struct, callers can use struct update syntax. For a `#[non_exhaustive]` struct, external callers cannot use a struct literal, **including** `Options { field: value, ..Default::default() }`; provide a constructor/default followed by field assignment or builder methods. [Reference: external construction restrictions](https://doc.rust-lang.org/reference/attributes/type_system.html#the-non_exhaustive-attribute)

### Compatible evolution

- **MAY** use `#[non_exhaustive]` before first release when adding variants or fields is likely. Weigh that flexibility against external exhaustive matching and struct construction. Adding the attribute to an existing exhaustive public type can itself break callers.
- **SHOULD** seal a trait when downstream implementations are deliberately unsupported, and document that choice. Sealing helps evolve implementation requirements; removing or changing a public callable method can still break users. [Sealed traits](https://rust-lang.github.io/api-guidelines/future-proofing.html#sealed-traits-protect-against-downstream-implementations-c-sealed)
- **SHOULD** apply `#[must_use]` when silently ignoring a value is likely a bug, especially for lazy operations. `Result` already carries it; extra function annotations should add a useful contract or message. [Diagnostic attribute semantics](https://doc.rust-lang.org/reference/attributes/diagnostics.html#the-must_use-attribute)
- **SHOULD** consider `const fn` a lasting API promise: removing const-callability can break callers.
- **MUST**, for a library promising SemVer compatibility, review changes to reachable items, signatures, generic bounds, trait implementations, auto traits such as `Send`/`Sync`, and behavior. Many such changes break callers, but details and exceptions matter. Use automated compatibility checks as an aid to review, not proof that every downstream program remains compatible. [Cargo's compatibility reference](https://doc.rust-lang.org/cargo/reference/semver.html)

## 6. Errors and panics

Use `Result` for expected failure and `Option` for meaningful absence. Reserve panics for violated invariants or explicitly documented panic-based APIs. Whether a bug should terminate a process, a worker or a request is an application policy; the panic strategy and execution environment determine what actually happens.

### Option, Result or panic

- **SHOULD** use `Option<T>` when absence is an ordinary outcome and the caller needs no separate error explanation. A lookup or a parser whose contract deliberately reports only success or absence can legitimately return `Option`.
- **SHOULD** use `Result<T, E>` for expected failures that callers need to distinguish, report or recover from, including most parsing, I/O and external-data validation. Do not panic merely because external input is malformed.
- **MUST NOT** use panics as the routine error propagation mechanism. A downstream application may select `panic = "abort"`, so catching a panic cannot be part of a reusable library's ordinary success or error contract.
- **SHOULD** prevent invalid states through types and constructors. When a panic is appropriate for a detected invariant violation, explain the invariant and document the public API's panic conditions.
- **PROJECT POLICY (Microsoft):** its guidelines require panicking on detected, unrecoverable programming bugs rather than returning an error that callers cannot act upon. This is a design policy, not a language rule requiring every invalid argument to panic. *Sources: [Microsoft correctness guidelines](https://microsoft.github.io/rust-guidelines/guidelines/correctness/index.html#m-panic-on-bug), [Rust Book: choosing whether to panic](https://doc.rust-lang.org/book/ch09-03-to-panic-or-not-to-panic.html).*

```rust
struct Config {
    port: Option<u16>,
}

fn main() {
    let cfg = Config { port: Some(8080) };
    // A single pattern match expresses both the check and the extraction.
    if let Some(port) = cfg.port {
        println!("configured port: {port}");
    }
}
```

### unwrap, expect and assertions

- **SHOULD** prefer `expect("why the invariant should hold")` to `unwrap()` when an intentional panic needs explanation. Prefer matching or `?` when absence or failure is expected.
- **MAY** use `unwrap` and `expect` in tests when failure should fail the test. They should still leave a useful diagnostic, and tests of error behavior should inspect the expected error.
- **SHOULD** give deliberate panics and assertions messages that explain the violated invariant or API misuse. Include relevant values when useful, while respecting the application's rules for sensitive data.
- **MUST** document relevant panic conditions of public functions in `# Panics`; use `# Errors` to describe error results. *Sources: [Rust API Guidelines: failure documentation](https://rust-lang.github.io/api-guidelines/documentation.html#function-docs-include-error-panic-and-safety-considerations-c-failure), [Google implementation guidance](https://google.github.io/styleguide/rustguide.html#implementation-concerns).*

```rust
use std::collections::HashMap;

fn main() {
    let buf = [10, 20, 30];
    let idx = 1;
    assert!(
        idx < buf.len(),
        "index {idx} is outside a buffer containing {} bytes",
        buf.len(),
    );

    let id = 7;
    let map = HashMap::from([(id, "registered")]);
    let value = map
        .get(&id)
        .expect("initialization registers id 7 before lookup");
    println!("{}: {value}", buf[idx]);
}
```

### catch_unwind

- **MUST NOT** treat `std::panic::catch_unwind` as general exception handling or as protection against undefined behavior. It catches unwinding Rust panics; it cannot catch an aborting panic or make an invalid pointer safe.
- **MAY** catch an unwinding panic at a carefully designed isolation or FFI boundary. Before continuing, establish that protected state remains valid or is repaired/discarded. `UnwindSafe` is an advisory check; applying `AssertUnwindSafe` requires reasoning about the captured state.
- **MUST** account for cleanup as well as the protected call: a caught panic payload can panic when dropped, and a destructor panicking during unwinding can abort the process. A panic hook runs before the catch. Design boundary handlers accordingly.
- **PROJECT POLICY (Google):** first-party code prohibits `catch_unwind`, with a narrow defense-in-depth exception that cannot be relied on for system properties. Check the target codebase's panic configuration and current rules. *Sources: [`catch_unwind`](https://doc.rust-lang.org/std/panic/fn.catch_unwind.html), [Google catch policy](https://google.github.io/styleguide/rustguide.html#dont-call-paniccatch_unwind).*

### Error types

- **SHOULD** make public error types implement `std::error::Error`, which requires `Debug` and `Display`. Preserve an underlying error through `source()` when it is a meaningful cause; errors without a cause can use the default `None`.
- **SHOULD** provide `Send + Sync + 'static` errors when the intended API needs thread transfer, shared error reporting or downcasting. These are useful interoperability properties, not requirements of the `Error` trait. Borrowed errors and local-only errors can be valid when their restrictions are deliberate.
- **SHOULD** carry structured information callers can use and add context without losing the underlying cause. Choose a public enum, a non-exhaustive enum, or a struct with a private representation according to the promised matching and compatibility behavior.
- **SHOULD** implement `From` for suitable infallible conversions and let `?` apply them. Use `map_err` for contextual or operation-specific conversion; it is not limited to foreign types.
- **MAY** use an error-derivation crate for typed library errors and an application error-reporting crate at application boundaries. Neither a dependency nor a single error type for the entire application is mandatory.
- **SHOULD** use a `Result` alias only when it retains the familiar meaning of `std::result::Result<T, E>`. **MAY** make an error `Copy` if all fields support it and duplication suits the error's semantics. *Sources: [Rust API Guidelines: errors](https://rust-lang.github.io/api-guidelines/interoperability.html#c-good-err), [`Error`](https://doc.rust-lang.org/std/error/trait.Error.html), [`From`](https://doc.rust-lang.org/std/convert/trait.From.html).*

## 7. Ownership, borrowing and performance

Prefer clear ownership and borrowing. Shared ownership and cloning are legitimate design tools; choose them deliberately and measure changes whose justification is performance.

### Ownership and borrowing

- **SHOULD** start with unique ownership and borrows when they express the data's lifetime clearly. Use `Rc` for shared ownership confined to one thread and `Arc` when shared ownership must support threads or a surrounding API requires it. Graphs may instead use arena indices or carefully designed weak references.
- **SHOULD** prefer `Rc`/`RefCell` over their synchronized counterparts for an intentionally local design, when the required operations fit. `Arc` and `Mutex` can still be reasonable in presently single-threaded code for interoperability or a documented concurrency contract.
- **SHOULD** investigate unnecessary clones, especially of large collections and strings. A clone can also be the clearest way to establish independent ownership. `Clone` does not promise a deep copy: cloning `Rc` or `Arc` retains shared ownership, and user-defined implementations may have other costs or behavior.
- **SHOULD** expose ownership types in public APIs when their semantics are intentional. `Box<dyn Trait>`, `Arc<T>` and `Rc<T>` are valid API choices. A wrapper around `Arc<Inner>` can hide sharing details, but it also needs clear cloning and mutation semantics. *Sources: [`Clone`](https://doc.rust-lang.org/std/clone/trait.Clone.html), [`Arc`](https://doc.rust-lang.org/std/sync/struct.Arc.html), [`Box`](https://doc.rust-lang.org/std/boxed/index.html).*

### Inference, iterators and strings

- **SHOULD** add type annotations when inference obscures meaning, especially around conversions, parsing and long iterator chains. Choose a variable annotation or turbofish according to readability.
- **SHOULD** use short combinator chains when they state the operation clearly, and a `for` loop when control flow or side effects are easier to follow. Naming a complex iterator before a loop can help; neither iterator expressions in loop headers nor `for_each` are generally forbidden Rust.
- **SHOULD** use `format!` for readable formatted construction and `write!` when appending to an existing buffer. Simple concatenation is also valid; choose based on clarity and measured allocation costs.
- **SHOULD** reuse buffers on measured hot paths when doing so fits the caller's needs. `clear()` retains a `String`'s capacity, but writing a larger result can still allocate. *Sources: [`String`](https://doc.rust-lang.org/std/string/struct.String.html), [Microsoft allocation reuse](https://microsoft.github.io/rust-guidelines/guidelines/performance/index.html#m-mem-reuse).*

```rust
use std::fmt::Write as _;

fn append_record(out: &mut String, id: u64, name: &str) -> std::fmt::Result {
    out.clear();
    write!(out, "{id}:{name}")
}

fn main() -> std::fmt::Result {
    let mut out = String::with_capacity(64);
    append_record(&mut out, 7, "Ada")?;
    println!("{out}");
    append_record(&mut out, 8, "Grace")?;
    println!("{out}");
    Ok(())
}
```

### Allocation and hot paths

- **SHOULD** justify non-trivial optimizations with representative measurements and retain a readable baseline. **MUST**, under this guide's review policy, benchmark a performance-based justification for adding `unsafe`; speed does not relax soundness requirements.
- **SHOULD** identify important CPU and allocation costs early for performance-sensitive crates. Choose benchmarks and profilers appropriate to the workload; `criterion` and `divan` are examples, not requirements.
- **SHOULD** use `Vec::with_capacity` or `reserve` when a useful bound is known. Avoid blindly reserving an untrusted claimed length. `collect()` and a `push` loop are both valid; iterator size information may already allow `collect()` to reserve effectively.
- **SHOULD** offer reusable output buffers for high-volume APIs when callers benefit, and document surprising allocations or resource costs.
- **MAY** use `Box<[T]>` or `Arc<str>` for suitable immutable data. Select hashers using both performance and collision-resistance requirements; replacing a randomized hasher changes the threat assumptions.
- **MAY** select a different global allocator in an application after measurement. A reusable library should not impose a process-wide allocator policy on its consumers. *Sources: [Microsoft performance guidelines](https://microsoft.github.io/rust-guidelines/guidelines/performance/index.html), [`Vec`](https://doc.rust-lang.org/std/vec/struct.Vec.html), [`HashMap`](https://doc.rust-lang.org/std/collections/struct.HashMap.html), [`global_allocator`](https://doc.rust-lang.org/std/alloc/index.html#the-global_allocator-attribute).*

## 8. Unsafe and FFI

`unsafe` transfers specific proof obligations to the programmer. Keep the trusted implementation small, explain those obligations, and offer a safe interface only when every safe caller is prevented from violating them.

### When it is allowed

- **SHOULD** prefer established safe implementations. **MUST**, under this guide's review policy, record why `unsafe` is necessary and why alternatives are unsuitable; performance justifications need measurements.
- **PROJECT POLICY (Microsoft):** accepted reasons include novel abstractions, measured performance work, and FFI/platform calls. Its prohibition on ad hoc workarounds does not prohibit a properly designed and justified `unsafe impl Send` or lifetime-managing abstraction.
- **SHOULD** use `#![forbid(unsafe_code)]` in crates that do not require unsafe constructs. Keep necessary unsafe code in a focused module or crate with limited responsibilities.
- **MUST** use `unsafe fn` or `unsafe trait` for caller or implementer obligations needed to prevent undefined behavior. The marker is not a warning for ordinary destructive actions such as deleting a database. *Sources: [Microsoft correctness guidelines](https://microsoft.github.io/rust-guidelines/guidelines/correctness/index.html), [Rust Reference: unsafe](https://doc.rust-lang.org/reference/unsafe-keyword.html).*

### Encapsulation and soundness

- **MUST** ensure safe callers cannot cause undefined behavior by violating hidden preconditions. If an API must trust caller-provided pointer validity or another uncheckable safety condition, expose that obligation through an `unsafe` interface.
- **MUST** audit the code that maintains each invariant, including safe code with access to private fields, callbacks and generic trait implementations. Module privacy helps establish the boundary; an `unsafe` block alone is not the whole trusted implementation.
- **MUST** preserve memory safety on unwinding paths. Safe trait implementations and callbacks may panic or behave unexpectedly within their allowed contracts; do not rely on them for undocumented safety guarantees.
- **MUST**, under this guide's validation policy, run tests for novel unsafe abstractions and unsafe performance optimizations under Miri where supported, including adversarial callbacks, `Clone` and `Drop`. Document unsupported cases and use appropriate alternative checks. Combine this with review and integration testing. Miri checks executed cases, has foreign-function/platform limitations, and passing it is not proof of soundness. *Sources: [Rust Reference: soundness](https://doc.rust-lang.org/reference/behavior-considered-undefined.html), [Miri documentation](https://github.com/rust-lang/miri).*

### Comments and documentation

- **MUST**, under this guide's review policy, put a `// SAFETY:` explanation by each unsafe operation/block and each `unsafe impl`, relating its obligations to facts established by the implementation.
- **MUST** give public `unsafe fn` and `unsafe trait` items a `# Safety` section describing the caller's or implementer's obligations. Document private unsafe contracts too when they are not evident locally.
- **MUST**, under this guide's lint baseline, deny `unsafe_op_in_unsafe_fn` and warn on `clippy::undocumented_unsafe_blocks`. Rust 2024 only warns on the former by default; this guide intentionally strengthens it. Explicit blocks keep local proof obligations visible even inside an `unsafe fn`.
- **SHOULD** keep unsafe behavior apparent at call sites. A macro that requires caller obligations should not disguise an unsafe operation as an unrestricted safe API.
- **MUST**, when constructing a borrowed slice from a raw pointer, satisfy the complete contract: appropriate alignment and non-nullness even for an empty slice, initialized elements in one allocation, readable storage for the borrow, no prohibited mutation while the shared borrow lives, and a total byte size no greater than `isize::MAX` with no address-space wrap. A null check establishes only one of these facts. Avoid returning an arbitrarily inferred lifetime disconnected from the owner. *Sources: [`slice::from_raw_parts`](https://doc.rust-lang.org/std/slice/fn.from_raw_parts.html), [Rust 2024 unsafe operations](https://doc.rust-lang.org/edition-guide/rust-2024/unsafe-op-in-unsafe-fn.html), [Rust API safety documentation](https://rust-lang.github.io/api-guidelines/documentation.html#c-failure).*

### FFI

- **MUST** match the target's calling convention, type layout and value-validity requirements. `#[repr(C)]` is a layout tool, not a complete FFI guarantee; it does not change nested field representations or make invalid values valid. Keep Rust-native collections, slices and trait objects out of an ordinary C ABI unless an explicit interop protocol supports them.
- **SHOULD** use C aliases such as `core::ffi::c_int` when the foreign declaration uses `int`, and fixed-width Rust integers when the protocol specifies those widths. Do not assume that C's `long`, `char` or enum representation has one portable width or signedness.
- **MUST** prevent invalid Rust values from being formed. For untrusted integer tags or Boolean encodings, receive an integer and validate/convert it before creating a Rust enum or `bool`. Direct ABI-compatible Boolean or enum parameters require a foreign caller contract that guarantees valid values; checking them after invalid Rust values have entered is too late.
- **MUST** document pointer lifetimes, lengths, nullability, ownership, aliasing, threading and callbacks. Bounds/null checks cannot prove arbitrary foreign memory is readable. Define who allocates and frees resources, use compatible allocation/deallocation pairs, and provide appropriate release functions. Use RAII wrappers for owned foreign resources, while documenting explicit fallible close operations when needed.
- **MUST** choose an unwind policy for every boundary. Ordinary `extern "C"` does not permit unwinding across it: an escaping unwinding Rust panic aborts; an entering foreign exception is undefined behavior. An intentional cross-language unwind design requires the appropriate ABI, such as `"C-unwind"`, and compatible unwinding behavior throughout. `catch_unwind` is not a general catcher for foreign exceptions.
- **MUST** use `#[unsafe(no_mangle)]` for unmangled exports in edition 2024 and justify symbol uniqueness. Foreign declarations use `unsafe extern "C" { ... }` in edition 2024; this declares the signatures' correctness and is distinct from defining an `unsafe extern "C" fn` with caller obligations.
- **SHOULD** separate foreign bindings and ABI adaptation from domain logic. Separate `-sys`/export crates and binding generators can help, but crate names and an absolute ban on raw pointers in logic crates are architectural choices. Review generated bindings and their contracts. *Sources: [Rust Reference: layout](https://doc.rust-lang.org/reference/type-layout.html), [Rust Reference: invalid values](https://doc.rust-lang.org/reference/behavior-considered-undefined.html), [Rustonomicon: FFI and unwinding](https://doc.rust-lang.org/nomicon/ffi.html#ffi-and-unwinding), [Rust 2024 unsafe attributes](https://doc.rust-lang.org/edition-guide/rust-2024/unsafe-attributes.html), [Rust 2024 extern blocks](https://doc.rust-lang.org/edition-guide/rust-2024/unsafe-extern.html).*

The following standalone library example uses edition 2024 syntax available in Rust 1.85. It returns ordinary validation failures as status codes and deliberately aborts on an unexpected unwinding panic. The borrowed input never escapes the call.

```rust
//! A C ABI entry point for an ASCII byte buffer.
#![deny(unsafe_op_in_unsafe_fn)]

use core::ffi::c_int;

fn process(input: &[u8]) -> c_int {
    if input.is_ascii() { 0 } else { -2 }
}

/// Processes ASCII bytes: 0 = success, -1 = null/nonempty or oversized input,
/// -2 = non-ASCII content. A panic aborts the process.
/// A zero length permits any pointer, including null; it is not dereferenced.
///
/// # Safety
/// For nonzero `len` with a non-null `data` and `len <= isize::MAX`,
/// `data` must point to `len` initialized consecutive `u8` values in one
/// allocation, with provenance permitting reads of the entire range.
/// The range must not wrap the address space and must remain readable and
/// unmodified by any thread or foreign code until this call returns.
/// `data` must be aligned for `u8` (alignment 1). Ownership stays with the caller.
// SAFETY: the integrating program must reserve this exact global symbol;
// no other definition may use it, and callers must use this C ABI signature.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn demo_rust_process(data: *const u8, len: usize) -> c_int {
    if len > isize::MAX as usize || (len != 0 && data.is_null()) {
        return -1;
    }

    match std::panic::catch_unwind(|| {
        let input: &[u8] = if len == 0 {
            &[] // Never construct a slice from a possibly null pointer.
        } else {
            // SAFETY: checks establish non-nullness and the size bound;
            // the caller supplies the remaining documented guarantees.
            unsafe { core::slice::from_raw_parts(data, len) }
        };
        process(input)
    }) {
        Ok(status) => status,
        // abort() does not return, allocate here, or drop the panic payload.
        Err(_payload) => std::process::abort(),
    }
}
```

For this abort policy, `extern "C"` already supplies an aborting boundary; the explicit catch demonstrates a handler that cannot trigger a second panic by dropping the payload. It does not promise recovery: aborting panics, panic-hook failures and some double panics never reach this branch, and abort does not run normal destructor cleanup. A design that returns an error after a caught panic must additionally justify state recovery and handle payload destruction. Catching a panic never validates foreign pointers. *Sources: [`catch_unwind`](https://doc.rust-lang.org/std/panic/fn.catch_unwind.html), [`process::abort`](https://doc.rust-lang.org/std/process/fn.abort.html).*

## 9. Concurrency and async

Choose thread safety and scheduling behavior to match the API's contract. Protect shared invariants explicitly, keep executor workers responsive, and design cancellation as part of correctness.

### Locks and threads

- **SHOULD** treat mutex poisoning as evidence that an invariant may have been interrupted. Propagating the failure with `expect` can be appropriate. Recovery with `PoisonError::into_inner` is also legitimate when the application can verify or restore the protected state; clear poison only after that reasoning or repair.
- **MUST NOT** rely on poisoning for memory safety. Detection is advisory and incomplete; an unpoisoned mutex does not prove that every application invariant holds.
- **SHOULD** start with `std::sync::Mutex` for ordinary synchronous locking unless the platform or project has another requirement. Keep critical sections short and define lock ordering when several locks are involved.
- **SHOULD** minimize mutable global state and prefer explicit state ownership or dependency injection. Synchronized globals and thread-locals are legitimate tools, but a library should not assume its crate-local state is a unique process-wide instance across separately linked copies or versions.
- **SHOULD** document meaningful blocking, I/O and latency behavior. Prefer waiting primitives or batching to unbounded busy-waiting; specialized spin algorithms require an explicit concurrency and performance justification.
- **PROJECT POLICIES:** Google's guide prohibits extracting poisoned mutex contents; its Chromium guidance selects project scheduling facilities instead of starting threads through `std`, and its Fuchsia guidance selects `fuchsia_sync::Mutex`. These are not universal Rust restrictions. *Sources: [`Mutex` poisoning](https://doc.rust-lang.org/std/sync/struct.Mutex.html#poisoning), [Google concurrency policies](https://google.github.io/styleguide/rustguide.html#concurrency).*

### Send and Sync

- **SHOULD** make reusable public types `Send` and `Sync` when their semantics permit it. `Send` allows ownership transfer between threads; `Sync` means shared references can cross threads. The compiler derives these auto traits only when appropriate, and legitimate local or thread-affine types may lack them.
- **SHOULD** provide `Send` futures when callers need a runtime API that moves tasks between threads. Single-thread executors and local tasks can accept `!Send` futures. Capturing a `!Send` value, or retaining it across suspension, can prevent a future from being `Send`.
- **SHOULD** assert promised auto traits at compile time to catch accidental API regressions.
- **MUST NOT** add `unsafe impl Send`/`Sync` merely to suppress compiler errors. A justified manual implementation is possible when its complete thread-safety contract is proved, including access, ownership and destruction on other threads. *Sources: [`Send`](https://doc.rust-lang.org/std/marker/trait.Send.html), [`Sync`](https://doc.rust-lang.org/std/marker/trait.Sync.html), [Rust API Guidelines: auto traits](https://rust-lang.github.io/api-guidelines/interoperability.html#c-send-sync).*

```rust
//! A compile-time check for a client's thread-safety contract.

/// A client whose API permits transfer and shared access across threads.
#[derive(Debug)]
pub struct MyClient;
const _: fn() = || {
    fn assert_send_sync<T: Send + Sync>() {}
    assert_send_sync::<MyClient>();
};
```

### Async

- **PROJECT POLICY (Chromium):** Google's guide restricts `async`/`.await` in Chromium. Async Rust is otherwise a normal language facility; choose it according to the workload and runtime integration.
- **SHOULD** use `async fn` when it makes the API clear. An explicit returned `impl Future` is also useful to express bounds such as `+ Send`, perform synchronous setup, or define a trait contract. Rust 1.85 supports `async fn` in traits; account for their future bounds and dynamic dispatch needs.
- **MUST NOT**, under this guide's scheduling policy, perform blocking I/O, thread sleeps or unbounded CPU work on executor workers. Use asynchronous operations or the runtime's blocking facilities; use bounded concurrency or a dedicated CPU pool for substantial computation. Blocking one worker delays tasks assigned to it; the effect depends on runtime configuration.
- **SHOULD** release ordinary mutex guards before `.await`. A synchronous mutex can be appropriate for a short, low-contention critical section without suspension; holding locks across an await needs explicit deadlock, scheduling and cancellation analysis, and may call for an async mutex.
- **SHOULD** split long work into bounded chunks and use the runtime's cooperative scheduling facilities where needed. An `.await` whose future is immediately ready need not yield. Microsoft's suggestion of 10 to 100 μs between yields assumes a particular switching-cost model; measure responsiveness and throughput instead of treating it as a universal budget.
- **SHOULD** inspect future size and values retained across suspension when memory or scheduling measurements indicate a problem.
- **SHOULD** abstract synchronous I/O with `Read`/`Write` or separate the algorithm from I/O. For async I/O, choose the ecosystem's traits deliberately: there is no standard-library `AsyncRead`, and different runtime traits need not interoperate directly.
- **MUST** maintain memory safety if a future is dropped before completion. **SHOULD** document cancellation effects on application state and external operations. Dropping an owned future cancels its future polling and drops its local state; it does not roll back completed I/O or necessarily stop separately spawned work. In Tokio, dropping a task's `JoinHandle` detaches it, and already-started `spawn_blocking` work cannot normally be aborted. *Sources: [`Future` runtime behavior](https://doc.rust-lang.org/std/future/trait.Future.html#runtime-characteristics), [Async Book: blocking and cancellation](https://rust-lang.github.io/async-book/part-guide/more-async-await.html#blocking-and-cancellation), [Tokio `spawn_blocking`](https://docs.rs/tokio/latest/tokio/task/fn.spawn_blocking.html), [Tokio `JoinHandle`](https://docs.rs/tokio/latest/tokio/task/struct.JoinHandle.html), [Microsoft yield guidance](https://microsoft.github.io/rust-guidelines/guidelines/performance/index.html#m-yield-points).*
- **MUST**, for an operation that may already have reached an external or paid system (an API call, a write, a job submission), create a stable operation identifier before sending the request, make the remote side idempotent or deduplicated by that identifier where the system allows it, and keep a reconciliation path that later confirms the actual outcome. Dropping the local future, or losing the response, says nothing about what the remote side did.

## 10. Macros and metaprogramming

Prefer ordinary Rust abstractions when they express the API clearly. The requirement levels below are this guide's policy; linked sources explain the language rules or the named organization's recommendations.

### When to use them

- **SHOULD** start with functions, types, traits and modules. Introduce a macro when the repetition or syntax benefit justifies its maintenance cost. External code generation and procedural macros are alternatives to assess, rather than a universal ordered ladder. [Google: Macros](https://google.github.io/styleguide/rustguide.html#macros).
- **SHOULD** consider expansion readability, diagnostics, IDE support, compilation time and edition compatibility before selecting a macro.
- **SHOULD** prefer `macro_rules!` when it suffices. **MUST NOT**, under this guide's policy, obscure an item's signature, kind or `async` behavior. [Microsoft: macro guidelines](https://microsoft.github.io/rust-guidelines/guidelines/macros/).

### Writing macros

- **MUST** qualify references to the defining crate in exported `macro_rules!` expansions with `$crate::`. This does not apply to caller-supplied paths or every path in an expansion. `$crate` resolves the defining crate but does **not** bypass privacy: any helper reached from another crate must be publicly accessible. [Rust Reference: hygiene](https://doc.rust-lang.org/reference/macros-by-example.html#hygiene).
- **SHOULD** document the syntax, generated behavior and examples of exported macros. **SHOULD** test them from a separate consumer crate, including renamed dependencies where supported, rather than only inside the defining crate.

This complete example deliberately exposes a helper for downstream expansion. `#[doc(hidden)]` hides it from ordinary documentation; it does not make it private.

```rust
#[doc(hidden)]
pub mod __private {
    pub fn greeting(name: &str) -> String {
        format!("Hello, {name}!")
    }
}

/// Builds a greeting for the supplied name.
#[macro_export]
macro_rules! greet {
    ($name:expr) => {
        $crate::__private::greeting($name)
    };
}

fn main() {
    assert_eq!(greet!("Ferris"), "Hello, Ferris!");
}
```

### Procedural macros

- **SHOULD** keep transformation logic in an ordinary implementation crate when that improves unit testing; the `proc-macro` crate can delegate to it. **SHOULD** test successful expansions and rejected uses, using snapshots and compile-error tests where useful.
- **MUST**, when adopting Microsoft's coupled companion-crate release model, pin companion crates to the same exact version (`=x.y.z`) and coordinate publication. This prevents incompatible generated code under that model. It is not a Cargo requirement and does not apply automatically to independently usable macro crates; other designs need an explicit compatibility strategy.
- **SHOULD** route generated references to third-party dependencies through a deliberately public, documentation-hidden helper module in the host crate. This is an intentional dependency re-export that carries compatibility obligations. Keep helper names out of ordinary user code and maintain compatibility between macros and helpers.
- **MUST NOT** introduce undocumented public names or collision-prone helpers. Procedural macros are unhygienic; use suitable qualified paths and carefully chosen generated identifiers. [Rust Reference: procedural macro hygiene](https://doc.rust-lang.org/reference/procedural-macros.html#procedural-macro-hygiene).

The companion-crate, helper-module and testing recommendations above come from [Microsoft's macro guidelines](https://microsoft.github.io/rust-guidelines/guidelines/macros/); this guide scopes their adoption rather than treating them as Rust language rules.

## 11. Documentation

Public API documentation explains the contract, shows useful behavior and states failure and safety conditions. Internal comments explain decisions and invariants that readers cannot infer from the code.

### Comment kinds

- **MUST** use rustdoc comments for API documentation: normally `///` before an item and `//!` inside the containing crate or module, before its items. A public module can also be documented with `///` on its declaration. These are conventions built on Rust's doc-attribute syntax. [Rust Reference: comments](https://doc.rust-lang.org/reference/comments.html).
- **SHOULD** use `//` for implementation explanations, invariants and issue-linked TODOs. Ordinary comments do not appear in rustdoc. They are not reserved for commented-out code, which version control generally makes unnecessary.
- **SHOULD** follow the repository's TODO/FIXME convention and include a useful issue reference when work is tracked. Do not copy Chromium-specific issue hosts or compiler-repository rules into unrelated projects.
- **SHOULD** keep documentation directly readable in source. Generated docs are appropriate when they avoid duplication without obscuring the contract. [Google: documentation](https://google.github.io/styleguide/rustguide.html#when-and-how-to-write-documentation).

### Content

- **MUST**, under this guide's library policy, enable `missing_docs` and document the public API, including public enum variants and fields. Self-explanatory names are not an automatic lint exemption. If the project adopts Google's allowance for terse or omitted variant documentation, use a narrow, explained lint exception. [rustc: `missing_docs`](https://doc.rust-lang.org/rustc/lints/listing/allowed-by-default.html#missing-docs).
- **SHOULD** begin with a concise summary sentence and put details in subsequent paragraphs; a fixed word count is unnecessary.
- **MUST** describe expected error conditions in `# Errors`, caller-relevant panic conditions in `# Panics`, and the obligations of every public `unsafe fn` or `unsafe trait` in `# Safety`. State meaningful conditions rather than adding empty headings. **SHOULD** give examples that demonstrate the API's purpose and use `?` for fallible operations that readers may copy. Assertions remain appropriate for showing expected results. [Rust API Guidelines: documentation](https://rust-lang.github.io/api-guidelines/documentation.html).
- **SHOULD** use intra-doc links such as ``[`std::num::ParseIntError`]`` and check for broken links when building docs. [rustdoc: intra-doc links](https://doc.rust-lang.org/rustdoc/write-documentation/linking-to-items-by-name.html).
- **MUST** make examples runnable: include imports, dependencies, setup and valid inputs. Rustdoc lines beginning with `#` can hide setup while still compiling it. Use `no_run` only when execution needs an external service or environment; label non-Rust sketches as `text`. [rustdoc: documentation tests](https://doc.rust-lang.org/rustdoc/documentation-tests.html).
- **SHOULD** provide accurate package metadata, including description, repository, license or license file, and appropriate keywords and categories. [Cargo: manifest format](https://doc.rust-lang.org/cargo/reference/manifest.html).

For a library package named `port_parser`, this complete `src/lib.rs` example has no external files or dependencies. Set `edition = "2024"` and `rust-version = "1.85"` in its manifest.

````rust
//! Parses decimal network port numbers.
#![warn(missing_docs)]

/// Parses a decimal port number.
///
/// # Errors
///
/// Returns an error for invalid digits, an empty input, or a value above 65535.
///
/// # Examples
///
/// ```
/// # fn main() -> Result<(), std::num::ParseIntError> {
/// assert_eq!(port_parser::parse_port("8080")?, 8080);
/// # Ok(())
/// # }
/// ```
pub fn parse_port(input: &str) -> Result<u16, std::num::ParseIntError> {
    input.parse()
}
````

## 12. Testing

Tests exercise observable behavior, failure paths and important invariants. Match the test strategy to the risks and supported platforms.

### Organization

- **MUST** follow the repository's selected test framework. Ordinary Cargo projects normally use `#[test]`; Chromium and Fuchsia integrations have their own framework requirements. [Google: testing](https://google.github.io/styleguide/rustguide.html#testing).
- **SHOULD** put unit tests in a `#[cfg(test)] mod tests` near the implementation. `use super::*` is reasonable there. Put consumer-facing integration tests in `tests/`. Those tests compile as separate crates and access the library's public API. [Rust Book: test organization](https://doc.rust-lang.org/book/ch11-03-test-organization.html).
- **SHOULD** make nondeterministic or expensive boundaries replaceable where that enables meaningful tests, without introducing traits solely to satisfy a blanket mocking rule.
- **MUST** keep test helpers out of the normal production API. `cfg(test)` works for unit-test helpers; it is not enabled for the ordinary library build linked by integration tests. Shared integration helpers can live under `tests/common/mod.rs`; helpers intentionally exported to other crates may need an explicitly enabled `test-util` feature. Features are additive, so enabling one can include that helper code in a production build. [Rust Book: integration tests](https://doc.rust-lang.org/book/ch11-03-test-organization.html#integration-tests).

### Assertions

- **MAY** use `expect`, `unwrap` or an explicit panic when unexpected failure should fail a test. Prefer `expect` when context would help. This exception does not change the recommendation to demonstrate error propagation in public API examples.
- **SHOULD** use precise assertions and add context when it explains a non-obvious expectation. Test boundaries and rejected inputs, rather than only weak properties such as the sign of a sum.

This complete example tests a checked operation and its overflow boundary:

```rust
//! Checked arithmetic with focused unit tests.

/// Adds two quantities, returning `None` on overflow.
pub fn checked_total(left: u32, right: u32) -> Option<u32> {
    left.checked_add(right)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adds_values_without_overflow() {
        assert_eq!(checked_total(20, 22), Some(42));
    }

    #[test]
    fn rejects_overflow() {
        assert_eq!(checked_total(u32::MAX, 1), None);
    }
}
```

### Additional checks

- **MUST**, under this guide's CI policy, run documentation tests for supported configurations, for example `cargo test --workspace --doc`. Ordinary Rust doctests compile and execute by default; `no_run`, `compile_fail` and `ignore` change that behavior. A successful ignored example provides no execution evidence. [rustdoc: documentation tests](https://doc.rust-lang.org/rustdoc/documentation-tests.html).
- **MUST** exercise novel unsafe abstractions with adversarial cases under Miri when supported, using a separately selected nightly toolchain, such as `cargo +nightly miri test`. Record unsupported cases and cover them by other suitable methods. Miri checks executed paths under its model, has platform and foreign-function limitations, and cannot prove soundness. A passing run supplements the safety argument and review. [Miri: usage and limitations](https://github.com/rust-lang/miri).
- **SHOULD** consider property testing and fuzzing for parsers, boundary-heavy algorithms and externally supplied data. Select maintained tools and inspect their own toolchain and target requirements before adding commands to CI.
- **SHOULD** test cancellation for operations that reach external systems: drop the future or kill the worker mid-call and check that reconciliation finds the real outcome (see section 9).

## 13. Security, dependencies and critical systems

Define arithmetic behavior, review dependencies and keep build inputs reproducible. Apply stronger controls where the threat model or safety plan requires them. The policies here do not themselves establish regulatory compliance or certification.

### Arithmetic and conversions

- **MUST** make integer overflow behavior explicit where it matters to correctness or security. Use checked operations for rejection, saturating operations for bounded quantities, or wrapping operations when modular arithmetic is intended; document the choice. Explicit operations have the specified behavior independently of the ordinary overflow-check profile setting. Do not infer that every program behaves identically in debug and release. [Rust Reference: overflow](https://doc.rust-lang.org/reference/expressions/operator-expr.html#overflow).
- **MUST** retain integer overflow checks in development and tests. **SHOULD** enable them in production unless the project's documented arithmetic and failure strategy justifies another choice. They produce panics; they do not turn overflow into a recoverable `Result`. Workspace-root profiles control these settings, and dependency profiles do not override them. [Cargo: profiles](https://doc.rust-lang.org/cargo/reference/profiles.html#overflow-checks).
- **SHOULD** use `From` for supported infallible conversions and `TryFrom` when rejection is needed. Review numeric `as` casts for truncation, sign changes and precision loss. A strict project may enable `clippy::as_conversions`, with narrow explanations for intentional casts. [Rust Reference: numeric casts](https://doc.rust-lang.org/reference/expressions/operator-expr.html#numeric-cast).
- **MUST** validate shift counts for ordinary shifts, or select a checked or explicitly wrapping operation. Negative counts and counts at least the integer width are overflow conditions for ordinary shifts. [Rust Reference: overflow](https://doc.rust-lang.org/reference/expressions/operator-expr.html#overflow).

This complete example rejects both arithmetic overflow and an out-of-range conversion:

```rust
fn wire_total(left: usize, right: usize) -> Result<u32, &'static str> {
    let total = left.checked_add(right).ok_or("total overflow")?;
    u32::try_from(total).map_err(|_| "total exceeds the wire format")
}

fn main() -> Result<(), &'static str> {
    assert_eq!(wire_total(20, 22)?, 42);
    assert!(wire_total(usize::MAX, 1).is_err());
    Ok(())
}
```

### Supply chain

- **MUST** commit `Cargo.lock` for deployed applications and use `--locked` in reproducible CI builds. **SHOULD** commit it for library repositories too, unless a documented workflow meets their needs better. This records the repository's resolution; downstream users resolve the library under their own dependency graph. Test supported dependency updates as well as the committed resolution. The Cargo team's current guidance is project-dependent, with committing the lockfile as a starting point for both kinds of package. [Cargo FAQ](https://doc.rust-lang.org/cargo/faq.html#why-have-cargolock-in-version-control), [Cargo team's 2023 change](https://blog.rust-lang.org/2023/08/29/committing-lockfiles/).
- **MUST** review and record new direct dependencies, including their purpose, maintenance, licenses, enabled features and security implications. **SHOULD** extend the review to transitives according to risk, especially build scripts, proc macros and unsafe or native code. ANSSI requires tracked validation of direct dependencies and recommends it for all dependencies. [ANSSI: libraries](https://anssi-fr.github.io/rust-guide/libraries.html).
- **MUST**, under this guide's CI policy, check the resolved graph for known advisories with `cargo audit` or an equivalent maintained check, and track justified exceptions and update decisions. A clean advisory scan is not a source-code audit or evidence that no vulnerabilities exist. [RustSec: tooling](https://rustsec.org/).
- **SHOULD** automate license, source and dependency restrictions with `cargo deny`. **MAY** use `cargo vet` to check whether dependency versions are covered by audits from trusted reviewers, with explicit criteria and exceptions. It records and evaluates review evidence; it does not perform a complete security review automatically. [cargo-deny](https://embarkstudios.github.io/cargo-deny/), [cargo-vet](https://mozilla.github.io/cargo-vet/).
- **SHOULD** minimize unnecessary dependencies and enabled features, while accounting for the cost of maintaining replacement code.

### Toolchain

- **MUST** declare and test the supported minimum Rust version (`rust-version`) and edition. These examples target Rust 1.85 or newer and edition 2024; that baseline is not a claim about today's latest release. Tool versions and dependency MSRVs can change independently. [Cargo: Rust version](https://doc.rust-lang.org/cargo/reference/rust-version.html).
- **SHOULD** use an explicitly selected stable release for ordinary production builds, update it deliberately, and record the compiler, target and relevant tools for reproducibility. ANSSI prescribes stable in its secure-development profile; a specialized environment may impose a particular approved toolchain instead. Run nightly-only analysis separately, using an explicit override. `rustfmt` itself is available on stable; only selected unstable options require nightly. [ANSSI: development environment](https://anssi-fr.github.io/rust-guide/devenv.html), [rustup: overrides](https://rust-lang.github.io/rustup/overrides.html).
- **MUST** justify unstable features against the owning repository's actual policy and supported targets. Chromium, Android and Fuchsia exceptions belong to those projects and are not general exceptions for unrelated code.
- **MUST** review automated fixes before committing them and rerun the affected checks.
- **SHOULD** use explicit initialization and teardown. Runtime registration before `main`, such as constructor-based schemes, needs a documented ordering and platform rationale; projects following Google's policy forbid it. This does not prohibit compile-time constant evaluation. [Google: no code before main](https://google.github.io/styleguide/rustguide.html).

### Memory and secrets

- **MUST NOT** depend on destructors running for memory soundness. Resource leaks are memory-safe in Rust, though they can exhaust resources or retain secrets. `mem::forget`, intentional process-lifetime allocation with `Box::leak`, and ownership transfer to foreign code have legitimate uses; review the lifetime, ownership and resource budget. ANSSI's secure-development profile intentionally prohibits leaks and `mem::forget`; projects adopting it must enforce that stricter policy. [Standard library: `forget`](https://doc.rust-lang.org/core/mem/fn.forget.html), [ANSSI: memory management](https://anssi-fr.github.io/rust-guide/unsafe/memory.html).
- **MUST** satisfy the specific safety contract of each `from_raw` API. Pairing it with the matching `into_raw` is the normal pattern, not a universal language restriction. `Box::from_raw` also permits correctly allocated, aligned and initialized storage satisfying its documented contract; `Rc`, `Arc` and `CString` have their own requirements. Prevent duplicate ownership and double reconstruction. [Standard library: `Box::from_raw`](https://doc.rust-lang.org/std/boxed/struct.Box.html#method.from_raw).
- **MUST** minimize secret copies and lifetime, and use a reviewed zeroing facility such as `zeroize` when erasure is required. Ordinary overwrites may be optimized away. Explicit erasure must execute to help; a wrapper that erases in `Drop` still depends on destruction. Copies, reallocations, registers and external storage need separate consideration. [zeroize: guarantees and limits](https://docs.rs/zeroize/latest/zeroize/).
- **MUST** account for destructor execution accurately: stack unwinding normally runs destructors; aborting, `mem::forget` and `process::exit` can skip them. Zeroing libraries cannot erase data through a destructor that never runs. [Rust Reference: destructors](https://doc.rust-lang.org/reference/destructors.html), [standard library: process exit](https://doc.rust-lang.org/std/process/fn.exit.html).
- **MUST NOT**, under this guide's policy, let `Drop` panic. Use an explicit fallible cleanup method when callers need to handle failure, and retain best-effort cleanup in `Drop`. **SHOULD** break resource-owning `Rc` or `Arc` cycles, for example with `Weak`, instead of expecting reference counts to collect cycles.

### Critical systems

- **MUST** identify the applicable safety standard, project safety plan, toolchain and target constraints, and the exact revision of any adopted coding guidelines. Apply the Safety-Critical Rust Coding Guidelines according to each rule's status, category and supported compiler releases. They are not universally binding law, and their categories do not permit treating every rule and deviation identically. [Safety-Critical Rust: guideline categories and status](https://coding-guidelines.arewesafetycriticalyet.org/process/style-guideline.html).
- **MUST** document compliance evidence and deviations as required by the adopted process. Passing Rust checks, Miri or a lint profile does not certify a component or system.
- **SHOULD** centralize lint and build policies and make stricter safety-related configurations explicit. S-CORE's centrally maintained policy is a project-specific implementation of this approach; consult its current policy repository rather than presenting a short excerpt as its complete configuration. [S-CORE: Rust coding guidelines](https://eclipse-score.github.io/score/main/contribute/development/rust/coding_guidelines.html).

The following is an illustrative Cargo policy excerpt, not a certification profile. It must be combined with the project's panic strategy, target constraints, review process and toolchain-specific checks. Workspace members opt in to inherited lint settings explicitly.

```toml
# Workspace-root Cargo.toml, excerpt
[workspace.lints.rust]
unsafe_op_in_unsafe_fn = "deny"
missing_docs = "warn"

[workspace.lints.clippy]
undocumented_unsafe_blocks = "deny"
as_conversions = "warn"
mem_forget = "warn"

[profile.dev]
overflow-checks = true

[profile.test]
overflow-checks = true

[profile.release]
overflow-checks = true
```

```toml
# Each member's Cargo.toml, excerpt
[lints]
workspace = true
```

## Recommended tools and CI

Use a small required baseline and add tools for concrete risks. Record tool versions and configuration; installing every tool is not itself a quality strategy. The commands below assume the tools and the project's required system dependencies are available.

### Baseline for ordinary Cargo projects

| Check | Example command | What it establishes |
| --- | --- | --- |
| Formatting | `cargo fmt --all -- --check` | Source matches the selected formatter and configuration. |
| Lints | `cargo clippy --workspace --all-targets --locked -- -D warnings` | The selected Cargo targets have no unsuppressed warnings in that configuration. |
| Tests | `cargo test --workspace --locked` | Runs the selected workspace's ordinary tests, including library doctests by default. |
| Explicit doctest job | `cargo test --workspace --doc --locked` | Needed when the chosen test workflow does not already run documentation tests. |
| Documentation | `RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps --locked` | Builds documentation and fails on rustdoc warnings, including broken intra-doc links. |
| Known advisories | `cargo audit` | Checks the resolved lockfile against known security advisories. |
| Dependency policy, when adopted | `cargo deny check` | Checks the configured advisory, license, source and dependency restrictions. |

`--all-targets` means Cargo targets such as libraries, binaries and tests; it does not mean every architecture. `--locked` prevents dependency resolution from changing the lockfile, but does not by itself pin the compiler, operating system or external tools. Select feature combinations deliberately: default features, no-default-features where supported, important optional features, and all-features only when that combination is supported. Check the MSRV and relevant deployment targets in separate jobs. [Cargo test options](https://doc.rust-lang.org/cargo/commands/cargo-test.html), [Cargo feature unification](https://doc.rust-lang.org/cargo/reference/features.html), [Cargo CI guidance](https://doc.rust-lang.org/cargo/guide/continuous-integration.html), [RustSec tools](https://rustsec.org/)

### Additional tools by purpose

| Need | Tool or command | Use and limitation |
| --- | --- | --- |
| Public API compatibility | `cargo semver-checks` | Checks supported API changes against a baseline release; retain behavioral and compatibility review. |
| Dependency review coverage | `cargo vet` | Matches dependency versions against trusted audits and configured criteria. |
| Dependency maintenance | `cargo outdated`, `cargo machete` | Identify update candidates or apparently unused declarations; review the findings. |
| Unsafe implementation analysis | `cargo +nightly miri test` | Detects supported forms of undefined behavior in executed cases; pin an available nightly and record coverage gaps. |
| Input exploration | `cargo-fuzz`, `proptest` | Exercise parsers, boundaries and invariants; configure targets, dependencies and a useful runtime budget. |
| Test execution | `cargo nextest run --workspace --locked` | Runs tests in isolated processes; run doctests separately. |
| Coverage | `cargo llvm-cov --workspace` | Shows exercised code; coverage percentages alone do not establish correctness. |
| Performance | `criterion`, `divan`, profilers | Measure representative workloads, allocations and relevant regressions. |
| Macro testing | `insta`, `trybuild` | Check expansion snapshots and intended compilation failures. |
| Foreign bindings | `bindgen`, `cbindgen`, `cxx`, Crubit | Support interop with different contracts and build requirements; review the resulting boundary. |

Primary tool references: [cargo-semver-checks](https://github.com/obi1kenobi/cargo-semver-checks), [cargo-vet](https://mozilla.github.io/cargo-vet/), [Miri](https://github.com/rust-lang/miri), [nextest](https://nexte.st/docs/running/), and the source links in the relevant implementation sections. Nextest's documented workflow still requires a separate doctest step; `cargo test --all-targets` also does not select doctests. [Cargo target selection](https://doc.rust-lang.org/cargo/commands/cargo-test.html#target-selection)

## Where context changes the rule

These differences are choices about scope and contracts, not a contest to select the most restrictive sentence.

| Topic | Context or source policy | General guidance in this revision |
| --- | --- | --- |
| Async and threads | Google's Chromium rules constrain runtime integration. | Choose the runtime and workload model; those restrictions do not apply to unrelated Cargo projects. |
| Mutex choice | Fuchsia uses its own synchronization primitives. | Use a suitable mutex for the environment; keep synchronous critical sections short. |
| Poison recovery | Google's style policy rejects recovering the poisoned value. | Recovery is possible when state can be verified or repaired; poisoning never proves memory safety. |
| Panic containment | Google restricts `catch_unwind`; engineered FFI boundaries can need containment. | Define panic strategy and recovery explicitly; catches do not handle aborts or undefined behavior. |
| Constructor names | Google requires `try_new` for a fallible initialization function. | Use a clear constructor name; fallible `new` is also an established API pattern. |
| API representation | Some organizational guidelines restrict ownership types, preludes or re-exports. | Make deliberate compatibility commitments; these constructs are not inherently unsound. |
| Formatting toolchain | Some repositories pin nightly formatter options or custom build tools. | Pin formatting separately when needed; stable compilation and nightly analysis can coexist. |
| Miri | Analysis requirements and supported targets differ. | Require supported coverage for novel unsafe abstractions under this guide, record gaps and retain the safety argument. |
| Resource leaks | ANSSI's secure-development profile prohibits intentional leaks and forgetting. | Respect that profile when adopted; Rust's soundness model itself permits leaks and does not guarantee destructors run. |
| Functional safety | S-CORE and the Safety-Critical Rust Guidelines have their own revisions, scopes and processes. | Adopt the applicable rules explicitly and assemble the required evidence; a lint excerpt is not certification. |
| TODO comments | The rustc repository rejects unfinished TODOs at submission and uses FIXME for persistent notes. | Follow the owning repository's convention rather than imposing compiler-project rules elsewhere. |
| Project contracts | A system's ADRs and architecture documents define authorization, data protection, idempotency and recovery. | Those documents take precedence over this guide where they conflict. |

Sources: [Google Rust Style Guide](https://google.github.io/styleguide/rustguide.html), [standard-library Mutex](https://doc.rust-lang.org/std/sync/struct.Mutex.html), [standard-library catch_unwind](https://doc.rust-lang.org/std/panic/fn.catch_unwind.html), [ANSSI](https://anssi-fr.github.io/rust-guide/), [S-CORE](https://eclipse-score.github.io/score/main/contribute/development/rust/coding_guidelines.html), and [rustc conventions](https://rustc-dev-guide.rust-lang.org/conventions.html). Details and direct contract links appear in the corresponding sections above.

## 14. Code review checklist

Select the items relevant to the change and the adopted profile. A checked box should identify evidence or a deliberate decision; it should not substitute for understanding the implementation.

### Toolchains, configuration and automation

- [ ] The MSRV, edition, deployment targets and required feature combinations are defined and tested.
- [ ] Formatting and Clippy pass with the selected tool versions; intentional local suppressions have reasons.
- [ ] Lint expectations remain valid across the supported configurations; conditional allowances are scoped.
- [ ] Shared workspace lints are inherited by the intended members.
- [ ] Build, test, rustdoc and relevant security checks run in CI without assuming that one command covers every configuration.

### Organization and public API

- [ ] Modules and crates have cohesive responsibilities; added abstraction serves actual consumers.
- [ ] Imports and re-exports are deliberate, and public dependencies have an explicit compatibility commitment.
- [ ] Construction and mutation preserve the type's invariants; public fields are appropriate for the contract.
- [ ] Names, conversions and getters communicate actual semantics, ownership and cost.
- [ ] Parameters accept appropriate borrows or ownership; ambiguous booleans and interchangeable identifiers have been considered.
- [ ] Useful standard traits have correct semantics; diagnostics redact secrets.
- [ ] Hashing, equality and ordering agree where applicable.
- [ ] Non-exhaustive types provide a usable external construction and matching strategy.
- [ ] Library compatibility review covers signatures, bounds, traits, auto traits, const-callability and behavior.

### Errors and panics

- [ ] Expected failure, absence and violated invariants use deliberate contracts.
- [ ] Error values provide the context and interoperability the caller needs, preserving meaningful causes.
- [ ] Panic and `expect` messages explain the invariant without disclosing secrets.
- [ ] Public failure and panic conditions are documented; malformed external input is not treated as an internal bug.
- [ ] Any `catch_unwind` boundary accounts for panic strategy, protected state, hooks and payload/destructor cleanup.

### Ownership and performance

- [ ] Borrowing, cloning and reference counting match the intended lifetime and sharing model.
- [ ] Public smart pointers or interior mutability are deliberate API choices.
- [ ] Capacity reservations and allocations are appropriate and bounded for untrusted input.
- [ ] Optimization claims have representative evidence; performance-motivated unsafe code has benchmarks.
- [ ] Iterator versus collection returns and reusable buffers match caller needs.

### Unsafe and FFI

- [ ] Every unsafe operation or implementation has a complete, locally applicable safety argument.
- [ ] Safe entry points cannot expose undefined behavior through hidden caller obligations.
- [ ] Unsafe function and trait contracts are documented, including lifetime, aliasing and thread requirements.
- [ ] Raw slices satisfy initialization, provenance, one-allocation, size, readability and mutation conditions.
- [ ] FFI layout, calling convention, value validity, ownership and release operations match the foreign interface.
- [ ] Edition-specific unsafe attributes and external declarations are correct, including symbol uniqueness.
- [ ] Unwinding, aborting, partial initialization and cancellation preserve memory soundness.
- [ ] Applicable adversarial/Miri checks are run; unsupported cases have a documented alternative validation plan.

### Concurrency and async

- [ ] Promised `Send`/`Sync` properties and future bounds match the intended executor and thread affinity.
- [ ] Locks have suitable ordering and duration; guards do not cross suspension without explicit justification.
- [ ] Mutex recovery validates or restores state, and no soundness argument depends on poison detection.
- [ ] Executor workers remain responsive; substantial blocking or CPU work uses the appropriate facilities.
- [ ] Cancellation, detached tasks and already-started external work have defined ownership and cleanup behavior.
- [ ] Calls to external or paid systems carry a stable operation id, are idempotent where possible, and have a reconciliation path.

### Documentation and tests

- [ ] Public library items are documented; examples include necessary setup and valid inputs.
- [ ] Applicable `# Errors`, `# Panics` and `# Safety` sections describe real contracts.
- [ ] Documentation builds without broken intra-doc links, and executable doctests actually run.
- [ ] Unit and integration tests assert meaningful behavior, boundaries and failure cases.
- [ ] Unit-only helpers use `cfg(test)`; shared test support cannot silently invalidate production invariants.
- [ ] Exported macros are checked from consumer contexts when visibility or path resolution matters.
- [ ] Mocks, property tests and fuzzing address concrete risks rather than merely increasing test counts.

### Dependencies and security

- [ ] New dependencies and risky transitives have an appropriate review; licenses, sources and features are acceptable.
- [ ] The lockfile and update policy support both reproducible builds and testing supported dependency updates.
- [ ] Known-advisory results and any exceptions are assessed; a clean scan is not treated as a complete audit.
- [ ] Overflow, numeric conversion and shift behavior match the domain and failure strategy.
- [ ] Secret copies, diagnostics and erasure are controlled; skipped destructors are accounted for.
- [ ] Specialized safety requirements use an explicit adopted revision and their actual evidence/deviation process.

### Agent-assisted changes and soundness sign-off

- [ ] A responsible human engineer read the full diff (not only the agent transcript) and can explain root cause, invariants preserved, edge cases and open uncertainties.
- [ ] LLM/agent disclosure states extent and purpose in the engineer's own words (not a generic badge or trailer).
- [ ] Every `// SAFETY:` argument, `# Safety` doc section, `unsafe` block/impl, and safe invariant upholding `unsafe` preconditions was authored, rewritten or explicitly verified by a human domain owner (`M34`).
- [ ] Bug fixes demonstrate red-green regression evidence; snapshot/expected-output diffs were inspected rather than blindly blessed.
- [ ] Recurring review findings were encoded into deterministic lints (`clippy.toml` `disallowed-methods`/`disallowed-types` or policy checks) where practical.

## 15. Changes authored with agents and LLMs

Coding agents and LLMs can accelerate exploration, refactoring and test writing, but they introduce two recurring failure modes: high-volume diffs and plausible-looking code that nobody deeply understands. The rules below apply whenever a change is drafted or assisted by an agent.

### Human-first documentation and authority

- **MUST NOT** require an LLM to understand or contribute to a repository. Architecture notes, testing procedures and engineering standards live in human documentation (this guide and project ADRs); `AGENTS.md` and `CLAUDE.md` only summarize them.
- **MUST NOT** treat an LLM review as sufficient to approve, merge or block a pull request. Agent review is strictly advisory and never substitutes for author self-review or required human code-owner approval.

### Accountability, disclosure and full-diff self-review

- **SHOULD** assign a responsible human engineer to every agent-assisted pull request. That engineer reads the entire final diff (not just the agent conversation) and records extent and purpose of LLM use in their own words, avoiding empty badges or trailers.
- **SHOULD** verify comprehension before requesting review: state the root cause, why the chosen fix is right compared to alternatives, which invariants are preserved, which edge cases are tested, and what remains uncertain.

### Soundness-critical code and safety prose (`M34`)

- **MUST** (`M34`) have a human domain owner write, rewrite or explicitly verify line by line every `unsafe` block, `unsafe impl`, `// SAFETY:` argument, `# Safety` documentation section, and safe code whose invariants uphold `unsafe` preconditions (areas where wrong code does not look wrong). Tooling checks the presence of safety prose and executes Miri on tested paths; it cannot prove that a safety argument is sound.

### Deterministic linters, cross-model review and mechanical edits

- **SHOULD** convert recurring review findings into deterministic tooling before adding prompt rules: for example, configure `clippy.toml` (`disallowed-methods`, `disallowed-types`) so every contributor and CI run enforces the rule uniformly without token overhead.
- **SHOULD** use an independent read-only reviewer from a **different model family** than the author for `unsafe`, FFI, async, dependency or public API changes when available, because models exhibit self-preference bias toward output from the same model.
- **SHOULD** prove bug fixes with a red-green test (failing on the base commit and passing on the fix), keep mass rewrites in dedicated reviewed commits produced with syntax tools (`cargo fix`, `clippy --fix`, `ast-grep`) per `M31`, and inspect every snapshot diff rather than blindly running `--bless`, `cargo insta accept`, or `TRYBUILD=overwrite`.

## Sources and maintenance

This technical revision uses primary documentation consulted on **30 October 2026**. Many are moving documents; record a release, commit or archived revision when adopting them as a formal project standard. Direct links throughout the guide identify the relevant contracts. The table below describes their role, not blanket conformance to every source.

| Source | Role in this revision |
| --- | --- |
| [Rust Reference](https://doc.rust-lang.org/reference/) | Language behavior, safety, attributes, layout, macros and destruction. |
| [Standard library documentation](https://doc.rust-lang.org/std/) | Concrete API contracts, including raw slices, errors, mutexes, conversions and panics. |
| [Rust Edition Guide](https://doc.rust-lang.org/edition-guide/rust-2024/) | Edition-2024 syntax and migration obligations. |
| [Cargo Book](https://doc.rust-lang.org/cargo/) | Features, workspaces, profiles, MSRV, lockfiles, testing and compatibility. |
| [Rustdoc Book](https://doc.rust-lang.org/rustdoc/) | Documentation syntax, linking and executable examples. |
| [Rust API Guidelines](https://rust-lang.github.io/api-guidelines/) | API naming, interoperability, flexibility and future evolution. |
| [Rust Style Guide](https://doc.rust-lang.org/style-guide/) | Default formatting conventions. |
| [Clippy documentation](https://doc.rust-lang.org/clippy/) and [rustfmt configuration](https://rust-lang.github.io/rustfmt/) | Lint groups, tool behavior and version-dependent configuration. |
| [Google Rust Style Guide](https://google.github.io/styleguide/rustguide.html) | Organization and API conventions, with project scope preserved. |
| [Microsoft Pragmatic Rust Guidelines](https://microsoft.github.io/rust-guidelines/guidelines/) | API, error, performance, macro and implementation policies, applied in context. |
| [ANSSI Secure Rust Guidelines](https://anssi-fr.github.io/rust-guide/) | A stricter secure-development profile; the online guide labels its own edition unstable. |
| [Safety-Critical Rust Coding Guidelines](https://coding-guidelines.arewesafetycriticalyet.org/) | Specialized rule categories, status and adoption processes. |
| [Eclipse S-CORE Rust Guidelines](https://eclipse-score.github.io/score/main/contribute/development/rust/coding_guidelines.html) | An example of scoped safety-related development and centralized policy. |
| [Fuchsia Rust Rubric](https://fuchsia.dev/fuchsia-src/development/api/rust) and [Linux Rust Coding Guidelines](https://docs.kernel.org/rust/coding-guidelines.html) | Domain-specific conventions; they do not replace the owning platform's current requirements. |
| [Rustc Dev Guide: conventions](https://rustc-dev-guide.rust-lang.org/conventions.html) | Compiler-repository conventions, including TODO/FIXME and formatting scope. |
| [Comprehensive Rust: Idiomatic Rust](https://google.github.io/comprehensive-rust/idiomatic/welcome.html) | Teaching context for API and ownership choices. |
| [Miri](https://github.com/rust-lang/miri), [RustSec](https://rustsec.org/), [cargo-deny](https://embarkstudios.github.io/cargo-deny/) and [cargo-vet](https://mozilla.github.io/cargo-vet/) | Tool capabilities and their actual evidence limits. |
| [Tokio documentation](https://docs.rs/tokio/latest/tokio/) and [zeroize documentation](https://docs.rs/zeroize/latest/zeroize/) | Runtime-specific cancellation behavior and the limits of secret erasure. |
| [Rust Forge: LLM Usage Policy](https://forge.rust-lang.org/policies/llm-usage.html) and [rustc-dev-guide: Working with LLMs](https://rustc-dev-guide.rust-lang.org/llm-guidance.html) | Human-first documentation, soundness authorship boundaries, cross-model review bias, deterministic lint preference and meaningful disclosure principles adapted to internal engineering repositories. |

Supplementary sources in the original compilation are useful for their own audiences: [Chromium Rust API design](https://chromium.googlesource.com/chromium/src/+/main/docs/rust/api_design.md), [PingCAP's Rust Style Guide](https://pingcap.github.io/style-guide/rust), [Rust Design Patterns](https://rust-unofficial.github.io/patterns/) and the [Std Dev Guide](https://std-dev-guide.rust-lang.org/). They are not used here to justify universal requirements.

## Revision notes

**Version 1.1** preserved the original subject coverage while correcting unsafe FFI examples, incomplete pointer contracts, panic and destructor claims, test-feature isolation, non-exhaustive construction, conversion naming and version-sensitive configuration. It replaced unsupported universal prohibitions with explicit project policies or contextual recommendations, and aligned the quick reference and checklist with those changes.

**Version 1.2** keeps 1.1 as the base and restores three elements of the previous edition: the adoption subsection with ADR precedence (section 1), the MUST matrix of scope, check and exception (after the golden rules), and the MUST rule for remote operations with a stable id, idempotency and reconciliation (section 9, with a matching test item in section 12 and checklist item). Dashes were removed to match the house style.

**Version 1.2.1** adds stable ids M01 to M33 to the MUST matrix, so tools, exception records and reviews can cite a rule. Two statements were corrected after testing on Rust 1.97.0 and Clippy 0.1.97: `allow_attributes_without_reason` also flags `#[expect]` without a reason (1.2 said it did not), and an unknown lint name in a Cargo `[lints]` table does not fail `-D warnings` (1.2 said it did). The dependency row now names `cargo deny check` as an equivalent advisory check.

**Version 1.3.0** adds `M34` (human domain-owner verification of soundness-critical code and safety prose) and Section 15 (Changes authored with agents and LLMs), establishing human-first documentation, full-diff human accountability, cross-model advisory review, deterministic `clippy.toml` rules (`disallowed-methods`/`disallowed-types`), red-green regression evidence and snapshot inspection discipline.

### Verification

**Reported for 1.1 by its reviser:** the ten Rust examples compiled with Rust 1.85.0, edition 2024, on x86_64-unknown-linux-gnu, with rustfmt, Clippy, execution, tests, doctest, rustdoc and TOML parsing passing.

**Rechecked for 1.2:** the same ten examples, unchanged, were extracted into an isolated workspace with `edition = "2024"` and `rust-version = "1.85"` declared, and checked with **Rust 1.97.0 (stable)** on x86_64-unknown-linux-gnu:

- `cargo fmt --check` with the guide's `rustfmt.toml`: passed.
- Clippy with the small lint baseline and `-D warnings`, including the default `incompatible_msrv` check against 1.85: passed.
- The five executable examples ran successfully.
- Both checked-arithmetic unit tests passed, and an FFI integration test passed five contract-respecting scenarios: ASCII, non-ASCII, null with zero length, null with nonzero length, and an oversized length rejected before dereferencing.
- The `port_parser` doctest compiled and ran; no tests were ignored.
- `cargo doc` with rustdoc warnings denied: passed. All four TOML examples parsed.

Not covered by the 1.2 recheck: a Rust 1.85.0 toolchain itself, Miri (no nightly toolchain was available), other platforms, a foreign-language linker test and the FFI abort path. Projects adopting the guide should run those checks in their own CI.
