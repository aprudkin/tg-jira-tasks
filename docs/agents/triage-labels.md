# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual
label strings used in this repo's issue tracker (the shared aimem repo `aprudkin/aimem`).

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label
string from this table.

These are a **state** dimension, orthogonal to aimem's `type:` / `priority:` / `project:` labels — they
coexist, they don't replace them. See `docs/agents/issue-tracker.md`.

**Note:** these five labels do **not** yet exist in `aprudkin/aimem`. Create them on first triage use
(they're shared across all aimem-tracked repos, so create once):

```bash
gh label create needs-triage    -R aprudkin/aimem --color fbca04 --description "Maintainer needs to evaluate"
gh label create needs-info      -R aprudkin/aimem --color d876e3 --description "Waiting on reporter"
gh label create ready-for-agent -R aprudkin/aimem --color 0e8a16 --description "AFK-ready, agent can pick up"
gh label create ready-for-human -R aprudkin/aimem --color 1d76db --description "Needs human implementation"
gh label create wontfix         -R aprudkin/aimem --color ffffff --description "Will not be actioned"
```

Edit the right-hand column to match whatever vocabulary you actually use.
