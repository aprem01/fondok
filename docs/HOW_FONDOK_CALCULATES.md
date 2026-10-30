# How Fondok calculates — moved

The methodology is authored once, in **`packages/methodology/`** (FON-120), at
three detail levels, and rendered from there:

| Level | Where |
|---|---|
| conceptual — what drives a number and where it is sourced | `/methodology` (every signed-in user) |
| detailed — formulas, defaults and precedence with values, each citing its source | `/methodology/detail` (administrators; served by `GET /methodology?level=detailed`) |
| engineering — field paths, routes, environment variables | `/admin/methodology-engineering` (diagnostics deployments) |

Atlas (the deal copilot) reads the conceptual and detailed levels of the
sections relevant to a question and cites them as `(methodology, <section id>)`.

Authoring rules, the section file anatomy and the client-copy discipline:
`packages/methodology/README.md`. The standing rule is unchanged: an
underwriting behaviour change ships with its methodology in the same change
set — CI enforces it (`scripts/check_methodology_touched.py`) and the generated
web modules are drift-gated (`scripts/gen_methodology.py --check`).

The former contents of this file (the operating-ratio precedence chain, the
STR basis, the Index Analysis rules, the exit valuation, the Max Price Solver)
are now the `detailed` and `engineering` levels of
`packages/methodology/sections/02-projection.md`, `03-sources.md`,
`04-engines.md` and `05-pricing.md`.
