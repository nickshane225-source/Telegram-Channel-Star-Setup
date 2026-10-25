import html
from html.parser import HTMLParser
from typing import List, Tuple
from markupsafe import Markup

# Tags supported by Telegram Bot API HTML parse mode
ALLOWED_TAGS = {
    "b", "strong",
    "i", "em",
    "u", "ins",
    "s", "strike", "del",
    "code",
    "pre",
    "a",
    "tg-spoiler",
    "blockquote",
}


class TelegramHTMLSanitizer(HTMLParser):
    """
    Sanitize HTML string to keep only tags and attributes allowed by Telegram Bot API.
    Disallowed tags are stripped (their inner text is preserved and escaped).
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.output: List[str] = []
        self.tag_stack: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]):
        tag_lower = tag.lower()
        if tag_lower in ALLOWED_TAGS:
            if tag_lower == "a":
                href = next((val for name, val in attrs if name.lower() == "href"), None)
                if href and (href.startswith("http://") or href.startswith("https://") or href.startswith("tg://")):
                    safe_href = html.escape(href, quote=True)
                    self.output.append(f'<a href="{safe_href}">')
                else:
                    self.output.append("<a>")
            else:
                self.output.append(f"<{tag_lower}>")
            self.tag_stack.append(tag_lower)

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower in ALLOWED_TAGS and tag_lower in self.tag_stack:
            # Close properly
            while self.tag_stack:
                popped = self.tag_stack.pop()
                self.output.append(f"</{popped}>")
                if popped == tag_lower:
                    break

    def handle_data(self, data: str):
        # Escape raw &, <, > in data while keeping quotes unescaped in text
        self.output.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str):
        self.output.append(f"&{name};")

    def handle_charref(self, name: str):
        self.output.append(f"&#{name};")

    def close(self):
        super().close()
        # Close any unclosed tags
        while self.tag_stack:
            popped = self.tag_stack.pop()
            self.output.append(f"</{popped}>")

    def get_result(self) -> str:
        return "".join(self.output)


def sanitize_telegram_html(raw_html: str) -> str:
    """
    Sanitize input text/HTML ensuring only Telegram-supported tags remain,
    malformed markup is fixed, and dangerous elements (e.g. <script>) are removed.
    """
    if not raw_html:
        return ""
    sanitizer = TelegramHTMLSanitizer()
    sanitizer.feed(raw_html)
    sanitizer.close()
    return sanitizer.get_result()


def sanitize_telegram_html_markup(raw_html: str) -> Markup:
    """Return sanitized HTML as a markupsafe.Markup object for safe template rendering."""
    return Markup(sanitize_telegram_html(raw_html))
