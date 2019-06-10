__all__ = [
    "header_quote",
    "header_unquote",
    "parse_options_header",
    "to_bytes",
    "MultiDict",
]


import re
from collections.abc import MutableMapping as DictMixin


_special = re.escape('()<>@,;:"\\/[]?={} \t')
_re_special = re.compile(r"[%s]" % _special)
_quoted_string = r'"(?:\\.|[^"])*"'  # Quoted string
_value = r"(?:[^%s]+|%s)" % (_special, _quoted_string)  # Save or quoted string
_option = r"(?:;|^)\s*([^%s]+)\s*=\s*(%s)" % (_special, _value)
_re_option = re.compile(_option)  # key=value part of an Content-Type like header


def header_quote(val):
    if not _re_special.search(val):
        return val

    return '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"'


def header_unquote(val, filename=False):
    if val[0] == val[-1] == '"':
        val = val[1:-1]

        if val[1:3] == ":\\" or val[:2] == "\\\\":
            val = val.split("\\")[-1]  # fix ie6 bug: full path --> filename

        return val.replace("\\\\", "\\").replace('\\"', '"')

    return val


def parse_options_header(header, options=None):
    if ";" not in header:
        return header.lower().strip(), {}

    content_type, tail = header.split(";", 1)
    options = options or {}

    for match in _re_option.finditer(tail):
        key = match.group(1).lower()
        value = header_unquote(match.group(2), key == "filename")
        options[key] = value

    return content_type, options


def to_bytes(data, encoding="utf8"):
    if isinstance(data, str):
        data = data.encode(encoding)

    return data


class MultiDict(DictMixin):
    """
    A dict that remembers old values for each key.
    HTTP headers may repeat with differing values,
    such as Set-Cookie. We need to remember all
    values.
    """

    def __init__(self, *args, **kwargs):
        self.dict = dict()
        for k, v in dict(*args, **kwargs).items():
            self[k] = v

    def __len__(self):
        return len(self.dict)

    def __iter__(self):
        return iter(self.dict)

    def __contains__(self, key):
        return key in self.dict

    def __delitem__(self, key):
        del self.dict[key]

    def keys(self):
        return self.dict.keys()

    def __getitem__(self, key):
        return self.get(key, KeyError, -1)

    def __setitem__(self, key, value):
        self.append(key, value)

    def append(self, key, value):
        self.dict.setdefault(key, []).append(value)

    def replace(self, key, value):
        self.dict[key] = [value]

    def getall(self, key):
        return self.dict.get(key) or []

    def get(self, key, default=None, index=-1):
        if key not in self.dict and default != KeyError:
            return [default][index]

        return self.dict[key][index]

    def iterallitems(self):
        for key, values in self.dict.items():
            for value in values:
                yield key, value
