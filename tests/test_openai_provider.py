import json
import unittest
from unittest.mock import Mock, patch

from knowledge_search.openai_provider import OpenAIGenerator, OpenAISettings
from test_conversation import JsonReply
from test_generation import result
import test_knowledge_search as fixtures


def openai_reply(value):
    return JsonReply({
        "status": "completed",
        "output": [
            {"type": "reasoning"},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": json.dumps(value)}]},
        ],
        "usage": {"input_tokens": 870, "output_tokens": 52, "total_tokens": 922},
    })


class OpenAIProviderTests(unittest.TestCase):
    def test_shortlist_and_conversation_preserve_literal_citations(self):
        settings = OpenAISettings(enabled=True, api_key="test-openai-key")
        base = result()
        first = base["results"][0]
        next_card = {**first, "document_key": "b" * 64, "source_id": "artigo:fixture",
                     "excerpt": "Conteúdo documental: O procedimento registra a verificação da pressão.",
                     "metadata": {"tipo_registro": "artigo"}}
        post = Mock(side_effect=[
            JsonReply({"input_tokens": 500}), openai_reply({"selected": ["C2"]}),
            JsonReply({"input_tokens": 870}), openai_reply({
                "decision": "answer", "question": "", "options": [], "abstain": False,
                "paragraphs": [{"text": "O procedimento registra a verificação da pressão.",
                                "citations": [{"evidence": "F1:E1"}]}],
            }),
        ])
        provider = OpenAIGenerator(settings, post)
        self.assertEqual(([next_card["document_key"]], "selected"),
                         provider.shortlist([first, next_card], "Como verificar a pressão?"))
        reply = provider.converse({**base, "results": [next_card]}, "Como verificar a pressão?", [], False)
        self.assertEqual("generated", reply["generation_status"])
        self.assertEqual(922, reply["usage"]["totalTokenCount"])
        citation = reply["generated_answer"]["paragraphs"][0]["citations"][0]
        self.assertEqual(next_card["document_key"], citation["document_key"])
        self.assertIn(citation["quote"], next_card["excerpt"])
        endpoint = "https://api.openai.com/v1/responses"
        self.assertEqual([endpoint + "/input_tokens", endpoint] * 2, [call.args[0] for call in post.call_args_list])
        for call in post.call_args_list:
            self.assertEqual("Bearer " + settings.api_key, call.kwargs["headers"]["Authorization"])
            self.assertNotIn(settings.api_key, json.dumps(call.kwargs["json"]))
            self.assertFalse(call.kwargs["allow_redirects"])
        body = post.call_args_list[-1].kwargs["json"]
        self.assertEqual("gpt-6.2-sol", body["model"])
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        schema = body["text"]["format"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), set(schema["required"]))

    def test_incomplete_and_refused_responses_keep_local_results(self):
        refusal = {"status": "completed", "output": [{"type": "message", "role": "assistant",
                    "content": [{"type": "refusal", "refusal": "Sample refusal"}]}]}
        for value in (refusal, {"status": "incomplete", "output": []}):
            with self.subTest(value=value):
                post = Mock(side_effect=[JsonReply({"input_tokens": 400}), JsonReply(value)])
                generator = OpenAIGenerator(OpenAISettings(enabled=True, api_key="fixture"), post)
                response = generator.converse(result(), "Falha de comunicação", [])
                self.assertEqual("rejected", response["generation_status"])
                self.assertIsNone(response["generated_answer"])


class OpenAIConfigTests(unittest.TestCase):
    def setUp(self):
        fixtures.KnowledgeApiTests.setUp(self)
        self.app.config.update(KNOWLEDGE_LLM_PROVIDER="openai",
                               OPENAI_SETTINGS=OpenAISettings(enabled=True))

    def test_selection_without_key_keeps_search_available(self):
        with patch("knowledge_search.generation.requests.post") as post:
            status = self.client.get("/knowledge/status", headers=self.headers).json
            self.assertEqual("openai", status["generation_provider"])
            self.assertEqual("not_configured", status["generation_status"])
            search = self.client.post("/knowledge/search", json={"query": "redler"}, headers=self.headers).json
            self.assertFalse(search["generation_available"])
            self.assertTrue(search["results"])
            answer = self.client.post("/knowledge/answer", json={"query": "redler"}, headers=self.headers).json
            self.assertEqual("not_configured", answer["generation_status"])
            post.assert_not_called()

    def test_conversation_routes_to_openai_when_configured(self):
        self.app.config["OPENAI_SETTINGS"] = OpenAISettings(enabled=True, api_key="fixture")
        reply = openai_reply({"decision": "ask", "abstain": True, "paragraphs": [], "options": [],
                             "question": "Em que momento a velocidade é interrompida?"})
        with patch("knowledge_search.generation.requests.post",
                   side_effect=[JsonReply({"input_tokens": 400}), reply]) as post:
            data = self.client.post("/knowledge/conversation", headers=self.headers, json={
                "message": "redler", "request_id": "67b23bcf-0c8f-431f-9e49-6a8e34cff2f3",
            }).json
            self.assertEqual("openai", data["generation_provider"])
            self.assertEqual("clarifying", data["generation_status"])
            for call in post.call_args_list:
                self.assertTrue(call.args[0].startswith("https://api.openai.com/v1/responses"))
