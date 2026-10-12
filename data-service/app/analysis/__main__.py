"""python -m app.analysis  -> refresh customer segments"""
import json

from app.analysis.segmentation import run

print(json.dumps(run(), indent=2, default=str))
