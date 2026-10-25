"""Common presentation of verified facts. Never rewrites or drops facts."""
def sentences(value: str) -> list[str]:
    output, start, quoted = [], 0, False
    for index, char in enumerate(value):
        if char == '“':
            quoted = True
        elif char == '”':
            quoted = False
        elif char in '。；' and not quoted:
            output.append(value[start:index+1].strip())
            start = index+1
    if value[start:].strip():
        output.append(value[start:].strip())
    return output


def format_evidence(text: str, layout: str = 'sections') -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text
    title, *facts = lines
    output = [title]
    for fact in facts:
        # These are server-rendered evidence sentences, not intent extraction.
        # Break long metric paragraphs at existing punctuation, retain every atom.
        parts = sentences(fact) if len(fact) > 180 else [fact]
        for part in parts:
            output.append(part if part.startswith(('- ', '|', '### ', '> ')) else '- ' + part)
    separator = '\n' if layout == 'list' else '\n\n'
    return separator.join(output)
