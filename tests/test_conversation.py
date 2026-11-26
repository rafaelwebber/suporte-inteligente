import hashlib
import json
import tempfile
import unittest
import uuid
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import test_knowledge_search as fixtures
from test_generation import Reply, answer, result
from knowledge_search.config import SearchError
from knowledge_search.conversation import ConversationStore, compact, reply
from knowledge_search.dialogue_context import retrieval_query
from knowledge_search.generation import GenerationSettings, GeminiGenerator, catalogue_source, supporting_sections
from knowledge_search.openai_provider import OpenAIGenerator, OpenAISettings
from knowledge_search.service import ticket_references
from knowledge_search.storage import build


def decision(kind="ask", question="A falha começou depois de uma alteração ou nunca funcionou?"):
    body = answer() if kind == "answer" else {"abstain": True, "paragraphs": []}
    for paragraph in body["paragraphs"]:
        paragraph["citations"] = [{"evidence": "F1:E1"}]
    return {"decision": kind, "question": question if kind == "ask" else "", "options": [], **body}


class JsonReply(Reply):
    def __init__(self, data):
        super().__init__(raw=json.dumps(data).encode())


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.settings = GenerationSettings(True, "fixture-key", "gemini-test")
        self.post = Mock(side_effect=[JsonReply({"totalTokens": 1200}), Reply(decision())])
        self.generator = GeminiGenerator(self.settings, self.post)

    def test_unclassified_history_and_planned_actions_cannot_support_an_answer(self):
        legacy = {"metadata": {"tipo_registro": "caso_ticket"}, "evidence_status": "sem_solucao_identificada"}
        self.assertEqual({}, supporting_sections(legacy, "Solução documentada: Texto dentro do relato."))
        curated = {"metadata": {"tipo_registro": "caso_tecnico"}}
        claim = ("Conhecimento estruturado: A1 | Natureza: acao | Qualificação: relatado "
                 "| Execução: planejada | Enunciado: Verificar o equipamento.")
        self.assertEqual({}, supporting_sections(curated, claim))
        executed = claim.replace("planejada", "realizada_conforme_relato")
        self.assertIn("Conhecimento estruturado", supporting_sections(curated, executed))

    def test_ask_counts_tokens_and_sends_only_three_sources(self):
        data = result(); data["results"] *= 5
        value = self.generator.converse(data, "PLC sem comunicação", [], True)
        self.assertEqual("ask", value["decision"])
        self.assertEqual(1200, value["usage"]["input_tokens"])
        self.assertTrue(self.post.call_args_list[0].args[0].endswith(":countTokens"))
        payload = self.post.call_args_list[1].kwargs["json"]
        context = json.loads(payload["contents"][0]["parts"][0]["text"])
        self.assertEqual(3, len(context["sources"]))
        for forbidden in ("CLIENTE PRIVADO", "private-ticket-id", "arquivo_privado"):
            self.assertNotIn(forbidden, json.dumps(payload))

    def test_context_over_4000_tokens_is_sent_to_generation(self):
        self.post.side_effect = [JsonReply({"totalTokens": 4001}), Reply(decision())]
        value = self.generator.converse(result(), "PLC", [])
        self.assertEqual("clarifying", value["generation_status"])
        self.assertEqual(2, self.post.call_count)

    def test_generation_retries_once_after_google_503(self):
        for final, expected in ((Reply(decision()), "clarifying"), (Reply(status=503), "provider_busy")):
            with self.subTest(expected=expected), patch("knowledge_search.generation.time.sleep") as delay:
                post = Mock(side_effect=[JsonReply({"totalTokens": 1200}), Reply(status=503), final])
                response = GeminiGenerator(self.settings, post).converse(result(), "PLC", [])
                self.assertEqual(expected, response["generation_status"])
                self.assertEqual(3, post.call_count)
                self.assertEqual(post.call_args_list[1].kwargs["json"], post.call_args_list[2].kwargs["json"])
                delay.assert_called_once_with(1)

    def test_remote_diagnosis_is_a_related_case_with_a_question(self):
        data = result()
        symptom = "Foi realizado acesso remoto e foi identificada uma exceção ao abrir a árvore."
        diagnosis = "A consulta demorou a retornar os dados necessários."
        original_failure = "Em razão do atraso, o sistema gerava a exceção."
        data["results"] = []
        data["ticket_histories"] = [{
            "package_id": "fixture", "ticket_reference": "20419", "source_id": "interacao:215807",
            "document_key": "a" * 64, "excerpt": "\n\n".join((symptom, diagnosis, original_failure)),
        }]
        self.post.side_effect = [JsonReply({"totalTokens": 1000}), Reply({
            "decision": "answer", "abstain": False, "question": "", "options": [], "paragraphs": [],
            "historical": [{"ticket": "T1", "problem": "F1:E1", "action": "F1:E1",
                            "diagnosis": "F1:E2", "outcome": "F1:E3", "missing": ""}],
        })]
        value = self.generator.converse(data, "Está aparecendo uma exceção na tela de Rastreabilidade", [])
        self.assertEqual("generated", value["generation_status"])
        self.assertTrue(value["generated_answer"]["related"])
        finding = value["generated_answer"]["paragraphs"][0]["finding"]
        self.assertEqual({"problem": symptom, "diagnosis": diagnosis}, finding["fields"])
        self.assertIn("qual mensagem de erro", value["follow_up"])

    def test_initial_abstention_asks_for_symptom_without_another_provider_call(self):
        self.post.side_effect = [JsonReply({"totalTokens": 1007}), Reply(decision("insufficient"))]
        value = self.generator.converse(result(), "problema com a unity", [])
        self.assertEqual("ask", value["decision"])
        self.assertEqual("clarifying_local", value["generation_status"])
        self.assertIn("O que acontece", value["follow_up"])
        self.assertEqual(2, self.post.call_count)
        self.assertIsNone(value["generated_answer"])

    def test_historical_synthesis_keeps_findings_and_tracks_invalid_citations(self):
        data = {**result(), "results": [], "ticket_histories": [{
            "package_id": "fixture", "ticket_reference": "10387", "source_id": "interacao:102160",
            "document_key": "a" * 64,
            "excerpt": "Foi ajustado o estado do agendamento e a produção seguiu.",
        }]}
        for citation in ("F1:E1", "F1:E9"):
            with self.subTest(citation=citation):
                response = {
                    "decision": "answer", "abstain": False, "question": "", "options": [],
                    "paragraphs": [{"text": "Confira se o estado muda ao iniciar.", "citations": [{"evidence": citation}]}],
                    "historical": [{"ticket": "T1", "problem": "", "diagnosis": "",
                                    "action": "F1:E1", "outcome": "F1:E1", "missing": ""}],
                }
                self.post.side_effect = [JsonReply({"totalTokens": 1000}), Reply(response)]
                value = self.generator.converse(data, "Produção aguardando", [])
                self.assertEqual("generated", value["generation_status"])
                answer = value["generated_answer"]
                if citation == "F1:E1":
                    self.assertEqual("Confira se o estado muda ao iniciar.", answer["paragraphs"][0]["text"])
                    self.assertEqual("10387", answer["historical_findings"][0]["finding"]["ticket"])
                else:
                    self.assertEqual("10387", answer["paragraphs"][0]["finding"]["ticket"])
                    self.assertEqual("invalid_synthesis_citations", value["generation_trace"]["reason"])

    def test_source_backed_choices_are_returned_and_fabricated_choices_rejected(self):
        data = result()
        data["results"].append({**data["results"][0], "document_key": "b" * 64,
                              "excerpt": "Valores congelados durante a operação."})
        value = decision()
        value["options"] = [{"label": "Falha de comunicação", "evidence": "F1:E1"},
                            {"label": "Valores congelados", "evidence": "F2:E1"}]
        self.post.side_effect = [JsonReply({"totalTokens": 200}), Reply(value)]
        output = self.generator.converse(data, "Problema no PLC", [])
        self.assertEqual(2, len(output["clarification_options"]))
        self.assertEqual("a" * 64, output["clarification_options"][0]["document_key"])
        for mutation in ("evidence", "source", "duplicate", "one"):
            invalid = json.loads(json.dumps(value))
            if mutation == "duplicate":
                invalid["options"][1]["label"] = invalid["options"][0]["label"]
            elif mutation == "one":
                invalid["options"] = invalid["options"][:1]
            else:
                invalid["options"][0][mutation] = "inventado sem apoio na fonte"
            post = Mock(side_effect=[JsonReply({"totalTokens": 200}), Reply(invalid)])
            self.assertEqual("rejected", GeminiGenerator(self.settings, post).converse(data, "PLC", [])["generation_status"])

    def test_abstention_after_generic_reply_offers_specific_local_choices(self):
        data = result()
        data["results"] = [{**data["results"][0], "excerpt": "Problema relatado: Alguém relata erro de problema com Unit"},
                           {**data["results"][0], "excerpt": "Problema relatado: A Unit fica travada ao iniciar."},
                           {**data["results"][0], "excerpt": "Problema relatado: Não consegue copiar uma Unit para outra."}]
        self.post.side_effect = [JsonReply({"totalTokens": 100}), Reply(decision("insufficient"))]
        value = self.generator.converse(data, "Relato inicial: problema na Unit. Resposta: erro genérico na Unit", ["Qual o sintoma?"], True)
        self.assertEqual("ask", value["decision"])
        self.assertEqual(2, len(value["clarification_options"]))
        self.assertTrue(all("erro de problema" not in o["label"] for o in value["clarification_options"]))

    def test_open_question_gets_specific_source_options_without_extra_call(self):
        data = result()
        data["results"] = [{**data["results"][0], "excerpt": "Problema relatado: A Unit fica travada ao iniciar."},
                           {**data["results"][0], "excerpt": "Problema relatado: Não consegue copiar uma Unit para outra."}]
        value = self.generator.converse(data, "Problema na Unity", [], True)
        self.assertEqual(2, len(value["clarification_options"]))
        self.assertEqual(2, self.post.call_count)

    def test_local_abstention_choices_are_extracted_from_ticket_symptoms(self):
        data = result()
        data["results"] = [{**data["results"][0], "excerpt": "Problema relatado: A tela fica preta ao iniciar."},
                           {**data["results"][0], "excerpt": "Problema relatado: O login apresenta erro após atualizar."}]
        self.post.side_effect = [JsonReply({"totalTokens": 200}), Reply(decision("insufficient"))]
        value = self.generator.converse(data, "problema com a unity", [])
        self.assertEqual("ask", value["decision"])
        self.assertEqual(2, len(value["clarification_options"]))
        self.assertEqual("A tela fica preta ao iniciar.", value["clarification_options"][0]["label"])

    def test_abstention_after_clarification_or_at_limit_stays_explicit(self):
        for previous, allowed in ((["Qual erro aparece?"], True), ([], False)):
            post = Mock(side_effect=[JsonReply({"totalTokens": 100}), Reply(decision("insufficient"))])
            value = GeminiGenerator(self.settings, post).converse(result(), "Ainda não sei", previous, allowed)
            self.assertEqual("insufficient", value["decision"])
            self.assertEqual("abstained", value["generation_status"])

    def test_answer_preserves_citation_and_usage(self):
        data = result()
        data["results"][0]["excerpt"] = "Solução candidata: Ajustar a configuração de rede após verificar a compatibilidade."
        response = json.loads(Reply(decision("answer")).raw)
        response["usageMetadata"] = {"promptTokenCount": 1200, "totalTokenCount": 1500}
        self.post.side_effect = [JsonReply({"totalTokens": 1200}), JsonReply(response)]
        value = self.generator.converse(data, "PLC", [])
        self.assertEqual("answer", value["decision"])
        self.assertEqual("a" * 64, value["generated_answer"]["paragraphs"][0]["citations"][0]["document_key"])
        self.assertEqual(1500, value["usage"]["totalTokenCount"])
        source = json.loads(self.post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])["sources"][0]
        self.assertNotIn("excerpt", source, "a ficha não é enviada duas vezes")
        self.assertEqual(source["evidence"][0]["text"], value["generated_answer"]["paragraphs"][0]["citations"][0]["quote"])

    def test_request_description_alone_cannot_be_a_final_solution(self):
        data = result()
        data["results"][0]["excerpt"] = "Problema relatado: Solicitação de inclusão de um medidor no supervisório."
        self.post.side_effect = [JsonReply({"totalTokens": 300}), Reply(decision("answer"))]
        value = self.generator.converse(data, "Problema na Unit", [], True)
        self.assertNotEqual("answer", value["decision"])
        self.assertIsNone(value["generated_answer"])

    def test_solution_answer_must_cite_the_action_not_only_the_symptom(self):
        data = result()
        data["results"][0]["excerpt"] = "Problema relatado: Erro de script ao clicar nas Units.\n\nSolução candidata: Alterar o caminho padrão no SCADA, após conferir a configuração."
        for evidence, expected in (("F1:E1", False), ("F1:E2", True)):
            value = decision("answer");value["paragraphs"][0]["citations"] = [{"evidence":evidence}]
            generator = GeminiGenerator(self.settings,Mock(side_effect=[JsonReply({"totalTokens":300}),Reply(value)]))
            actual=generator.converse(data,"Erro de script ao clicar nas Units",[],True)
            self.assertEqual(expected,actual["decision"]=="answer")

    def test_catalogue_chunks_are_literal_bounded_and_keep_final_words(self):
        text = "Solução candidata: " + "palavra " * 50 + "não confirmado"
        catalogue = catalogue_source("F1", text)
        self.assertTrue(all(12 <= len(e["text"]) <= 260 and e["text"] in text for e in catalogue))
        self.assertIn("não confirmado", " ".join(e["text"] for e in catalogue))

    def test_generation_import_does_not_depend_on_flask_import_order(self):
        process = subprocess.run([sys.executable, "-c", "import knowledge_search.generation"], capture_output=True, text=True)
        self.assertEqual(0, process.returncode, process.stderr)

    def test_repeated_question_and_question_limit_are_rejected(self):
        for questions, allowed in (([decision()["question"]], True), ([], False)):
            with self.subTest(allowed=allowed):
                post = Mock(side_effect=[JsonReply({"totalTokens": 1}), Reply(decision())])
                value = GeminiGenerator(self.settings, post).converse(result(), "PLC", questions, allowed)
                self.assertEqual("rejected", value["generation_status"])

    def test_no_sources_and_disabled_never_call_provider(self):
        data = result(); data["results"] = []
        value = self.generator.converse(data, "Falha", [])
        self.assertEqual("ask", value["decision"])
        self.assertEqual("insufficient", self.generator.converse(data, "Falha", [value["follow_up"]])["decision"])
        disabled = GeminiGenerator(replace(self.settings, enabled=False), self.post)
        self.assertEqual("disabled", disabled.converse(result(), "PLC", [])["generation_status"])
        self.post.assert_not_called()

    def test_timeout_and_bad_count_fail_without_generation(self):
        for response in (requests.Timeout(), JsonReply({"totalTokens": True}), JsonReply({}), JsonReply(None)):
            with self.subTest(response=type(response).__name__):
                post = Mock(side_effect=[response])
                value = GeminiGenerator(self.settings, post).converse(result(), "PLC", [])
                self.assertIn(value["generation_status"], ("rejected", "unavailable"))
                self.assertEqual(1, post.call_count)

    def test_fabricated_evidence_and_inconsistent_decision_are_rejected(self):
        cases = [decision("answer"), decision("ask"), decision("insufficient")]
        cases[0]["paragraphs"][0]["citations"][0]["evidence"] = "F99:E999"
        cases[1]["paragraphs"] = answer()["paragraphs"]; cases[1]["abstain"] = False
        cases[2]["question"] = "Uma pergunta indevida?"
        for case in cases:
            post = Mock(side_effect=[JsonReply({"totalTokens": 100}), Reply(case)])
            self.assertEqual("rejected", GeminiGenerator(self.settings, post).converse(result(), "PLC", [])["generation_status"])


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = ConversationStore(Path(self.temp.name) / "sessions.sqlite3")

    def begin(self, owner="alice", session_id=None, request_id=None, message="Falha no PLC"):
        return self.store.begin(owner, session_id, request_id or str(uuid.uuid4()), message)

    def test_owner_isolation_pending_lease_and_idempotency(self):
        request_id = str(uuid.uuid4())
        identifier, turns, _ = self.begin(request_id=request_id)
        with self.assertRaises(SearchError) as caught:
            self.begin(owner="bob", session_id=identifier)
        self.assertEqual(404, caught.exception.status)
        with self.assertRaises(SearchError) as caught:
            self.begin(session_id=identifier)
        self.assertEqual(409, caught.exception.status)
        response = {"follow_up": "Quando começou?", "session_id": identifier}
        self.store.finish("alice", identifier, request_id, turns, response)
        self.assertEqual(response, self.begin(request_id=request_id)[2])
        with self.assertRaises(SearchError):
            self.begin(request_id=request_id, message="Outro problema")
        _, next_turns, _ = self.begin(session_id=identifier, message="Após atualizar")
        self.assertEqual("Quando começou?", next_turns[-1]["asked"])
        self.assertIn("Após atualizar", compact(next_turns))

    def test_invalid_input_masking_and_turn_limit(self):
        for session_id in ([], "", "bad", 123):
            with self.assertRaises(SearchError):
                self.begin(session_id=session_id)
        identifier, turns, _ = self.begin(message="Falha CPF 12345678901 senha=segredo")
        self.assertNotIn("12345678901", turns[0]["message"])
        self.assertNotIn("segredo", turns[0]["message"])
        self.store.finish("alice", identifier, str(uuid.uuid4()), turns * 6, {"follow_up": ""})
        with self.assertRaises(SearchError):
            self.begin(session_id=identifier)

    def test_compact_context_keeps_all_turns_within_budget(self):
        turns = [{"message": f"Fato{i} " + "a" * 1700, "asked": f"Pergunta{i} " + "b" * 200} for i in range(6)]
        value = compact(turns)
        self.assertLessEqual(len(value), 2000)
        for i in range(6):
            self.assertIn(f"Fato{i}", value)

    def test_retrieval_excludes_role_labels_and_normalizes_domain_alias(self):
        turns = [{"message": "problemas com a unity", "asked": ""},
                 {"message": "Erro genérico com Unit relatado", "asked": "Qual erro ocorre com a unidade?"},
                 {"message": "pronlema com a unity", "asked": ""}]
        query = retrieval_query(turns)
        for noise in ("usuário", "usuario", "pergunta", "relato", "unity"):
            self.assertNotIn(noise, query)
        self.assertIn("unit", query)
        confirmed = retrieval_query([turns[0], {"message": "Sim", "asked": "A Unit fica travada ao copiar?"}])
        self.assertIn("copiar", confirmed)

    def test_option_request_does_not_become_search_content(self):
        prompt = "Estou com problema em uma Unit. Quais situações parecidas existem nos tickets? Mostre opções para eu escolher a que mais se parece com o meu caso."
        self.assertEqual("unit",retrieval_query([{"message":prompt,"asked":""}]))
        general = "Estou com um problema no sistema, mas não sei identificar a causa. Pode me mostrar algumas situações para eu escolher a mais parecida?"
        query = retrieval_query([{"message":general,"asked":""}])
        self.assertEqual("",query)
        for word in ("situacao","opcao","escolher","parecida","identificar"):
            self.assertNotIn(word,query)

    def test_session_can_replay_and_continue_after_an_answer(self):
        request_id = str(uuid.uuid4())
        identifier, turns, _ = self.begin(request_id=request_id)
        answer = {"paragraphs": [{"text": "Confira o estado do PLC."}]}
        response = {"decision": "answer", "follow_up": "", "generated_answer": answer}
        self.store.finish("alice", identifier, request_id, turns, response)
        self.assertEqual(response, self.begin(request_id=request_id)[2])
        _, continued, _ = self.begin(session_id=identifier, message="O estado continua o mesmo.")
        self.assertEqual(answer, continued[-1]["prior_answer"])
        self.assertIn("Confira o estado do PLC.", compact(continued))

    def test_ticket_attached_to_an_old_choice_does_not_restrict_retrieval(self):
        turns = [{"message": "Agendamento de produção não inicia.", "asked": ""},
                 {"message": "O teste não aponta erros (Ticket #20896)",
                  "asked": "O teste na tela da fórmula apresenta mensagem?"}]
        self.assertEqual([], ticket_references(turns))
        self.assertNotIn("20896", retrieval_query(turns))
        self.assertNotIn("20896", compact(turns))
        self.assertIn("não aponta erros", compact(turns))
        turns.append({"message": "Compare também com o Ticket #20896", "asked": ""})
        self.assertEqual(["20896"], ticket_references(turns))

    def test_retrieval_drops_explicitly_corrected_components(self):
        turns = [{"message": "Sensor 2 em falha e PLC sem comunicação"},
                 {"message": "Não era o sensor 2, e sim o sensor 3. Não vi alarme."}]
        query = retrieval_query(turns).split()
        self.assertIn("sensor3", query)
        self.assertIn("comunicacao", query)
        self.assertNotIn("sensor2", query)
        self.assertNotIn("alarme", query)

    def test_failure_releases_session_for_retry(self):
        search = Mock(); search.search.side_effect = RuntimeError("fixture failure")
        request_id = str(uuid.uuid4())
        with self.assertRaises(RuntimeError):
            reply(self.store, search, Mock(), "alice", {"message": "Falha no PLC", "request_id": request_id})
        self.assertEqual(1, len(self.begin(request_id=request_id, message="Falha no PLC")[1]))

    def test_retry_after_technical_failure_preserves_original_turn(self):
        request_id = str(uuid.uuid4())
        identifier, turns, _ = self.begin(request_id=request_id)
        self.store.finish("alice", identifier, request_id, turns, {"generation_status":"rejected"})
        _, retried, _ = self.begin(session_id=identifier, message="Falha no PLC")
        self.assertEqual(turns, retried)
        self.store.release("alice", identifier)
        _, next_turn, _ = self.begin(session_id=identifier, message="Outro detalhe do problema")
        self.assertEqual(2, len(next_turn))


class ConversationApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.KnowledgeApiTests.setUp(self)
        self.app.config["KNOWLEDGE_CONVERSATION_DATABASE"] = self.root / "conversations.sqlite3"
        self.fake = Mock()
        self.fake.settings.status.return_value = "ready"
        self.fake.converse.return_value = {"decision": "ask", "follow_up": "Quando a falha começou?",
            "generated_answer": None, "warnings": [], "generation_status": "clarifying", "usage": {}}
        self.app.extensions["knowledge_generator"] = self.fake

    @staticmethod
    def record_of_kind(identifier, kind, data):
        entry = fixtures.record(identifier, "", "")
        payload = json.loads(entry["payload"])
        payload.update(tipo=kind, dados=data)
        entry["payload"] = json.dumps(payload)
        entry["fingerprint"] = hashlib.sha256(entry["payload"].encode()).hexdigest()
        return entry

    def test_conversation_refines_query_and_retry_does_not_generate_twice(self):
        request_id = str(uuid.uuid4())
        data = {"request_id": request_id, "message": "PLC sem comunicação"}
        value = self.client.post("/knowledge/conversation", json=data, headers=self.headers)
        self.assertEqual(200, value.status_code)
        self.assertEqual("ask", value.json["decision"])
        self.assertNotIn("generation_trace", value.json)
        self.assertEqual(200, self.client.post("/knowledge/conversation", json=data, headers=self.headers).status_code)
        self.assertEqual(1, self.fake.converse.call_count)
        data.update(session_id=value.json["session_id"], request_id=str(uuid.uuid4()), message="Após atualização")
        value = self.client.post("/knowledge/conversation", json=data, headers=self.headers)
        self.assertEqual(2, value.json["turn"])
        context = self.fake.converse.call_args.args[1]
        self.assertIn("PLC", context)
        self.assertIn("Após atualização", context)
        self.assertEqual(404, self.client.post("/knowledge/conversation", json={**data,"request_id":str(uuid.uuid4())}, headers={"Authorization":"Bearer bob"}).status_code)

    def test_auth_validation_and_no_database_exposure(self):
        self.assertEqual(401, self.client.post("/knowledge/conversation", json={}).status_code)
        for data in ([], {}, {"message":"Falha","request_id":str(uuid.uuid4()),"sources":[]}):
            self.assertEqual(400, self.client.post("/knowledge/conversation", json=data, headers=self.headers).status_code)
        self.assertEqual(404, self.client.get("/instance/conversations.sqlite3").status_code)

    def test_conversation_builds_reuses_and_refreshes_case_cards(self):
        ticket, reference = "18929", "199738"
        action = "Foi ajustado o estado da janela de agendamento."
        outcome = "A produção iniciou normalmente."

        def update_source(snapshot, text):
            fixtures.source_fixture(self.settings.source, [
                fixtures.record(f"caso_ticket:{ticket}", "Agendamento de produção", "A próxima batelada não iniciou.", ticket=ticket),
                self.record_of_kind(f"interacao:{reference}", "interacao",
                                    {"ticket": ticket, "interacao": reference, "texto": text}),
            ], snapshot)
            build(self.settings)

        classifications, seen_status = [], []

        def provider(url, **kwargs):
            if url.endswith("/input_tokens"):
                return JsonReply({"input_tokens": 800})
            body = kwargs["json"]
            context = json.loads(body["input"])
            if body["text"]["format"]["name"] == "knowledge_case_cards":
                self.assertNotIn(reference, body["input"])
                case = context["case_material"][0]
                interaction = next(p for p in case["passages"] if p["type"] == "interacao")
                classifications.append(interaction["text"])
                findings = [{"category": "acao_executada", "source": interaction["id"],
                             "quote": action, "action_source": ""}]
                findings.append({"category": "resultado", "source": interaction["id"],
                                 "quote": outcome, "action_source": interaction["id"]})
                value = {"cases": [{"id": case["id"], "findings": findings}]}
            else:
                case = context["case_cards"][0]
                seen_status.append(case["outcome_status"])
                evidence = case["findings"][0]["evidence"]
                value = {"decision": "related", "question": "", "abstain": False, "options": [], "historical": [],
                         "paragraphs": [{"text": "O histórico registra um ajuste.", "citations": [{"evidence": evidence}]}]}
            return JsonReply({"status": "completed", "output": [
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(value)}]},
            ]})

        self.app.config.update(KNOWLEDGE_LLM_PROVIDER="openai", OPENAI_SETTINGS=OpenAISettings(enabled=True, api_key="fixture"))
        self.app.extensions["knowledge_generator"] = OpenAIGenerator(self.app.config["OPENAI_SETTINGS"], provider)
        update_source("case-v1", f"{action}\n{outcome}")
        for attempt in range(3):
            if attempt == 2:
                update_source("case-v2", f"{action}\nAguardando retorno da operação.")
            response = self.client.post("/knowledge/conversation", headers=self.headers, json={
                "message": f"O que aconteceu no ticket #{ticket}?", "request_id": str(uuid.uuid4()),
            })
            self.assertEqual(200, response.status_code)
            self.assertEqual("generated", response.json["generation_status"])
            citation = response.json["generated_answer"]["paragraphs"][0]["citations"][0]
            self.assertEqual(f"interacao:{reference}", citation["source_id"])
            self.assertEqual(action, citation["quote"])
        self.assertEqual(2, len(classifications))
        self.assertEqual(["registrado_no_historico", "registrado_no_historico", "nao_confirmado_nos_trechos"], seen_status)

    def test_named_tickets_are_investigated_in_their_interactions(self):
        cases = [
            ("13576", "145246", "Alterado um status no banco de dados.",
             "Foi possível abrir a tela do Cancela Passo."),
            ("15371", "162807", "O status preso foi removido.",
             "A produção voltou a funcionar."),
        ]
        records = []
        for ticket, interaction, action, outcome in cases:
            records.append(fixtures.record(f"caso_ticket:{ticket}", "Cancela Passo", "A janela não abre.", ticket=ticket))
            records.append(self.record_of_kind(f"interacao:{interaction}", "interacao", {
                "ticket": ticket, "interacao": interaction, "texto": f"{action}\n{outcome}",
            }))
        fixtures.source_fixture(self.settings.source, records, "s2")
        build(self.settings)

        cross_ticket_citation = False

        def provider(url, **kwargs):
            if url.endswith(":countTokens"):
                return JsonReply({"totalTokens": 1600})
            context = json.loads(kwargs["json"]["contents"][0]["parts"][0]["text"])
            self.assertEqual(["13576", "15371"], context["explicit_tickets"])
            self.assertFalse(context["allow_questions"])
            findings = []
            for source in context["sources"]:
                action, outcome = source["evidence"]
                findings.append({"ticket": source["ticket"], "problem": "", "diagnosis": "",
                                 "action": action["id"], "outcome": outcome["id"], "missing": ""})
            self.assertEqual(2, len(findings))
            if cross_ticket_citation:
                findings[0]["action"] = findings[1]["action"]
            return Reply({"decision": "answer", "question": "", "abstain": False, "paragraphs": [],
                          "options": [], "historical": findings})

        first = self.client.post("/knowledge/conversation", json={
            "message": "O botão Cancela Passo está habilitado, mas a janela não abre.",
            "request_id": str(uuid.uuid4()),
        }, headers=self.headers).json
        self.app.extensions["knowledge_generator"] = GeminiGenerator(
            GenerationSettings(True, "fixture-key", "gemini-test"), post=provider)
        response = self.client.post("/knowledge/conversation", json={
            "message": "Os tickets #13576 e #15371 são parecidos. O que foi feito?",
            "request_id": str(uuid.uuid4()), "session_id": first["session_id"],
        }, headers=self.headers)
        self.assertEqual(200, response.status_code)
        payload = response.json
        self.assertEqual("answer", payload["decision"])
        self.assertEqual(["13576", "15371"], [item["ticket_reference"] for item in payload["results"]])
        self.assertTrue(payload["generated_answer"]["historical"])
        for paragraph, (ticket, interaction, action, outcome) in zip(payload["generated_answer"]["paragraphs"], cases, strict=True):
            self.assertIn(action, paragraph["text"])
            self.assertIn(outcome, paragraph["text"])
            for citation in paragraph["citations"]:
                self.assertEqual(ticket, citation["ticket_reference"])
                self.assertEqual("interacao:" + interaction, citation["source_id"])
                document = self.client.get("/knowledge/documents/" + citation["document_key"], headers=self.headers)
                self.assertEqual(200, document.status_code)
                self.assertIn(citation["quote"], document.json["body"])

        cross_ticket_citation = True
        follow_up = self.client.post("/knowledge/conversation", json={
            "message": "Qual foi o resultado?", "request_id": str(uuid.uuid4()),
            "session_id": first["session_id"],
        }, headers=self.headers)
        self.assertEqual(200, follow_up.status_code)
        self.assertEqual("rejected", follow_up.json["generation_status"])
        self.assertTrue(follow_up.json["messages"][-1]["prior_answer"]["historical"])

    def test_triage_considers_all_cards_and_opens_the_selected_sources(self):
        records = []
        for ticket in range(101, 112):
            records.append(fixtures.record(f"caso_ticket:{ticket}", f"Rastreabilidade caso {ticket}",
                                           "A janela de rastreabilidade não abre.", ticket=str(ticket)))
            records.append(self.record_of_kind(f"interacao:{ticket + 500}", "interacao", {
                "ticket": str(ticket), "interacao": str(ticket + 500),
                "texto": "Alterado o filtro de teste da listagem.\nFoi possível abrir a janela em seguida.",
            }))
        for index in range(1, 7):
            records.append(self.record_of_kind(f"artigo:{index}", "artigo", {
                "titulo": f"Rastreabilidade manual {index}",
                "texto": f"Documentação técnica de Rastreabilidade para o módulo demonstrativo {index}.",
            }))
        fixtures.source_fixture(self.settings.source, records, "s2")
        build(self.settings)
        picked = {}
        mode = "historical"

        def provider(url, **kwargs):
            if url.endswith(":countTokens"):
                return JsonReply({"totalTokens": 1900})
            context = json.loads(kwargs["json"]["contents"][0]["parts"][0]["text"])
            if "candidates" in context:
                cards = context["candidates"]
                tickets = [card for card in cards if " caso " in card["title"]]
                articles = [card for card in cards if " manual " in card["title"]]
                self.assertEqual((17, 11, 6), (len(cards), len(tickets), len(articles)))
                self.assertNotIn("Alterado o filtro", json.dumps(context))
                picked["ticket"] = tickets[-1]["title"].split()[-1]
                picked["article"] = articles[-1]["title"].split()[-1]
                if mode == "invalid":
                    return Reply({"selected": ["C999"]})
                return Reply({"selected": [tickets[-1]["id"], articles[-1]["id"]]})

            if mode == "invalid":
                return Reply(decision("insufficient"))
            sources = context["sources"]
            history = [source for source in sources if source.get("ticket")]
            self.assertEqual([picked["ticket"]], [source["ticket_number"] for source in history])
            article = next(source for source in sources if any(
                f"módulo demonstrativo {picked['article']}" in e["text"] for e in source["evidence"]
            ))
            action, outcome = history[0]["evidence"]
            if mode == "related":
                return Reply({"decision": "related", "question": "", "abstain": False, "options": [],
                              "paragraphs": [{"text": "A referência descreve o mesmo módulo. Falta saber a versão.",
                                              "citations": [{"evidence": article["evidence"][0]["id"]}]}]})
            return Reply({"decision": "answer", "question": "", "abstain": False, "paragraphs": [],
                          "options": [], "historical": [{"ticket": history[0]["ticket"], "action": action["id"],
                            "outcome": outcome["id"], "problem": "", "diagnosis": "", "missing": ""}]})

        self.app.extensions["knowledge_generator"] = GeminiGenerator(
            GenerationSettings(True, "fixture-key", "gemini-test"), post=provider)
        for mode in ("historical", "related", "invalid"):
            with self.subTest(mode=mode):
                response = self.client.post("/knowledge/conversation", json={
                    "message": "A janela de Rastreabilidade não abre.", "request_id": str(uuid.uuid4()),
                }, headers=self.headers)
                self.assertEqual(200, response.status_code)
                data = response.json
                self.assertEqual(17, len(data["results"]))
                if mode == "invalid":
                    self.assertEqual("rejected", data["triage"]["status"])
                    self.assertTrue(any("seleção local" in warning for warning in data["warnings"]))
                    continue
                self.assertEqual({"status": "selected", "candidates": 17, "selected": 2}, data["triage"])
                answer = data["generated_answer"]
                self.assertTrue(answer[mode])
                citation = answer["paragraphs"][0]["citations"][0]
                expected = f"interacao:{int(picked['ticket']) + 500}" if mode == "historical" else f"artigo:{picked['article']}"
                self.assertEqual(expected, citation["source_id"])
                source = self.client.get("/knowledge/documents/" + citation["document_key"], headers=self.headers)
                self.assertIn(citation["quote"], source.json["body"])

    def test_solution_at_end_of_long_document_reaches_generation(self):
        records = [fixtures.record("long", "Falha PLC", "Sintoma sem ação. " * 300,
            solucao_documentada="Ajustar configuração de rede conforme procedimento validado.",
            resultado_documentado="Comunicação restabelecida no caso histórico.",
            aplicabilidade_ao_caso_atual="Conferir versão antes de aplicar.")]
        fixtures.source_fixture(self.settings.source, records, "s2")
        build(self.settings)
        response = self.client.post("/knowledge/conversation", json={"message":"Falha PLC","request_id":str(uuid.uuid4())}, headers=self.headers)
        self.assertEqual(200, response.status_code)
        evidence = self.fake.converse.call_args.args[0]["results"][0]["excerpt"]
        for text in ("Ajustar configuração", "Comunicação restabelecida", "Conferir versão"):
            self.assertIn(text, evidence)

    def test_concrete_symptoms_precede_generic_records_and_history_labels_are_not_searched(self):
        records = [fixtures.record("generic", "Problema com Unit", "Pessoa relata erro de problema com Unit"),
                   fixtures.record("locked", "Unit travada", "A Unit fica travada ao iniciar."),
                   fixtures.record("copy", "Copiar Unit", "Não consegue copiar uma Unit para outra.")]
        fixtures.source_fixture(self.settings.source, records, "s2")
        build(self.settings)
        found = self.service.search("unit", 20)
        evidence = self.service.evidence(found)
        ids = [item["source_id"] for item in evidence["results"]]
        self.assertEqual({"locked", "copy"}, set(ids[:2]))
        with patch.object(self.service, "search", wraps=self.service.search) as search:
            value = self.client.post("/knowledge/conversation", json={"message":"problemas com a unity","request_id":str(uuid.uuid4())}, headers=self.headers)
            self.assertEqual(200, value.status_code)
            self.assertEqual("unit", search.call_args.args[0])
            self.assertEqual(20, search.call_args.args[1])

    def test_unit_request_filters_unrelated_proposals_and_candidates_outside_topic(self):
        records=[fixtures.record("proposal","Proposta de automação - Unit","Solicitação de engenharia para inclusão de medidor de vazão.",solucao_candidata="Executar projeto de inclusão de medidor."),
                 fixtures.record("unrelated","Falha no sensor","Sensor apresenta leitura incorreta.",solucao_candidata="Trocar sensor após verificar a leitura."),
                 fixtures.record("locked","Unit travada","A Unit fica travada ao iniciar."),
                 fixtures.record("copy","Copiar Unit","A Unit não copia para a próxima etapa.")]
        fixtures.source_fixture(self.settings.source,records,"s2");build(self.settings)
        found=self.service.search("unit sensor",20)
        found["topic_terms"]=["unit"]
        selected=self.service.evidence(found)
        self.assertEqual({"locked","copy"},{r["source_id"] for r in selected["results"]})
        found["query"]="proposta unit medidor"
        self.assertIn("proposal",{r["source_id"] for r in self.service.evidence(found)["results"]})

    def test_completely_general_prompt_does_not_search_instruction_words(self):
        with patch.object(self.service,"search",wraps=self.service.search) as search:
            value=self.client.post('/knowledge/conversation',json={"message":"Estou com um problema no sistema. Mostre opções para escolher.","request_id":str(uuid.uuid4())},headers=self.headers)
            self.assertEqual(200,value.status_code)
            search.assert_not_called()
            self.assertEqual([],value.json['results'])


if __name__ == "__main__":
    unittest.main()
