from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse


ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
VOID_TAGS = {"br"}
ALLOWED_ATTRS = {
    "a": {"href", "title", "target", "rel"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "*": {"style"},
}
ALLOWED_STYLES = {
    "background-color",
    "color",
    "font-style",
    "font-weight",
    "text-align",
    "text-decoration",
}
SAFE_URL_SCHEMES = {"http", "https", "mailto"}


def is_safe_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in SAFE_URL_SCHEMES or not parsed.scheme


def sanitize_style(value: str) -> str:
    clean_parts: list[str] = []
    for declaration in value.split(";"):
        if ":" not in declaration:
            continue
        name, raw_value = declaration.split(":", 1)
        name = name.strip().lower()
        raw_value = raw_value.strip()
        if name not in ALLOWED_STYLES or "url(" in raw_value.lower() or "expression(" in raw_value.lower():
            continue
        clean_parts.append(f"{name}: {raw_value}")
    return "; ".join(clean_parts)


class SanitizingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed"}:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS:
            return

        clean_attrs: list[str] = []
        allowed = ALLOWED_ATTRS.get(tag, set()) | ALLOWED_ATTRS["*"]
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            value = raw_value or ""
            if name.startswith("on") or name not in allowed:
                continue
            if name == "href" and not is_safe_url(value):
                continue
            if name == "style":
                value = sanitize_style(value)
                if not value:
                    continue
            if name == "target" and value not in {"_blank", "_self"}:
                continue
            clean_attrs.append(f'{name}="{escape(value, quote=True)}"')

        if tag == "a" and any(attr.startswith("target=\"_blank\"") for attr in clean_attrs):
            clean_attrs = [attr for attr in clean_attrs if not attr.startswith("rel=")]
            clean_attrs.append('rel="noopener noreferrer"')

        attr_text = f" {' '.join(clean_attrs)}" if clean_attrs else ""
        self.parts.append(f"<{tag}{attr_text}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(escape(data))

    def handle_entityref(self, name: str) -> None:
        if not self.skip_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.skip_depth:
            self.parts.append(f"&#{name};")


def sanitize_html(html: str | None) -> str:
    if not html:
        return ""
    parser = SanitizingParser()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts).strip()
