import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openclaw_model_bridge import create_model_bridge
import uvicorn


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    saved = json.loads(args.config.read_text(encoding='utf-8'))
    uvicorn.run(create_model_bridge(args.config), host='127.0.0.1', port=int(saved['port']), log_level='warning')
