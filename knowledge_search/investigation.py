"""Seleciona trechos de interações para relatar o que ocorreu em cada atendimento."""

import re

ACTION = re.compile(
    r"\b(?:alterad[oa]s?|ajustad[oa]s?|corrigid[oa]s?|removid[oa]s?|"
    r"atualizad[oa]s?|reiniciad[oa]s?|trocad[oa]s?|reconfigurad[oa]s?|"
    r"aplicad[oa]s?|implantad[oa]s?|reprocessad[oa]s?|substituid[oa]s?|"
    r"alteramos|ajustamos|corrigimos|removemos|atualizamos|reiniciamos|"
    r"trocamos|configuramos|alterou|ajustou|corrigiu|removeu|atualizou|reiniciou|modificou|"
    r"(?:realizad[oa]|executad[oa]|efetuad[oa]|feit[oa]) (?:um |uma |o |a )?"
    r"(?:ajuste|correcao|alteracao|atualizacao|reinicio|troca|remocao))\b"
)
OUTCOME = re.compile(
    r"\b(?:foi possivel (?:abrir|seguir|continuar|concluir|retomar|salvar|carregar|processar|dar inicio)|"
    r"voltou a (?:abrir|funcionar|iniciar|processar|responder|operar)|normalizou|"
    r"producao (?:seguiu|retomou|voltou ao normal|iniciou normalmente)|"
    r"restabelecid[oa]|funcionou|resolvid[oa]|sem (?:novos erros|nova excecao))\b"
)
UNCERTAIN = re.compile(r"\b(?:nao (?:foi|chegou|sera)|sera|seria|deve ser|a ser|"
                       r"pode ser|precisa ser|solicitad[oa]|proposta|sugestao|se for|planejad[oa])\b")
COMPLETED = re.compile(r"\b(?:ao|apos|depois de|tivemos que|foi necessario|foi preciso)\s+"
                       r"(?:alterar|ajustar|corrigir|modificar|remover|atualizar|reiniciar|executar|trocar|configurar|fechar)\b")


def recorded_action(text):
    from api.text_utils import normalize

    text = normalize(text)
    return not UNCERTAIN.search(text) and bool(
        ACTION.search(text) or (COMPLETED.search(text) and OUTCOME.search(text))
    )


def recorded_outcome(text):
    from api.text_utils import normalize

    text = normalize(text)
    return bool(OUTCOME.search(text)) and not UNCERTAIN.search(text) and not re.search(
        r"\bnao (?:funcionou|normalizou|voltou|seguiu|retomou)\b", text
    )


def history_excerpt(text, query):
    from api.text_utils import keywords

    if not text:
        return "", 0
    terms = set(keywords(query))
    lines = re.split(r"\r?\n+|(?<=[.!?])\s+(?=[A-ZÁ-Ú])", text)
    lines = [line.strip() for line in lines if line.strip()]
    if not lines:
        return "", 0
    best = max(range(len(lines)), key=lambda i: (
        3 * recorded_action(lines[i]) + 2 * recorded_outcome(lines[i]),
        len(terms & set(keywords(lines[i]))),
    ))
    neighborhood = lines if len(text) <= 600 else lines[max(0, best - 1):best + 3]
    # Se uma interação for longa, conserva a passagem de interesse e seu contexto.
    if sum(len(line) for line in neighborhood) > 680:
        neighborhood = [lines[best]]
    passage = "\n\n".join(neighborhood)[:700]
    score = (4 * any(recorded_action(line) for line in neighborhood)
             + 3 * any(recorded_outcome(line) for line in neighborhood)
             + len(terms & set(keywords(passage))) / max(1, len(terms)))
    return passage, score
