"""Transporte da pesquisa para a Responses API."""

import json
import os
from dataclasses import dataclass

from .generation import GenerationSettings, KnowledgeGenerator


@dataclass(frozen=True)
class OpenAISettings(GenerationSettings):
    model: str = "gpt-6.2-sol"

    @classmethod
    def from_env(cls):
        return cls(enabled=True, api_key=os.getenv("OPENAI_API_KEY", "").strip(),
                   model=os.getenv("OPENAI_MODEL", "gpt-6.2-sol").strip())


def strict_schema(schema):
    if schema["type"] == "object":
        properties = {key: strict_schema(value) for key, value in schema["properties"].items()}
        return {**schema, "properties": properties, "required": list(properties),
                "additionalProperties": False}
    if schema["type"] == "array":
        return {**schema, "items": strict_schema(schema["items"])}
    return dict(schema)


class OpenAIGenerator(KnowledgeGenerator):
    endpoint = "https://api.openai.com/v1/responses"

    def payload(self, name, instruction, context, schema, output_limit):
        body = {
            "model": self.settings.model,
            "instructions": instruction,
            "input": json.dumps(context, ensure_ascii=False),
            "text": {"format": {"type": "json_schema", "name": name, "strict": True,
                                "schema": strict_schema(schema)}},
            "max_output_tokens": output_limit,
            "store": False,
        }
        if self.settings.model.startswith("gpt-6"):
            body["reasoning"] = {"effort": "low"}
        return body

    def count_input(self, payload):
        fields = ("model", "instructions", "input", "text", "reasoning")
        data, error = self.request_json(
            self.endpoint + "/input_tokens", {"Authorization": f"Bearer {self.settings.api_key}"},
            {key: payload[key] for key in fields if key in payload},
        )
        return data.get("input_tokens") if isinstance(data, dict) else None, error

    def complete(self, payload, *, retry=True):
        return self.request_json(self.endpoint, {"Authorization": f"Bearer {self.settings.api_key}"},
                                 payload, retry=retry)

    @staticmethod
    def response_usage(data):
        usage = data.get("usage") or {}
        return {
            "promptTokenCount": usage.get("input_tokens"),
            "candidatesTokenCount": usage.get("output_tokens"),
            "thoughtsTokenCount": (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
            "totalTokenCount": usage.get("total_tokens"),
            "cachedContentTokenCount": (usage.get("input_tokens_details") or {}).get("cached_tokens"),
        }

    @staticmethod
    def response_value(data):
        if not isinstance(data, dict) or data.get("status") != "completed":
            raise ValueError("incomplete OpenAI response")
        messages = [item for item in data["output"] if item.get("type") == "message"]
        if len(messages) != 1 or messages[0].get("role") != "assistant":
            raise ValueError("invalid OpenAI message")
        content = messages[0]["content"]
        if not content or any(part.get("type") != "output_text" for part in content):
            raise ValueError("missing OpenAI text")
        return json.loads("".join(part["text"] for part in content))
