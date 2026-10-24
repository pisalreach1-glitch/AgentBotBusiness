from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from google.genai import errors, types

from app.config import Settings
from app.domain import UserError
from app.provider import GeminiProvider


class GeminiAdapterTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return Settings("test-token", "test-key-not-real", "gemini-3.8-flash")

    def client(self, response=None, error=None):
        generate = AsyncMock(return_value=response, side_effect=error)
        aio = SimpleNamespace(models=SimpleNamespace(generate_content=generate), aclose=AsyncMock())
        return SimpleNamespace(aio=aio, close=Mock())

    async def test_real_sdk_client_constructs_and_closes_without_network(self):
        provider = GeminiProvider(self.settings())
        self.assertIsNotNone(provider.client.aio.models)
        await provider.close()

    async def test_async_request_uses_json_schema_and_keeps_thoughts_out_of_answer(self):
        response = types.GenerateContentResponse(
            candidates=[types.Candidate(finish_reason="STOP", content=types.Content(parts=[
                types.Part(text="private model reasoning", thought=True),
                types.Part(text='{"content":"ជំរាបសួរ","tasks":[]}'),
            ]))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=12, candidates_token_count=15, thoughts_token_count=3),
        )
        client = self.client(response)
        with patch("google.genai.Client", return_value=client) as factory:
            provider = GeminiProvider(self.settings())
            reply = await provider.generate("system role", "business JSON")
            kwargs = client.aio.models.generate_content.call_args.kwargs
            self.assertEqual(kwargs["model"], "gemini-3.8-flash")
            self.assertEqual(kwargs["config"].response_mime_type, "application/json")
            self.assertEqual(kwargs["config"].system_instruction, "system role")
            self.assertIn("content", kwargs["config"].response_json_schema["properties"])
            self.assertEqual(factory.call_args.kwargs["http_options"].retry_options.attempts, 1)
            self.assertEqual(reply.stop_reason, "end_turn")
            self.assertNotIn("reasoning", reply.text)
            self.assertEqual((reply.input_tokens, reply.output_tokens), (12, 18))
            await provider.close()
        client.aio.aclose.assert_awaited_once()
        client.close.assert_called_once()

    async def test_max_tokens_maps_to_incomplete_status_contract(self):
        response = types.GenerateContentResponse(candidates=[types.Candidate(
            finish_reason="MAX_TOKENS", content=types.Content(parts=[types.Part(text='{"content":')]))])
        with patch("google.genai.Client", return_value=self.client(response)):
            reply = await GeminiProvider(self.settings()).generate("role", "data")
        self.assertEqual(reply.stop_reason, "max_tokens")

    async def test_safety_block_without_candidate_is_handled(self):
        response = types.GenerateContentResponse(prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY"))
        with patch("google.genai.Client", return_value=self.client(response)):
            reply = await GeminiProvider(self.settings()).generate("role", "data")
        self.assertEqual(reply.stop_reason, "refusal")
        self.assertEqual(reply.text, "")

    async def test_quota_auth_and_model_errors_are_safe_and_not_retried(self):
        for code in (429, 403, 404, 500, 503):
            client = self.client(error=errors.ClientError(code, {"error": {"message": "SECRET_API_KEY", "code": code}}))
            with self.subTest(code=code), patch("google.genai.Client", return_value=client):
                with self.assertRaises(UserError) as caught:
                    await GeminiProvider(self.settings()).generate("role", "data")
                self.assertNotIn("SECRET_API_KEY", str(caught.exception))
                client.aio.models.generate_content.assert_awaited_once()
