class MultipartError(Exception):
    ...


class UnexpectedExit(MultipartError, EOFError):
    ...


class MalformedData(MultipartError):
    ...
