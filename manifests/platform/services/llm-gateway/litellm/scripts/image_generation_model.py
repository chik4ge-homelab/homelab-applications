IMAGE_GENERATION_API_BASE = "http://image-generation-api.llm-gateway.svc.cluster.local:8080/v1"
IMAGE_GENERATION_MODEL = "image-generation"


def add_image_generation_model(config: dict) -> None:
    config["model_list"].append(
        {
            "model_name": IMAGE_GENERATION_MODEL,
            "litellm_params": {
                "model": "openai/fake",
                "api_base": IMAGE_GENERATION_API_BASE,
                "api_key": "image-generation-no-auth",
                "timeout": 3600,
            },
        }
    )
    config["general_settings"]["image_generation_model"] = IMAGE_GENERATION_MODEL
