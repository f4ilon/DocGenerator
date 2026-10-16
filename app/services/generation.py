import os

import httpx

from app.models import FunctionEntity


class AIClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    async def generate(
        self, function: FunctionEntity, source: str, style: str = "google"
    ) -> tuple[str, str]:
        if not self.api_key:
            return self._local_draft(function, style), "local-placeholder"

        prompt = (
            f"Write a concise {style}-style documentation block for this function. "
            "Describe behavior and parameters without inventing details.\n\n"
            f"Function: {function.name}\nSource:\n{source[:12000]}"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
        try:
            result = response.json()["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("AI service returned an invalid chat-completion response") from exc
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("AI service returned an empty documentation draft")
        return result.strip(), self.model

    @staticmethod
    def _local_draft(function: FunctionEntity, style: str = "google") -> str:
        args = [name for name in function.params if name != "self"]
        summary = f"Describe the behavior of {function.name.replace('_', ' ')}."
        if style == "jsdoc":
            parameters = "\n".join(f" * @param {{any}} {name} - Describe this parameter." for name in args)
            return f"/**\n * {summary}\n{parameters + chr(10) if parameters else ''} */"
        if style == "numpy":
            parameters = "\n".join(f"{name} : object\n    Describe this parameter." for name in args)
            return f'"""{summary}\n\nParameters\n----------\n{parameters}\n"""'
        if not args:
            return f'"""{summary}"""'
        params = "\n".join(f"        {name}: Describe this parameter." for name in args)
        return f'"""{summary}\n\n    Args:\n{params}\n"""'
