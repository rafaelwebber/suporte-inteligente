import re
import unicodedata

# Ordem importa: CNPJ antes de CPF e cartão antes de telefone, para o padrão mais longo vencer.
_PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[CNPJ]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[CPF]"),
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), "[CARTAO]"),
    (re.compile(r"(?:\+?55\s?)?\(?\b\d{2}\)?\s?9?\d{4}[-\s]?\d{4}\b"), "[TELEFONE]"),
]

STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das", "em", "no", "na",
    "nos", "nas", "por", "pelo", "pela", "pelos", "pelas", "para", "pra", "pro", "com", "sem", "sob",
    "sobre", "e", "ou", "mas", "que", "se", "nao", "sim", "ja", "mais", "menos", "muito", "pouco",
    "ao", "aos", "esta", "este", "isto", "essa", "esse", "isso", "aquele", "aquela", "aquilo", "ele",
    "ela", "eles", "elas", "eu", "voce", "voces", "nos", "meu", "minha", "seu", "sua", "seus", "suas",
    "lhe", "me", "te", "foi", "ser", "estar", "esta", "estao", "tem", "ter", "tinha", "ha", "fica",
    "ficou", "quando", "como", "onde", "qual", "quais", "porque", "pois", "entao", "tambem", "so",
    "apenas", "ainda", "agora", "hoje", "ontem", "cliente", "favor", "bom", "boa", "dia", "tarde",
    "noite", "obrigado", "obrigada", "ola", "oi", "the", "and", "of", "to", "is", "in", "on", "for",
}


def anonymize(text: str | None) -> str | None:
    """Mascara dados pessoais (e-mail, CPF, CNPJ, cartão, telefone) antes de gravar o ticket."""
    if not text:
        return text
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize(text: str | None) -> str:
    """Minúsculas, sem acento e sem espaços nas pontas (para comparar nomes e cabeçalhos)."""
    return _strip_accents((text or "").strip().lower())


def _stem(token: str) -> str:
    """Radicalização mínima de plural em português (impressoras -> impressora, conexoes -> conexao)."""
    if len(token) <= 4 or token.isdigit():
        return token
    for suffix, repl in (("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el"), ("ns", "m")):
        if token.endswith(suffix):
            return token[: -len(suffix)] + repl
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def keywords(*texts: str | None, limit: int = 200) -> list[str]:
    """Palavras-chave normalizadas (minúsculas, sem acento, sem stopwords), sem repetição e na ordem em que aparecem."""
    seen: dict[str, None] = {}
    for text in texts:
        if not text:
            continue
        for token in re.findall(r"[a-z0-9]+", _strip_accents(text.lower())):
            if len(token) < 2 or token in STOPWORDS:
                continue
            seen.setdefault(_stem(token), None)
            if len(seen) >= limit:
                return list(seen)
    return list(seen)
