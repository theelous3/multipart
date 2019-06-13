__all__ = ["parse_form_data"]


from io import BytesIO
from urllib.parse import parse_qs

from .parser import MultipartParser
from .utils import MultiDict, parse_options_header
from .errors import MultipartError


def parse_form_data(environ, charset="utf8", strict=False, **kwargs):
    """ Parse form data from an environ dict and return a (forms, files) tuple.
        Both tuple values are dictionaries with the form-field name as a key
        (unicode) and lists as values (multiple values per key are possible).
        The forms-dictionary contains form-field values as unicode strings.
        The files-dictionary contains :class:`Part` instances, either
        because the form-field was a file-upload or the value is too big to fit
        into memory limits.

        :param environ: An WSGI environment dict.
        :param charset: The charset to use if unsure. (default: utf8)
        :param strict: If True, raise :exc:`MultipartError` on any parsing
                       errors. These are silently ignored by default.
    """

    forms, files = MultiDict(), MultiDict()

    try:
        if environ.get("REQUEST_METHOD", "GET").upper() not in ("POST", "PUT"):
            raise MultipartError("Request method other than POST or PUT.")
        content_length = int(environ.get("CONTENT_LENGTH", "-1"))
        content_type = environ.get("CONTENT_TYPE", "")

        if not content_type:
            raise MultipartError("Missing Content-Type header.")

        content_type, options = parse_options_header(content_type)
        stream = environ.get("wsgi.input") or BytesIO()
        kwargs["charset"] = charset = options.get("charset", charset)

        if content_type == "multipart/form-data":
            boundary = options.get("boundary", "")

            if not boundary:
                raise MultipartError("No boundary for multipart/form-data.")

            for part in MultipartParser(stream, boundary, content_length, **kwargs):
                if part.filename or not part.is_buffered():
                    files[part.name] = part
                else:  # TODO: Big form-fields are in the files dict. really?
                    forms[part.name] = part.value

        elif content_type in (
            "application/x-www-form-urlencoded",
            "application/x-url-encoded",
        ):
            mem_limit = kwargs.get("mem_limit", 2 ** 20)
            if content_length > mem_limit:
                raise MultipartError("Request too big. Increase MAXMEM.")

            data = stream.read(mem_limit).decode(charset)

            if stream.read(1):  # These is more that does not fit mem_limit
                raise MultipartError("Request too big. Increase MAXMEM.")

            data = parse_qs(data, keep_blank_values=True)

            for key, values in data.items():
                for value in values:
                    forms[key] = value
        else:
            raise MultipartError("Unsupported content type.")

    except MultipartError:
        if strict:
            raise

    return forms, files
