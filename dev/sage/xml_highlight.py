"""Basic XML syntax highlighting for the XML editor tab."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument


def _fmt(color: str, *, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class XmlHighlighter(QSyntaxHighlighter):
    """Highlight tags, attribute keys/values, text, comments, and punctuation."""

    # Block states for multi-line constructs
    _STATE_NONE = 0
    _STATE_COMMENT = 1

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        # VS Code Dark+ token colors (HTML/XML scopes)
        self._comment = _fmt("#6A9955")  # comment
        self._punct = _fmt("#808080")  # punctuation.definition.tag  < > /
        self._tag = _fmt("#569CD6")  # entity.name.tag
        self._attr = _fmt("#9CDCFE")  # entity.other.attribute-name
        self._value = _fmt("#CE9178")  # string
        self._text = _fmt("#D4D4D4")  # editor.foreground (character data)
        self._entity = _fmt("#569CD6")  # constant.character.entity
        self._equals = _fmt("#D4D4D4")  # = (default foreground)

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        # Default: treat whole block as text; override as we scan.
        if text:
            self.setFormat(0, len(text), self._text)

        i = 0
        n = len(text)
        in_comment = self.previousBlockState() == self._STATE_COMMENT
        self.setCurrentBlockState(self._STATE_NONE)

        while i < n:
            if in_comment:
                end = text.find("-->", i)
                if end < 0:
                    self.setFormat(i, n - i, self._comment)
                    self.setCurrentBlockState(self._STATE_COMMENT)
                    return
                self.setFormat(i, end + 3 - i, self._comment)
                i = end + 3
                in_comment = False
                continue

            if text.startswith("<!--", i):
                end = text.find("-->", i + 4)
                if end < 0:
                    self.setFormat(i, n - i, self._comment)
                    self.setCurrentBlockState(self._STATE_COMMENT)
                    return
                self.setFormat(i, end + 3 - i, self._comment)
                i = end + 3
                continue

            # Entity reference outside tags
            if text[i] == "&":
                semi = text.find(";", i + 1)
                if semi > i and semi - i < 12 and text[i + 1 : semi].replace("#", "").isalnum():
                    self.setFormat(i, semi + 1 - i, self._entity)
                    i = semi + 1
                    continue

            if text[i] == "<":
                # <?xml …?> or <!DOCTYPE …> - treat like a tag for now
                close = text.find(">", i + 1)
                if close < 0:
                    self._highlight_tag_fragment(text, i, n)
                    return
                self._highlight_tag_fragment(text, i, close + 1)
                i = close + 1
                continue

            i += 1

    def _highlight_tag_fragment(self, text: str, start: int, end: int) -> None:
        """Color < / tagName attr="value" /> inside [start, end)."""
        i = start
        if i < end and text[i] == "<":
            self.setFormat(i, 1, self._punct)
            i += 1
            if i < end and text[i] in "/?!":
                self.setFormat(i, 1, self._punct)
                i += 1

        # After < or </ - read tag name
        while i < end and text[i].isspace():
            i += 1
        name_start = i
        while i < end and (text[i].isalnum() or text[i] in "_-:.?"):
            i += 1
        if i > name_start:
            self.setFormat(name_start, i - name_start, self._tag)

        while i < end:
            ch = text[i]
            if ch in ">/?":
                self.setFormat(i, 1, self._punct)
                i += 1
                continue
            if ch.isspace():
                i += 1
                continue
            if ch == "=":
                self.setFormat(i, 1, self._equals)
                i += 1
                continue
            if ch in "'\"":
                quote = ch
                j = i + 1
                while j < end and text[j] != quote:
                    j += 1
                if j < end:
                    j += 1
                self.setFormat(i, j - i, self._value)
                i = j
                continue
            # Attribute name
            attr_start = i
            while i < end and (text[i].isalnum() or text[i] in "_-:"):
                i += 1
            if i > attr_start:
                self.setFormat(attr_start, i - attr_start, self._attr)
            else:
                i += 1
