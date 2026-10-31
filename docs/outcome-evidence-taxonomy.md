# Outcome evidence taxonomy v6

The host records a tool invocation and its declared effect separately. Contract
version 6 reflects that distinction:

| Proof kind | What a successful, complete, scoped receipt can establish |
| --- | --- |
| `operation` | The requested tool invocation occurred, including read-only tools. |
| `effect` | A state-changing operation succeeded. Read-only tools cannot establish this. |
| `observation` | The tool returned an actual observation. |
| `reasoning`, `provided_data` | The cited input or supported tool result supports the answer. |
| `artifact` | The requested owned media artifact exists. |
| `delivery` | The media is available in the app session or has a platform send receipt. |

Proof kind alone never establishes semantic completion: the independent review
still checks the requested action, arguments, result and source scope. Failed,
unfinished, approval-waiting, missing, foreign and truncated evidence retain their
existing restrictions. Planning and other harness inspection tools remain excluded
from the operation catalog.

For contracts through v5, `operation` had the stronger state-change meaning.
Normalization conservatively treats that legacy kind as `effect`, including its
independent coverage goals. The stored contract and review are not mutated. The
versioned contract cache ensures new executions derive a v6 contract; saved write
or reservation obligations are never silently downgraded to successful reads.

The contract and independent goal review may choose different compatible proof
kinds. A mismatch is cleared only when the same satisfied requirement cites a
complete, verified receipt supporting both kinds. An unrelated receipt, prose,
pending artifact or successful read cannot bridge an actual effect obligation.

## Regression evidence

Isolated acceptance job `f9cd81175bc7` successfully called `calculate` twice, but
v5 normalization rejected its operation requirement because read receipts exposed
only observation/reasoning. The agent repeated the calculations and still ended
partial. The original report and SQLite database remain unchanged under
`runtime/harness-media-acceptance/1790941468576252000/`.

`outcome-v6-replay.json` in that directory records a read-only replay using the
first two real journal receipts and unchanged original citations/math claims.
The source only persisted the host-normalized verdict, so this is a reconstruction:
the checks are restored to the model's persisted satisfied conclusions, and the
contract is freshly rebound as v6. The replay completes with no repair or coverage
gap. Negative replays preserve the v5 mutation requirement and reject read evidence
for a v6 effect. Hashes confirm the original database and report are unchanged.

`tests/fixtures/outcome_calculator_read_invocation.json` retains the arithmetic-only
original normalized failure and journal records for a portable regression. Tests
also cover generic `api_get` invocation proof, read-versus-effect rejection and
legacy contract compatibility. The focused outcome/evidence/delivery suite passes
107 tests.
