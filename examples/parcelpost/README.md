# parcelpost

Shipping quotes for small parcels: zones, weight tariffs, volumetric weight and discounts.

> This is AgentReady's demo repository. It is a small but realistic codebase whose
> documentation has drifted from the code, which is exactly the kind of thing that
> trips up AI coding agents. Don't fix the README: AgentReady should find the problems.

## Quick start

```python
from parcelpost import quote
quote(weight_kg=2.5, dims_cm=(30, 20, 10), origin="MD", dest="RO")
```

## Development

Install the dev tools and run the tests:

```
pip install -r requirements-dev.txt
pytest
```

All pricing rules live in `parcelpost/rates.py`. Discounts are configured in `rules.py`.
