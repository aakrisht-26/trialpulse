"""Step 2 feasibility spike.

This package is the only place allowed to call ClinicalTrials.gov's undocumented
internal history endpoint (history_api.py), and only for small verification
samples. No module outside this package may import from it; a test enforces that.
"""
