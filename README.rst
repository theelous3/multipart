sansio_multipart: A sansio parser for multipart/form-data
=========================================================

This lib provides a parser for the multipart/form-data format. You feed it bytes, and it will feed you events and data objects.

Requirements
------------

Python 3.x


Examples
--------

Here's the setup for our examples.

Note: Our multipart body is split up in to *many* pieces, in order to make it as difficult to parse as possible. Only on the fourth chunk can we actually produce a ``Part`` object. This is just to illustrate a worst case scenario stream :D

.. code:: python

    from sansio_multipart import MultipartParser, NEED_DATA
    from collections import deque

    # boundary extracted from request's content-type header
    boundary = '8banana133744910kmmr13a56!102!2405'.encode()

    # chunks of data, as though they were read from a stream
    chunks = deque(
        [
            b'--8banana133744910kmmr',
            b'13a56!102!2405\r\nContent-Disposition: form-da',
            b'ta; name="file_1"; filename="test_file1.tx',
            b't"\r\nContent-Type: application/octet-strea',
            b'm\r\ncontent-length: 9\r\n\r\nCompoo',
            b'per\r\n--8banana',
            b'133744910kmmr13a5',
            b'6!102!2405--\r\n'
        ]
    )




There are a couple of ways to interact with the parser. Likely the most familiar way is to request events one by one. We'll pretend our deque is a socket, for illustrative purposes.

You'll be familiar with this pattern if you've used other sansio python libs, like h11.

.. code:: python

    with MultipartParser(boundary) as parser:
        while len(chunks):
            event = parser.next_event()
            if event is NEED_DATA:
                parser.recv(chunks.popleft())
                continue
            print(event)

    # Outputs

    # <sansio_multipart.parser.Part object at 0xb707d84c>
    # PartData(raw=bytearray(b'Compooper'), size=9)


That isn't the only way to handle things. The following is probably the simplest way to interact with a similar stream. Enter the parser, throw data at it, and read events.

.. code:: python

    with MultipartParser(boundary) as parser:
        for chunk in chunks:
            events = parser.parse(chunk)
            print("Chunk events ->", events)

    # Outputs:
    # Chunk events: -> [<Events.NEED_DATA: 1>]
    # Chunk events: -> [<Events.NEED_DATA: 1>]
    # Chunk events: -> [<Events.NEED_DATA: 1>]
    # Chunk events: -> [<Events.NEED_DATA: 1>]
    # Chunk events: -> [<sansio_multipart.parser.Part object at 0xb7048a4c>, <Events.NEED_DATA: 1>]
    # Chunk events: -> [PartData(raw=bytearray(b'Compooper'), size=9), <Events.NEED_DATA: 1>]
    # Chunk events: -> [<Events.NEED_DATA: 1>]
    # Chunk events: -> [<Events.FINISHED: 2>]
    """

Of course, you can just feed the entirity in. This time we'll have the full body data available, and two parts in our multipart (because it's multipart!).

.. code:: python

    full_data = b'--8banana133744910kmmr13a56!102!1823\r\nContent-Disposition: form-data; name="file_1"; filename="test_file1.txt"; Content-Type: application/octet-stream\r\n\r\nCompooper\r\n--8banana133744910kmmr13a56!102!1823\r\nContent-Disposition: form-data; name="data_1"\r\n\r\nwatwatwatwat=yesyesyes\r\n--8banana133744910kmmr13a56!102!1823--\r\n'

    boundary = '8banana133744910kmmr13a56!102!1823'

    with MultipartParser(boundary) as parser:
        print(parser.parse(full_data))

    # Outputs
    # [
    #     <sansio_multipart.parser.Part object at 0xb707d7ac>,
    #     PartData(raw=bytearray(b'Compooper'), size=9),
    #     <sansio_multipart.parser.Part object at 0xb707d7ec>,
    #     PartData(raw=bytearray(b'watwatwatwat=yesyesyes'), size=22),
    #     <Events.FINISHED: 2>
    # ]


You can buffer a ``PartData`` object to a ``Part`` object by passing it to the ``Part.buffer`` method, like ``part.buffer(part_data)``.

That's all there is to it!

Event reference:

* ``NEED_DATA`` Given when there isn't enough data to continue giving other events or data objects.

* ``FINISHED`` Given when the data has been successfully exhausted.

Data object reference:

* ``Part`` The object representing the head of a multipart part.

* ``PartData`` The object representing the body, or a segment of the body, of a multipart part. For any given part, you may have ``1..n`` data objects.

Error reference:

* ``UnexpectedExit`` Raised when you leave the context manager of the parser before a terminator line was parsed. Inherits from ``MultipartError``, ``EOFError``.

* ``MalformedData`` Raised in cases where the data is out of spec for the multipart protocol, and cannot be parsed. Inherits from ``MultipartError``.


Limitations
-----------

* Only parses ``multipart/form-data`` as seen from actual browsers.

  * Not suitable as a general purpose multipart parser (e.g. for multipart emails).
  * No ``multipart/mixed`` support (RFC 2388, deprecated in RFC 7578)
  * No ``encoded-word`` encoding (RFC 2047).
  * No ``base64`` or ``quoted-printable`` transfer encoding.

* Part headers are expected to be encoded in the charset given to the ``Part``/``MultipartParser`` constructor.
  [For operability considerations, see RFC 7578, section 5.1.]


Changelog
---------

* **0.3** Complete api change. The parser is now a sansio parser, meaning no io happens internally. This makes it safe for use in projects that don't like uncontrolled io happening (like async).

* **0.2**
  * Dropped support for Python versions below 3.6. Stay on 0.1 if you need Python 2.5+ support.

* **0.1 (21.06.2010)**
  * First release
