from __future__ import annotations

from urllib.parse import urlparse


def parse_next_link(header_value: str | None) -> str | None:
    if not header_value:
        return None
    for part in header_value.split(","):
        pieces = [piece.strip() for piece in part.split(";")]
        if not pieces or not pieces[0].startswith("<") or not pieces[0].endswith(">"):
            continue
        rel_values = {
            piece[5:-1] for piece in pieces[1:] if piece.startswith('rel="') and piece.endswith('"')
        }
        if "next" in rel_values:
            return pieces[0][1:-1]
    return None


def validate_next_url(next_url: str, *, base_url: str) -> str:
    parsed_next = urlparse(next_url)
    parsed_base = urlparse(base_url)
    if parsed_next.scheme != "https":
        raise ValueError("GitHub pagination next URL must use https")
    if parsed_next.username or parsed_next.password:
        raise ValueError("GitHub pagination next URL must not include credentials")
    base_port = parsed_base.port or (443 if parsed_base.scheme == "https" else None)
    next_port = parsed_next.port or (443 if parsed_next.scheme == "https" else None)
    if parsed_next.hostname != parsed_base.hostname or next_port != base_port:
        raise ValueError("GitHub pagination next URL points to a different host")
    return next_url
