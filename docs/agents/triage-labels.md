# Triage labels

The skills use five canonical triage roles. Apply these labels in this repository's GitHub Issues tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | Maintainer needs to evaluate this issue |
| `needs-info` | `needs-info` | Waiting on reporter for more information |
| `ready-for-agent` | `ready-for-agent` | Fully specified, ready for an AFK agent |
| `ready-for-human` | `ready-for-human` | Requires human implementation |
| `wontfix` | `wontfix` | Will not be actioned |

Create a missing label in this repository on first use:

```bash
gh label create <label> -R aprudkin/tg-jira-tasks --color <hex> --description "..."
```
