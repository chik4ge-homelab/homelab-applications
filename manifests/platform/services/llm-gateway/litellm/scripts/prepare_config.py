import json
import os
import sys

from discover_models import CONFIG_PATH, build_config, discover_models
from image_generation_model import add_image_generation_model


def main() -> None:

    config = build_config(discover_models())
    add_image_generation_model(config)
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, separators=(",", ":"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("failed to prepare LiteLLM configuration", file=sys.stderr)
        raise SystemExit(1)
