__all__ = ["MultipartParser", "Part", "PartData", "Events"]


from dataclasses import dataclass
from itertools import chain
from enum import Enum, auto
from collections import deque

from wsgiref.headers import Headers

from typing import Union, Generator, List, Tuple

from .utils import to_bytes, parse_options_header
from .errors import UnexpectedExit, MalformedData


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
    size: int


class Part:
    def __init__(self, charset="latin1"):
        self.headerlist = []
        self.headers = None
        self.data = bytearray()
        self.size = 0
        self.disposition = None
        self.name = None
        self.filename = None
        self.content_type = None
        self.charset = charset

    @property
    def value(self) -> str:
        """ Data decoded with the specified charset """
        return self.data.decode(self.charset)

    @property
    def raw(self) -> bytearray:
        """ Data without decoding """
        return self.data

    def buffer(self, part_data) -> None:
        self.data += part_data.raw
        self.size += part_data.size


class MultipartParser:
    def __init__(self, boundary, content_length=None, charset="latin1"):
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
            raise UnexpectedExit("Unexpected end. No terminator line parsed.")

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
                raise MalformedData("Part does not start with boundary")

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
                raise MalformedData("Content-Disposition header is missing.")

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
            raise MalformedData("Syntax error in header: No colon.")

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
                self._buffer_chunk([(line, newline)])
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
            if not self.state in (States.BUILDING_HEADERS, States.FINISHED):
                # we haven't hit an end condition for the current part.
                self.state = States.BUILDING_BODY_NEED_DATA
            return PartData(raw=part_data_buffer, size=len(part_data_buffer))

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
                raise MalformedData("Size of part body exceeds part Content-Length.")
