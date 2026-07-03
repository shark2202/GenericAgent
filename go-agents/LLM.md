```toml
[models.providers.bbgate]
class_path = "langchain_openai:ChatOpenAI"
api_key_env = "BBGATE_API_KEY"
base_url = "https://bbgate.bytecp.com/v1"
models = [
    "deepseek-v4-pro",
    "z-ai/glm-5.2",
    "z-ai/glm-5.1",
]
```

