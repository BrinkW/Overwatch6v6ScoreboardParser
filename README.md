# Overwatch6v6ScoreboardParser

## Keeping reference data current

Heroes, perks and perk tiers change every patch. After each patch or season launch, run:

```
python tools/sync_reference.py            # report what's new or changed
python tools/sync_reference.py --apply    # download icons + update reference/*.json
```

See [docs/reference-sync.md](docs/reference-sync.md) for what it checks and how to read the report.
