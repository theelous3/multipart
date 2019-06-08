# -*- coding: utf-8 -*-
"""
Parser for multipart/form-data
==============================

This module provides a parser for the multipart/form-data format. It can read
from a file, a socket or a WSGI environment. The parser can be used to replace
cgi.FieldStorage (without the bugs) and works with Python 2.5+ and 3.x (2to3).
"""

__author__ = "Marcel Hellkamp, Mark Jameson"
__version__ = "0.2"
__license__ = "MIT"
__all__ = [
    "MultipartError",
    "MultipartParser",
    "Part",
    "PartData",
    "Events",
    "parse_form_data",
]


import re
import sys
from itertools import chain
from enum import Enum, auto
from dataclasses import dataclass
from collections import deque
from collections import MutableMapping as DictMixin
from urllib.parse import parse_qs
from wsgiref.headers import Headers

from typing import Union, Generator, List, Tuple

##############################################################################
################################ Helper & Misc ###############################
##############################################################################
# Some of these were copied from bottle: http://bottle.paws.de/


# ---------
# MultiDict
# ---------


class MultiDict(DictMixin):
    """ A dict that remembers old values for each key.
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


def to_bytes(data, encoding="utf8"):
    if isinstance(data, str):
        data = data.encode(encoding)

    return data


# -------------
# Header Parser
# -------------


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


##############################################################################
################################## Multipart #################################
##############################################################################


class MultipartError(ValueError):
    pass


class Events(Enum):
    NEED_DATA = auto()
    FINISHED = auto()


class States(Enum):
    BUILDING_HEADERS = auto()
    BUILDING_HEADERS_NEED_DATA = auto()
    BUILDING_BODY = auto()
    BUILDING_BODY_NEED_DATA = auto()
    FINISHED = auto()
    ERROR = auto()


@dataclass
class PartData:
    raw: bytearray
    length: int


class Part:
    def __init__(self, charset="latin1"):
        self.headerlist = []
        self.headers = None
        self.data = None
        self.file = False
        self.form_data = False
        self.size = 0
        self.disposition = None
        self.name = None
        self.filename = None
        self.content_type = None
        self.charset = charset

    def is_buffered(self) -> bool:
        """ Return true if the data is fully buffered in memory."""
        return isinstance(self.data, BytesIO)

    @property
    def value(self) -> str:
        """ Data decoded with the specified charset """
        return self.raw.decode(self.charset)

    @property
    def raw(self) -> bytearray:
        """ Data without decoding """
        return self.data


class MultipartParser:
    def __init__(self, boundary, content_length=None, charset="latin1"):
        """
        Parse a multipart/form-data byte stream. This object is an iterator
        over the parts of the message.
        """
        self.boundary = boundary
        self.separator = b"--" + to_bytes(self.boundary)
        self.terminator = b"--" + to_bytes(self.boundary) + b"--"

        self.separator_len = len(self.separator)

        self.charset = charset

        self.state = States.BUILDING_HEADERS
        self.events_queue = deque()

        self.buffer = bytearray()
        self.last_partial_line = None

        self.current_part = None

        self.expected_part_size = None
        self.current_part_size = 0

    def parts(self) -> List[Union[Part, PartData, Events]]:
        return list(self)

    def recv(self, chunk) -> None:
        """
        Queue any events parsing chunk may create.
        """
        self._queue_events(chunk)

    def parse(self, chunk) -> List[Union[Part, PartData, Events]]:
        """Queue events for the chunk, and return them as a list."""
        self._queue_events(chunk)
        return self.parts()

    def next_event(self) -> Union[Part, PartData, Events]:
        """
        Return the next event from the queue.
        If there is no event, request data, unless parsing is complete.
        """
        try:
            return self.events_queue.popleft()
        except IndexError:
            if self.state is not States.FINISHED:
                return Events.NEED_DATA
            else:
                return Events.FINISHED

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        if self.state is not States.FINISHED:
            raise MultipartError("Unexpected end. No terminator line parsed.")

    def __iter__(self) -> Generator[Union[Part, PartData, Events], None, None]:
        """
        Yield all events in the queue.
        """
        while True:
            try:
                event = self.events_queue.popleft()
            except IndexError:
                break
            else:
                yield event

    def _queue_events(self, chunk) -> None:
        """
        Send the given chunk through the parser based on the current  parser
        state, and add any events that result to the events queue.
        """
        # Prepend the current buffer if there is any and clear the buffer.
        # Carries partial chunks from one chunk parsing to the next.
        if self.state is States.ERROR:
            raise RuntimeError("Cannot use parser in ERROR state.")

        if self.buffer:
            chunk = self.buffer + chunk
            self.buffer = bytearray()
        chunk = iter(chunk.splitlines(True))

        while True:
            try:
                # Prepend the buffer between state changes, to carry
                # separators and terminations between parsing routes.
                if self.buffer:
                    split_buffer = iter(self.buffer.splitlines(True))
                    chunk = chain(split_buffer, chunk)
                    self.buffer = bytearray()

                # Depending on the parser's current state, attempt to
                # either build and queue a Part / PartData object, or
                # queue actionable events.
                if self.state is States.BUILDING_HEADERS:
                    maybe_part = self._parse_part(chunk)
                    if maybe_part:
                        self.events_queue.append(maybe_part)

                elif self.state is States.BUILDING_BODY:
                    maybe_part_data = self._build_part_data(chunk)
                    if maybe_part_data:
                        self.events_queue.append(maybe_part_data)

                # queue events based on parser state post parse attempt
                if self.state is States.BUILDING_HEADERS_NEED_DATA:
                    self.events_queue.append(Events.NEED_DATA)
                    self.state = States.BUILDING_HEADERS
                    break

                elif self.state is States.BUILDING_BODY_NEED_DATA:
                    self.events_queue.append(Events.NEED_DATA)
                    self.state = States.BUILDING_BODY
                    break

                elif self.state is States.FINISHED:
                    self.events_queue.append(Events.FINISHED)
                    break
            except Exception:
                self.state = States.ERROR
                raise

    def _parse_part(self, chunk_lines) -> Union[Part, None]:
        """
        Try to construct and return a Part object, given sufficient
        data in the chunk. If too little data is given, the given data
        will be buffered, and a fresh attempt to create the Part will
        be made upon future request.
        """
        lines = self._separate_newlines(chunk_lines)

        # Consume first boundary. Ignore leading blank lines
        maybe_seperator, first_newline = next(
            ((l, nl) for l, nl in lines if l), (b"", b"")
        )

        if not first_newline:
            # We have not recieved a full line of headers.
            self._buffer_chunk([(maybe_seperator, first_newline)])
            self.state = States.BUILDING_HEADERS_NEED_DATA
            return

        if maybe_seperator != self.separator:
            if len(maybe_seperator) >= self.separator_len:
                raise MultipartError("Part does not start with boundary")

        # Buffer the beginning of the part in case we need to reattempt
        # creation later (incomplete data).
        self._buffer_chunk([(maybe_seperator, first_newline)])

        self.current_part = self.current_part or Part(charset=self.charset)
        # alias the part so we can return it later and not wipe it
        # with state change
        part = self.current_part

        for line, newline in lines:
            self._construct_part(part, line, newline)

            if self.state is States.BUILDING_HEADERS_NEED_DATA:
                self._buffer_chunk([(line, newline)])
                self._buffer_chunk(lines)
                break

            elif self.state is States.BUILDING_BODY:
                self.buffer = bytearray()
                self._buffer_chunk(lines)
                return part
                break
        else:
            # We have iterated the given data to completion, but have not
            # recieved enough data to build the Part.
            self.state = States.BUILDING_HEADERS_NEED_DATA

    def _construct_part(self, part, line, newline) -> None:
        """
        Add headers to the Part as they are parsed.
        """
        line = line.decode(self.charset)

        if not newline:
            self.state = States.BUILDING_HEADERS_NEED_DATA
            return

        if not line.strip():
            # blank line -> end of header segment
            part.headers = Headers(part.headerlist)
            content_disposition = part.headers.get("Content-Disposition", "")
            content_type = part.headers.get("Content-Type", "")

            if not content_disposition:
                raise MultipartError("Content-Disposition header is missing.")

            part.disposition, part.options = parse_options_header(content_disposition)
            part.name = part.options.get("name")
            part.filename = part.options.get("filename")
            part.content_type, options = parse_options_header(content_type)
            part.charset = options.get("charset") or part.charset

            content_length = part.headers.get("Content-Length")
            if content_length is not None:
                self.expected_part_size = int(content_length)

            self.current_part = None
            self.state = States.BUILDING_BODY
            return

        if ":" not in line:
            raise MultipartError("Syntax error in header: No colon.")

        name, value = line.split(":", 1)
        part.headerlist.append((name.strip(), value.strip()))

    def _build_part_data(self, chunk_lines) -> Union[PartData, None]:
        """
        Parse through the chunk, creating and returning PartData objects when possible.
        """
        part_data_buffer = bytearray()

        lines = self._separate_newlines(chunk_lines)

        previous_newline = b""

        for line, newline in lines:

            # Handle the case where our last chunk of data ended
            # ambigiously.
            if self.last_partial_line is not None:
                line = self.last_partial_line + line
                self.last_partial_line = None

            if line == self.terminator:
                self.state = States.FINISHED
                self.current_part_size = 0
                self.expected_part_size = None
                break

            elif line == self.separator:
                self._buffer_chunk(lines)
                self.state = States.BUILDING_HEADERS
                self.current_part_size = 0
                self.expected_part_size = None
                break
            else:
                if not newline:
                    # It is impossible to tell the difference between
                    # body data + CRLF + the beginning of the next seperator or
                    # terminator, and random body data. For example, with a terminator
                    # of "--terminator", we can't make a hard decision when our body chunk
                    # is "somedata\r\n--term". We need to hold off on taking any specific
                    # action, instead requesting more data.
                    # We only make a distinction here when there is a possibility
                    # that the chunk could actually be a separator or terminator,
                    # which is when the line is shorter than the sep / term.
                    # Anything else would either pass/fail the sep/term check, or
                    # could not be a sep/term.
                    if len(line) < self.separator_len:
                        self.last_partial_line = line
                        self.state = States.BUILDING_BODY_NEED_DATA
                else:
                    self._regulate_content_length(len(line))
                    part_data_buffer += previous_newline + line
                    previous_newline = newline

        if part_data_buffer:
            self.state = States.BUILDING_BODY_NEED_DATA
            return PartData(raw=part_data_buffer, length=len(part_data_buffer))

    def _separate_newlines(self, lines) -> Generator[Tuple[bytes, bytes], None, None]:
        """
        Iterate over a binary file-like object line by line. Each line is
        returned as a (line, line_ending) tuple.
        """
        for line in lines:
            if line.endswith(b"\r\n"):
                yield line[:-2], b"\r\n"
            elif line.endswith(b"\n"):
                yield line[:-1], b"\n"
            elif line.endswith(b"\r"):
                yield line[:-1], b"\r"
            else:
                yield line or b"", b""

    def _buffer_chunk(self, chunk_lines) -> None:
        """
        Take an iterator like Iterable[Tuple[bytes, bytes]] and store them.
        For convenience's sake, we join everything back up as a singular byte string
        for reparsing later.
        """
        self.buffer += b"".join((l + nl) for l, nl in chunk_lines)

    def _regulate_content_length(self, line_size) -> None:
        if self.expected_part_size is not None:
            self.current_part_size += line_size
            if self.current_part_size > self.expected_part_size:
                raise MultipartError("Size of part body exceeds part Content-Length.")


# utils


# TODO add data type assignments to parts
def assign_data_type(part: Part) -> None:
    if part.filename is not None:
        part.file = True
    else:
        part.form_data = True


##############################################################################
#################################### WSGI ####################################
##############################################################################


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
