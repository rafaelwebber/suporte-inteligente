"""Texto para recuperação separado do histórico enviado ao modelo."""
import re

from api.text_utils import keywords, normalize

# Termos que não distinguem sintomas. Não alteram o índice nem a busca antiga.
GENERIC = set(keywords("problema problemas erro erros falha falhas generico generica relatado relatada "
                       "relata relatando informado informada informa informando apresenta apresentando "
                       "apresentado apresentada ocorre ocorrendo especifico especifica sistema "
                       "senhor senhora sr sra contato telefone ligou chamado ticket entrou unidade unit units unity"))

# Instruções de conversa não descrevem o equipamento nem o sintoma pesquisado.
CONVERSATIONAL = set(keywords("estou estamos tenho tendo preciso gostaria ajuda ajudar identificar causa "
                             "mostre mostrar sugira sugerir opcoes alternativas escolher escolha situacoes "
                             "parecido parecida parecidos parecidas semelhante semelhantes parece caso casos "
                             "existe existem algum alguma alguns algumas anterior anteriores aquele atendimento "
                             "feito fazer realizado registrada registrado conhecido resultado resposta responder "
                             "pergunta detalhe detalhes descrever explicar entendi saber consiga consegue poderia "
                             "pode podem sei sabemos quero quer queria conseguir tentando verifiquei conferi"))

# Componentes deste acervo. São restrições locais, nunca fatos inferidos para a resposta.
TOPICS = {"unit", "plc", "clp", "scada", "supervisorio", "servidor", "viewer", "redler",
          "misturador", "dosagem", "balanca", "sensor", "impressora", "login"}


def topic_terms(turns):
    message = re.sub(r"\bunity\b", "unit", turns[0]["message"], flags=re.I)
    return sorted(set(keywords(message)) & TOPICS)


def technical_terms(text):
    return {term for term in keywords(text, limit=1000) if term not in GENERIC and not term.isdigit()}


def informative_option(label, history=""):
    # Uma alternativa precisa acrescentar algo ao componente já informado.
    return bool(technical_terms(label) - set(keywords(history, limit=1000)))


def case_observation(turn):
    """Recupera o texto de opções antigas que anexavam a fonte à resposta."""
    message = turn["message"]
    if turn.get("asked"):
        return re.sub(r"(?<=\S)[ \t]+\(\s*ticket\s+#\d+\s*\)\s*$", "", message, flags=re.I)
    return message


def retrieval_query(turns):
    parts, excluded = [], set()
    for turn in turns:
        message = normalize(case_observation(turn))
        answer = message.strip(" .!?")
        if answer in ("sim", "nao") and turn.get("asked"):
            # Uma pergunta negada não descreve um sintoma confirmado.
            if answer == "nao":
                continue
            message = normalize(turn["asked"])
        message = re.sub(r"\bunity\b", "unit", message)
        def with_identifier(match):
            label, number = match.groups()
            if label == "unit":
                return label + number
            if label not in GENERIC | CONVERSATIONAL and keywords(label):
                return f"{label} {number} {label}{number}"
            return match.group()

        message = re.sub(r"\b([a-z]{3,})\s+(\d{1,3})\b", with_identifier, message)
        message = re.sub(r"\b(?:na verdade|corrigindo|se (?:vc|voce) quis dizer)\b", ",", message)
        clauses = re.split(r"[,;!?]|\.(?=\s|$)|\n|\b(?:mas|porem|e sim)\b", message)
        for clause in clauses:
            # Ausência ou correção explícita. Falhas como "não inicia" continuam pesquisáveis.
            absent = re.search(
                r"\bnao (?:ha|tem|existe|houve|era|vi|apareceu|e(?!\s+possivel\b)|"
                r"(?:aponta|apresenta|exibe|mostra)(?=\s+(?:erros?|alarmes?|mensage)))\s+(.+)", clause,
            )
            if absent:
                excluded.update(keywords(absent.group(1)))
                clause = clause[:absent.start()]
            terms = [term for term in keywords(clause)
                     if term not in CONVERSATIONAL and (term not in GENERIC or term in TOPICS)]
            excluded.difference_update(terms)
            parts.append(terms)
    return " ".join(dict.fromkeys(term for part in parts for term in part if term not in excluded))[:1800]


def matching_ticket_cards(cards, query):
    """Protege correspondências claras de função e sintoma na seleção para leitura."""
    terms = technical_terms(query)
    matches = []
    for card in cards:
        if not card.get("ticket_reference"):
            continue
        title_terms = technical_terms(card["title"])
        common = len(terms & title_terms)
        if common >= 3 and common * 2 >= len(title_terms):
            matches.append((common, common / len(title_terms), card))
    matches.sort(key=lambda match: match[:2], reverse=True)
    return [card for _, _, card in matches[:2]]


def symptom_text(text):
    """Retira aberturas de contato sem transformar o relato técnico em fato novo."""
    text = re.split(r"\n\s*\d{1,2}:\d{2}", text, maxsplit=1)[0].strip()
    match = re.search(r"\b(?:relatando|informando|relata|informa|informou|relatou|diz|diiz)\b\s*(?:que\s+)?", text[:250], re.I)
    return text[match.end():].strip() if match else text


def projected_sections(text):
    from .documents import FIELDS
    labels = {label for _, label in FIELDS}
    sections, current = {}, None
    for paragraph in text.split("\n\n"):
        label, separator, value = paragraph.partition(": ")
        if separator and label in labels:
            current = label
            sections[current] = value
        elif current:
            sections[current] += "\n\n" + paragraph
    return sections
