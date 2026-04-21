# Prompt Templates

Domain-specific few-shot examples used by Aether agents. Each subdirectory contains prompt content for one domain.

## Structure

```
prompts/
├── README.md
└── finance/                    # Active domain (default)
    ├── planner_fewshots.txt    # Few-shot example for PlannerAgent
    └── critic_fewshots.txt     # Few-shot example for CriticAgent
```

## Adding a new domain

1. Create a new directory under `prompts/` (e.g., `prompts/legal/`).
2. Add `planner_fewshots.txt` and `critic_fewshots.txt` with domain-appropriate examples matching the JSON schemas the agents expect.
3. Set `PROMPTS_DIR=aether/prompts/legal` in your `.env` or environment.

The engine code is domain-agnostic — only these few-shot files contain domain-specific content.
